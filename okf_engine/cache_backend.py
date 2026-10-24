"""Backend dla KnowledgeService; jawna konstrukcja, bez bootstrap main.py.

SQLite należy do CacheWorker. TaskManager oczekuje na wynik i przekazuje
anulowanie przez clock. Inventory jest jeszcze istniejącym ograniczonym
skanem; dokumenty przetwarzamy partiami. Nie kasujemy niewidzianych wpisów DB.
"""
import json
import threading
import time
from collections import Counter
from dataclasses import dataclass, field, replace

from .cache_store import CacheStore, CacheError, fingerprint, record_from_public
from .cache_hit import cache_hit, record_tag
from .cache_worker import CacheWorker
from .cache_restore import restore_cached_document
from .documents import DocumentError, same_binding
from .paths import find_case_collisions
from .scanner import ScanResult, _inventory


@dataclass
class _Job:
    reader: object = field(repr=False)
    bundle: str
    limits: object
    clock: object = field(repr=False)
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    result: object = field(default=None, repr=False)
    error: str = None
    state: object = field(default=None, repr=False)
    binding: str = field(default=None, repr=False)
    versions: str = None
    pending: list = field(default_factory=list, repr=False)
    offset: int = 0
    documents: list = field(default_factory=list, repr=False)
    reasons: set = field(default_factory=set)
    skipped: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)
    error_stages: Counter = field(default_factory=Counter)
    restore_only: bool = False
    after: object = None
    changes: object = field(default=None, repr=False)


class CacheRefreshBackend:
    def __init__(self, path, key, *, batch_size=10):
        if not isinstance(key, bytes) or len(key) != 32:
            raise CacheError('CACHE_FINGERPRINT_INVALID')
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 100:
            raise CacheError('CACHE_BATCH_INVALID')
        self._key, self._batch_size = key, batch_size
        self._lock = threading.Lock()
        self._jobs = {}
        self._sequence = 0
        self._worker = CacheWorker(lambda: CacheStore(path), self._step,
                                   lambda store: store.close())
        self._worker.start()

    def close(self, timeout=5):
        return self._worker.close(timeout)

    def status(self):
        return self._worker.status()

    def restore(self, reader, bundle_id, limits, clock):
        return self._run_job(reader, bundle_id, limits, clock, restore_only=True)

    def run(self, reader, bundle_id, limits, clock):
        return self._run_job(reader, bundle_id, limits, clock)

    def update(self, reader, bundle_id, limits, clock, concepts):
        from .models import DocumentIdentity
        concepts = tuple(sorted(set(concepts)))
        if not 1 <= len(concepts) <= limits.max_documents_per_bundle:
            raise CacheError('CACHE_RECORD_LIMIT')
        for concept in concepts:
            DocumentIdentity(bundle_id, concept)
        return self._run_job(reader, bundle_id, limits, clock, changes=concepts)

    def _run_job(self, reader, bundle_id, limits, clock, restore_only=False, changes=None):
        job = _Job(reader, bundle_id, limits, clock, restore_only=restore_only, changes=changes)
        with self._lock:
            self._sequence += 1
            name = str(self._sequence)
            self._jobs[name] = job
        try:
            self._worker.submit(name)
            while not job.done.wait(0.05):
                try:
                    clock()  # sprawdzenie anulowania w wątku zadania
                except DocumentError:
                    raise
                except Exception:
                    # Ta sama bezpieczna odpowiedź co w _step, niezależnie
                    # od tego, który wątek pierwszy sprawdzi anulowanie.
                    raise DocumentError('CACHE_REFRESH_FAILED') from None
                state = self._worker.status()
                if state['stopping'] or not state['alive']:
                    raise CacheError('CACHE_WORKER_UNAVAILABLE')
            if job.error:
                raise DocumentError(job.error)
            return job.result
        finally:
            self._worker.cancel(name)
            with self._lock:
                self._jobs.pop(name, None)

    def _step(self, store, item, ticket_valid):
        with self._lock:
            job = self._jobs.get(item.key)
        if job is None:
            return True, None
        try:
            job.clock()
            if not ticket_valid():
                raise CacheError('CACHE_CANCELLED')
            reader = job.reader
            current = reader.current_bundle(job.bundle)
            if job.state is None:
                job.state = current
                entry = current.entry
                job.binding = json.dumps([entry.root_path, str(current.path), entry.root,
                                          entry.scope, entry.profile], ensure_ascii=False)
                # Cache zależy również od limitów parsera, nie tylko wersji kodu.
                job.versions = 'parser1-mask1:' + json.dumps(vars(job.limits.to_parse_limits()), sort_keys=True)
                if job.restore_only:
                    job.reasons.add('cache_unchecked')
                    return False, None
                if job.changes is not None:
                    job.pending = list(job.changes)
                    return False, None
                # Inventory ograniczone; niepełny wynik jest jawny i nie powoduje purge DB.
                candidates = _inventory(current.path, job.bundle, job.limits, job.clock,
                                        job.clock() + 90, job.reasons, job.skipped, current)
                collisions = {name for group in find_case_collisions(candidates) for name in group}
                job.skipped['case_collision'] += len(collisions)
                job.pending = sorted(name for name in candidates if name not in collisions)
                return False, None
            if not same_binding(job.state, current):
                raise DocumentError('BUNDLE_UNAVAILABLE')
            if job.restore_only:
                records, cursor = store.load_page(job.bundle, job.binding, job.versions,
                                                  after=job.after, limit=self._batch_size)
                for record in records:
                    job.clock()
                    if not ticket_valid():
                        raise CacheError('CACHE_CANCELLED')
                    try:
                        document = restore_cached_document(record, self._key, current,
                                                           binding=job.binding, versions=job.versions,
                                                           now=reader.now())
                        job.documents.append(document)
                    except CacheError:
                        job.errors['CACHE_RECORD_REJECTED'] += 1
                        job.reasons.add('cache_record_rejected')
                job.after = cursor
                if cursor is not None:
                    return False, None
                job.result = ScanResult(tuple(job.documents), True, tuple(sorted(job.reasons)),
                                        {}, dict(job.errors))
                job.done.set()
                return True, None
            end = min(len(job.pending), job.offset + self._batch_size)
            for concept in job.pending[job.offset:end]:
                job.clock()
                if not ticket_valid():
                    raise CacheError('CACHE_CANCELLED')
                stage = 'read_bytes'
                try:
                    source = reader.read_bytes(job.bundle, concept)
                    if not same_binding(job.state, source.state):
                        raise DocumentError('BUNDLE_UNAVAILABLE')
                    stage = 'cache_load'
                    cached = store.load(source.identity, job.binding, job.versions)
                    document = None
                    if cached is not None:
                        try:
                            stage = 'cache_hit'
                            document = cache_hit(reader, source, cached, cached.auth_tag, self._key,
                                                 binding=job.binding, versions=job.versions)
                        except CacheError:
                            # Nie podpisujemy rekordu z DB. Odtwarzamy go wyłącznie z pliku.
                            document = None
                    if document is None:
                        stage = 'build_document'
                        document = reader.build_document(source)
                        info = source.path.lstat()
                        snapshot = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                        record = record_from_public(document.view, job.binding, job.versions, snapshot,
                                                    fingerprint(source.raw, self._key), document.format)
                        record = replace(record, auth_tag=record_tag(record, self._key))
                        stage = 'cache_write_check'
                        reader.check_bytes(source)
                        job.clock()
                        if not ticket_valid():
                            raise CacheError('CACHE_CANCELLED')
                        store.apply([record])
                    job.documents.append(document)
                except DocumentError as exc:
                    if exc.code == 'BUNDLE_UNAVAILABLE':
                        raise
                    job.errors[exc.code] += 1
                    job.error_stages[stage + '.' + exc.code] += 1
                    job.reasons.add('document_unavailable')
            job.offset = end
            if end < len(job.pending):
                time.sleep(0)
                return False, None
            job.clock()
            if not ticket_valid():
                raise CacheError('CACHE_CANCELLED')
            job.result = ScanResult(tuple(job.documents), bool(job.reasons),
                                    tuple(sorted(job.reasons)), dict(job.skipped), dict(job.errors),
                                    dict(job.error_stages))
            job.done.set()
            return True, None
        except Exception as exc:
            job.error = exc.code if isinstance(exc, DocumentError) else 'CACHE_REFRESH_FAILED'
            job.done.set()
            return True, None
