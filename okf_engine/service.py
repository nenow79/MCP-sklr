"""Odczyt OKF i koordynacja odświeżeń, bez FastMCP/TaskManager.

source() zwraca (BundleState[], KnowledgeLimits | None, access_version).
access_version obejmuje konfigurację bundles, limits, roots i chroniony katalog
skills. Wszystkie operacje stanu/publikacji korzystają ze wspólnego reentrantnego
locka konfiguracji. Skan działa poza nim; zmiana wersji unieważnia jego ticket.
submit(bundle_id, run) zwraca id zadania, task_status(id) jego status (napis).
Callback run(cancel_event) zwraca wyłącznie bezpieczne podsumowanie dla kolejki.
"""
import hmac
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .documents import DocumentError, DocumentReader
from .index import KnowledgeIndex
from .models import IdentityError, validate_bundle_id
from .public_view import mask_text
from .revisions import RevisionSigner
from .scanner import scan_bundle
from .search import parse_request, prepare_document, search
from .textlines import split_lines


class KnowledgeError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _Cancelled(Exception):
    pass


@dataclass(frozen=True, repr=False)
class WriteContext:
    """Prywatne zależności adaptera zapisów; nie serializować ani logować."""
    bundle: object
    limits: object
    signer: object
    now: datetime
    version: object


@dataclass
class _Refresh:
    ticket: int
    version: object = field(repr=False)
    task_id: object = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    finished: bool = False


def _json_size(value):
    return len(json.dumps(value, ensure_ascii=False))


def _budget(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1000 <= value <= 80000:
        raise KnowledgeError("INVALID_ARGUMENT")


class KnowledgeService:
    def __init__(self, source, authorize, submit, task_status, *, lock=None,
                 now=None, clock=time.monotonic, signer=None, active_project=None):
        self._source, self._authorize = source, authorize
        self._submit, self._task_status = submit, task_status
        self._lock = lock if lock is not None else threading.RLock()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._clock = clock
        self._active_project = active_project or (lambda: None)
        self._signer = signer or RevisionSigner(RevisionSigner.new_key())
        self.index = KnowledgeIndex(self._now, prepare=prepare_document)
        self._version = object()
        self._states = {}
        self._limits = None
        self._refreshes = {}
        self._retry_at = {}

    def _sync(self):
        states, limits, version = self._source()
        available = {state.id: state for state in states if state.available}
        if version != self._version:
            for bundle_id in set(self._states) | set(self._refreshes):
                self.index.invalidate(bundle_id)
            for refresh in self._refreshes.values():
                refresh.cancel.set()
            self._refreshes.clear()
            self._retry_at.clear()
            self._version = version
        # Dostępność filesystem może zmienić się bez zmiany configu.
        for bundle_id in self._states.keys() - available.keys():
            self.index.invalidate(bundle_id)
            refresh = self._refreshes.pop(bundle_id, None)
            if refresh is not None:
                refresh.cancel.set()
            self._retry_at.pop(bundle_id, None)
        self._states, self._limits = available, limits

    def _current(self, bundle_id):
        with self._lock:
            self._sync()
            state = self._states.get(bundle_id)
            if state is None:
                raise DocumentError("BUNDLE_UNAVAILABLE")
            return state

    def _reader(self):
        if self._limits is None:
            raise KnowledgeError("CONFIG_INVALID")
        return DocumentReader(self._current, self._authorize, self._signer,
                              self._limits, self._now)

    def write_context(self, bundle_id):
        with self._lock:
            self._sync()
            self._select(bundle_id, "all")
            if bundle_id is None:
                raise KnowledgeError("INVALID_ARGUMENT")
            state = self._states[bundle_id]
            if not state.writable:
                raise KnowledgeError("WRITE_FORBIDDEN")
            if self._limits is None:
                raise KnowledgeError("CONFIG_INVALID")
            return WriteContext(state, self._limits, self._signer, self._now(), self._version)

    def check_write_context(self, context):
        with self._lock:
            self._sync()
            if context.version != self._version:
                raise KnowledgeError("BUNDLE_UNAVAILABLE")
            state = self._current(context.bundle.id)
            if not state.writable:
                raise KnowledgeError("WRITE_FORBIDDEN")
            if state.path != context.bundle.path or state.entry != context.bundle.entry:
                raise KnowledgeError("BUNDLE_UNAVAILABLE")

    def after_write(self, bundle_id):
        """Unieważnia dawną generację przed próbą skanu; nigdy nie cofa zapisu."""
        with self._lock:
            self.index.invalidate(bundle_id)
            pending = self._refreshes.pop(bundle_id, None)
            if pending is not None:
                pending.cancel.set()
            self._retry_at.pop(bundle_id, None)
            try:
                self._sync()
                if bundle_id not in self._states:
                    return {"index_state": "empty", "index_error": "BUNDLE_UNAVAILABLE"}
                task_id = self._request_refresh(bundle_id, "force")
                return {"index_state": self.index.state(bundle_id).state,
                        "refresh_task_id": task_id, "index_error": None}
            except Exception:
                # Żadnego wyjątku po committed do ogólnego loggera kolejki.
                return {"index_state": "error", "index_error": "REFRESH_UNAVAILABLE"}

    def write_time(self):
        return self._now()

    def refresh_bundle(self, bundle_id):
        with self._lock:
            self._sync()
            self._select(bundle_id, "all")
            return {"task_id": self._request_refresh(bundle_id, "force")}

    def after_project_write(self, root_alias):
        with self._lock:
            self._sync()
            selected = [key for key, state in self._states.items()
                        if state.entry.scope == "project" and state.entry.root == root_alias]
            return {key: self.after_write(key) for key in selected}

    def _select(self, bundle_id, scope):
        if scope not in ("all", "central", "project"):
            raise KnowledgeError("INVALID_ARGUMENT")
        if bundle_id is not None:
            try:
                validate_bundle_id(bundle_id)
            except IdentityError:
                raise KnowledgeError("INVALID_ARGUMENT") from None
            if bundle_id not in self._states:
                raise KnowledgeError("BUNDLE_UNAVAILABLE")
            if scope != "all" and self._states[bundle_id].entry.scope != scope:
                raise KnowledgeError("SCOPE_MISMATCH")
            return [bundle_id]
        active = self._active_project()
        return sorted(key for key, state in self._states.items()
                      if (scope in ("all", "central") and state.entry.scope == "central") or
                      (scope in ("all", "project") and state.entry.scope == "project" and state.entry.root == active))

    def _reap(self, bundle_id):
        refresh = self._refreshes.get(bundle_id)
        if refresh is None:
            return None
        status = self._task_status(refresh.task_id)
        if status in ("queued", "running"):
            return refresh
        if not refresh.finished:
            refresh.cancel.set()
            self.index.finish_failed(refresh.ticket, bundle_id, "REFRESH_INTERRUPTED")
            self._retry_at[bundle_id] = self._clock() + 5
        self._refreshes.pop(bundle_id, None)
        return None

    def _state(self, bundle_id):
        refresh = self._reap(bundle_id)
        state = self.index.state(bundle_id)
        result = asdict(state)
        result["index_state"] = result.pop("state")
        result["indexed_at"] = state.indexed_at.isoformat() if state.indexed_at else None
        result["reasons"] = list(state.reasons)
        result["refresh_task_id"] = refresh.task_id if refresh else None
        return result

    def bundles(self):
        with self._lock:
            self._sync()
            result = []
            for bundle_id, state in sorted(self._states.items()):
                result.append(dict(id=bundle_id, scope=state.entry.scope, writable=state.writable,
                                   label=mask_text(state.entry.label).text,
                                   description=mask_text(state.entry.description).text,
                                   **self._state(bundle_id)))
            return {"count": len(result), "bundles": result}

    def _request_refresh(self, bundle_id, mode):
        pending = self._reap(bundle_id)
        if pending is not None:
            return pending.task_id
        state = self.index.state(bundle_id)
        stale = (state.indexed_at is None or
                 (self._now() - state.indexed_at).total_seconds() >= self._limits.refresh_after_s)
        if mode == "cached" or (mode == "auto" and (
                self._clock() < self._retry_at.get(bundle_id, 0) or
                (not stale and state.state != "error"))):
            return None
        refresh = _Refresh(self.index.begin(bundle_id), self._version)
        self._refreshes[bundle_id] = refresh
        reader, limits = self._reader(), self._limits

        def run(cancel_event):
            return self._run_refresh(bundle_id, refresh, reader, limits, cancel_event)

        try:
            refresh.task_id = self._submit(bundle_id, run)
        except Exception as exc:
            self._refreshes.pop(bundle_id, None)
            code = exc.code if isinstance(exc, KnowledgeError) else "REFRESH_UNAVAILABLE"
            self.index.finish_failed(refresh.ticket, bundle_id, code)
            self._retry_at[bundle_id] = self._clock() + 5
            raise KnowledgeError(code) from None
        return refresh.task_id

    def _run_refresh(self, bundle_id, refresh, reader, limits, cancel_event):
        def clock():
            if cancel_event.is_set() or refresh.cancel.is_set():
                raise _Cancelled()
            return self._clock()

        try:
            with self._lock:
                self._sync()
                clock()
                if refresh.version != self._version:
                    raise _Cancelled()
            scan = scan_bundle(reader, bundle_id, limits, clock=clock)

            def can_publish():
                # KnowledgeIndex woła pod wspólnym lockiem tylko przed podmianą.
                self._sync()
                clock()
                return refresh.version == self._version

            if not self.index.publish(refresh.ticket, reader, bundle_id, scan, limits,
                                      commit_lock=self._lock, validate_commit=can_publish):
                raise _Cancelled()
            with self._lock:
                state = self.index.state(bundle_id)
                refresh.finished = True
                self._retry_at.pop(bundle_id, None)
                return {"index_state": state.state, "indexed_documents": state.indexed_documents,
                        "partial": state.state == "partial", "reasons": list(state.reasons)}
        except Exception as exc:
            # Nie przepuszczamy wyjątków od parsera/I/O do logów TaskManager.
            cancelled = isinstance(exc, _Cancelled) or cancel_event.is_set() or refresh.cancel.is_set()
            code = "REFRESH_CANCELLED" if cancelled else "REFRESH_FAILED"
            with self._lock:
                refresh.finished = True
                if self._refreshes.get(bundle_id) is refresh:
                    self.index.finish_failed(refresh.ticket, bundle_id, code)
                    self._retry_at[bundle_id] = self._clock() + 5
            return {"error": code, "cancelled": cancelled}

    def search(self, query="", bundle_id=None, scope="all", type=None, tags=None,
               status=None, query_mode="terms", browse=False, include_deprecated=False,
               limit=10, max_total_chars=12000, refresh="auto"):
        if refresh not in ("auto", "cached", "force"):
            raise KnowledgeError("INVALID_ARGUMENT")
        request = parse_request(query, query_mode, browse, type, tags, status,
                                include_deprecated, limit, max_total_chars,
                                explicit_bundle=bundle_id is not None)
        with self._lock:
            self._sync()
            selected = self._select(bundle_id, scope)
            reader = self._reader()
            tasks, failures = [], {}
            for selected_id in selected:
                try:
                    task_id = self._request_refresh(selected_id, refresh)
                    if task_id is not None:
                        tasks.append({"bundle_id": selected_id, "task_id": task_id})
                except KnowledgeError as exc:
                    failures[selected_id] = exc.code
            result = search(self.index, reader, selected, request,
                            self._limits.search_budget_s, clock=self._clock)
            # Ostatnia kontrola zakresu, również gdy skan indeksu nie dał trafień.
            self._sync()
            self._select(bundle_id, scope)
            hits = [hit.to_dict() for hit in result.hits if hit.bundle_id in self._states]
            reasons = set(result.reasons)
            if failures:
                reasons.add("refresh_unavailable")
            if len(hits) != result.count:
                reasons.add("bundle_unavailable")
            return {"results": hits, "count": len(hits),
                    "partial": result.partial or bool(failures) or len(hits) != result.count,
                    "truncated": result.truncated,
                    "reasons": sorted(reasons),
                    "policy": {"scope": scope, "archive": "excluded_for_project",
                               "include_deprecated": include_deprecated or request.status == "deprecated",
                               "skipped_deprecated": result.skipped_deprecated},
                    "index": {key: self._state(key) for key in selected if key in self._states},
                    "refresh_tasks": [task for task in tasks if task["bundle_id"] in self._states],
                    "refresh_errors": failures, "refresh_requested": refresh == "force"}

    def get(self, bundle_id, concept_id, view="outline", section_id=None,
            section_mode="subtree", expected_revision=None, max_chars=12000):
        _budget(max_chars)
        if (view not in ("outline", "section", "document") or
                section_mode not in ("direct", "subtree") or
                (view != "section" and (section_id is not None or section_mode != "subtree")) or
                (expected_revision is not None and (not isinstance(expected_revision, str) or
                                                   len(expected_revision) > 200)) or
                (view == "section" and (not isinstance(section_id, str) or not section_id or
                                        not expected_revision))):
            raise KnowledgeError("INVALID_ARGUMENT")
        with self._lock:
            self._sync()
            self._select(bundle_id, "all")
            reader = self._reader()
            document = reader.read(bundle_id, concept_id)
            if expected_revision is not None and not hmac.compare_digest(
                    expected_revision.encode("utf-8", errors="surrogatepass"), document.revision.encode("ascii")):
                raise KnowledgeError("REVISION_CONFLICT")
            public = document.view
            result = {"bundle_id": bundle_id, "scope": document._bundle.entry.scope,
                      "format": document.format,
                      "concept_id": public.identity.concept_id, "revision": document.revision,
                      "metadata": public.metadata,
                      "signals": {"status": public.status, "trust_tier": public.trust_tier,
                                  "stale": public.stale,
                                  "warnings": list(dict.fromkeys(public.warnings + document.outline.warnings))},
                      "masked": public.masked, "view": view, "truncated": False}
            if view == "outline":
                entries = [asdict(entry) for entry in document.outline.entries]
                # Budżet obejmuje pełne entries; identyfikatorów nigdy nie tniemy.
                selected, used = [], _json_size({"metadata": public.metadata, "entries": []})
                if used > max_chars:
                    raise KnowledgeError("RESULT_TOO_LARGE")
                for entry in entries:
                    extra = _json_size(entry) + (2 if selected else 0)
                    if used + extra > max_chars:
                        break
                    selected.append(entry)
                    used += extra
                result.update(entries=selected, truncated=len(selected) < len(entries) or document.outline.partial)
            else:
                if view == "section":
                    try:
                        section = document.outline.get_section(section_id, section_mode)
                    except ValueError:
                        raise KnowledgeError("SECTION_UNAVAILABLE") from None
                    content, start = section.content, section.start_line
                else:
                    content, start = public.body, public.body_start_line
                size = lambda text: _json_size({"metadata": public.metadata, "content": text})
                if size("") > max_chars:
                    raise KnowledgeError("RESULT_TOO_LARGE")
                if size(content) > max_chars:
                    low, high = 0, len(content)
                    while low < high:
                        middle = (low + high + 1) // 2
                        if size(content[:middle]) <= max_chars:
                            low = middle
                        else:
                            high = middle - 1
                    content = content[:low]
                    result["truncated"] = True
                result.update(content=content, start_line=start,
                              end_line=start + len(split_lines(content)) - 1 if content else None)
            reader.check_document(document)
            return result
