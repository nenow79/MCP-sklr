"""Koordynator publicznych rekordów; jeszcze bez adaptera KnowledgeIndex/runtime.

resolve(key) -> (bundle_id, binding, versions); authorize(context) -> bool.
read(context, concept) -> CacheRecord lub None WYŁĄCZNIE dla potwierdzonego
usunięcia. Błąd odczytu musi zgłosić wyjątek, nie None.
publish(context, records, valid) musi atomowo sprawdzić valid i aktualny dostęp
przed podmianą RAM. Ten moduł sam nie przyznaje dostępu i nie tworzy rewizji API.
"""
import threading
from dataclasses import dataclass, field

from .cache_store import CacheStore, CacheRecord, CacheError
from .cache_worker import CacheWorker
from .models import DocumentIdentity


@dataclass
class _Run:
    context: tuple
    after: object = None
    records: dict = field(default_factory=dict, repr=False)
    changes: tuple = ()
    offset: int = 0
    loading: bool = True


class CachePipeline:
    def __init__(self, path, resolve, authorize, read, publish, *, batch_size=25, capacity=256):
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 100:
            raise CacheError('CACHE_BATCH_INVALID')
        self._resolve, self._authorize = resolve, authorize
        self._read, self._publish = read, publish
        self._batch_size = batch_size
        self._lock = threading.RLock()
        self._requests = {}
        self._worker = CacheWorker(lambda: CacheStore(path), self._step,
                                   lambda store: store.close(), capacity=capacity)

    def start(self):
        self._worker.start()

    def submit(self, key, changes=()):
        """changes jest zbiorem znanych ścieżek; pełną inventory dostarcza przyszły adapter."""
        changes = tuple(changes)
        if len(changes) > 5000:
            raise CacheError('CACHE_RECORD_LIMIT')
        for concept in changes:
            DocumentIdentity('validation', concept)
        with self._lock:
            previous = self._requests.get(key, ())
            merged = tuple(sorted(set(previous) | set(changes)))
            if len(merged) > 5000:
                raise CacheError('CACHE_RECORD_LIMIT')
            self._requests[key] = merged
            try:
                return self._worker.submit(key)
            except Exception:
                if previous:
                    self._requests[key] = previous
                else:
                    self._requests.pop(key, None)
                raise

    def cancel(self, key):
        with self._lock:
            self._requests.pop(key, None)
            return self._worker.cancel(key)

    def status(self):
        return self._worker.status()

    def close(self, timeout=5):
        return self._worker.close(timeout)

    def _step(self, store, item, ticket_valid):
        if item.cursor is None:
            context = self._resolve(item.key)
            if not isinstance(context, tuple) or len(context) != 3:
                raise CacheError('CACHE_CONTEXT_INVALID')
            with self._lock:
                run = _Run(context, changes=self._requests.get(item.key, ()))
        else:
            run = item.cursor

        def valid():
            return (ticket_valid() and self._resolve(item.key) == run.context
                    and self._authorize(run.context) is True)

        if not valid():
            return True, None
        bundle, binding, versions = run.context
        if run.loading:
            records, cursor = store.load_page(bundle, binding, versions,
                                              after=run.after, limit=self._batch_size)
            for record in records:
                run.records[record.identity.concept_id] = record
            run.after = cursor
            if cursor is None:
                run.loading = False
            return False, run
        if run.offset < len(run.changes):
            concepts = run.changes[run.offset:run.offset + self._batch_size]
            updates, deletes = [], []
            for concept in concepts:
                if not valid():
                    return True, None
                record = self._read(run.context, concept)
                identity = DocumentIdentity(bundle, concept)
                if record is None:
                    deletes.append(identity)
                elif (not isinstance(record, CacheRecord) or record.identity != identity
                      or record.binding != binding or record.versions != versions):
                    raise CacheError('CACHE_CONTEXT_INVALID')
                else:
                    updates.append(record)
            # Pod wspólnym lockiem submit nie może unieważnić ticket w trakcie commit.
            # Zewnętrzny config nie dzieli tego locka: adapter nadal kontroluje dostęp.
            with self._lock:
                if not valid():
                    return True, None
                store.apply(updates, deletes)
            for record in updates:
                run.records[record.identity.concept_id] = record
            for identity in deletes:
                run.records.pop(identity.concept_id, None)
            run.offset += len(concepts)
            return False, run
        snapshot = tuple(run.records[key] for key in sorted(run.records))
        with self._lock:
            if not valid():
                return True, None
            self._publish(run.context, snapshot, valid)
            if ticket_valid():
                self._requests.pop(item.key, None)
        return True, None
