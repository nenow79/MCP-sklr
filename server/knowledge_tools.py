"""Adapter odczytowych narzędzi OKF i kolejki; bez zależności od FastMCP.

Żadne query, dokumenty ani rewizje nie trafiają do TaskManager/SQLite. Executor
przechwytuje także nieoczekiwane wyjątki przed ogólnym loggerem kolejki.
"""
import json
from contextlib import contextmanager

from okf_engine.bundles import evaluate_bundles
from okf_engine.documents import DocumentError
from okf_engine.search import SearchError
from okf_engine.service import KnowledgeError, KnowledgeService
from server.path_guard import resolve_path
from tasks.manager import QueueFullError, TaskStateError
from server.knowledge_sources import KnowledgeSources


class RefreshQueue:
    def __init__(self, manager, panel_available, central=None):
        self.manager = manager
        self.panel_available = panel_available
        self.central = central or (lambda bundle_id: False)

    def status(self, task_id):
        task = self.manager.get(task_id)
        return task.status.value if task is not None else "interrupted"

    def submit(self, bundle_id, run):
        if not self.panel_available():
            raise KnowledgeError("PANEL_UNAVAILABLE")

        def executor(cancel_event):
            try:
                summary = run(cancel_event)
                if summary.get("error"):
                    message = ("Odświeżanie OKF: przekroczono budżet publikacji; "
                               "zachowano poprzednią generację indeksu."
                               if summary["error"] == "REFRESH_BUDGET_EXHAUSTED"
                               else "Odświeżanie OKF przerwane.")
                    return {"started": False, "exit_code": 1, "error": message,
                            "cancelled": summary.get("cancelled", False)}
                # Jawna lista pól: nawet zmiana serwisu nie utrwali treści dokumentów.
                safe = {"index_state": summary["index_state"],
                        "indexed_documents": summary["indexed_documents"],
                        "partial": summary["partial"],
                        "reasons": [reason for reason in summary.get("reasons", [])
                                    if reason in {"scan_budget", "refresh_budget", "document_limit",
                                                  "depth_limit", "scan_io_error", "index_text_limit",
                                                  "document_unavailable"}]}
                # Diagnostyka z allowlisty serwisu: kody/liczniki/czasy, bez dokumentów.
                from okf_engine.service import READ_ERROR_CODES, READ_ERROR_STAGES, READER_METRIC_FIELDS
                diagnostics = summary.get("diagnostics", {})
                safe["diagnostics"] = {
                    name: value for name, value in diagnostics.items()
                    if name in {"scan_ms", "publish_ms", "candidates", "read_documents"}
                    and type(value) is int and value >= 0}
                for phase in ("scan_checks", "publish_checks"):
                    safe["diagnostics"][phase] = {
                        name: value for name, value in diagnostics.get(phase, {}).items()
                        if name in READER_METRIC_FIELDS and type(value) is int and value >= 0}
                safe["diagnostics"]["read_errors"] = {
                    code: count for code, count in diagnostics.get("read_errors", {}).items()
                    if code in READ_ERROR_CODES and type(count) is int and 0 < count <= 5000}
                safe["diagnostics"]["read_error_stages"] = {
                    name: count for name, count in diagnostics.get("read_error_stages", {}).items()
                    if isinstance(name, str) and name.partition(".")[0] in READ_ERROR_STAGES
                    and name.partition(".")[2] in READ_ERROR_CODES
                    and type(count) is int and 0 < count <= 5000}
                return {"exit_code": 0, "stdout": json.dumps(safe, ensure_ascii=False)}
            except Exception:
                return {"started": False, "exit_code": 1, "error": "Odświeżanie OKF nie powiodło się."}

        try:
            from autopilot.context import MAINTENANCE, CENTRAL_KNOWLEDGE
            task = self.manager.submit(
                {"kind": "knowledge_refresh", "title": "Odświeżanie indeksu OKF",
                 "payload": {"bundle_id": bundle_id}, "offer_trust": False,
                 "sensitive": True}, executor, needs_approval=False,
                internal_maintenance=CENTRAL_KNOWLEDGE if self.central(bundle_id) else MAINTENANCE)
        except QueueFullError:
            raise KnowledgeError("QUEUE_FULL") from None
        except TaskStateError:
            raise KnowledgeError("REFRESH_UNAVAILABLE") from None
        return task.id


def create_service(config_store, roots, manager, panel_available, **kwargs):
    """Jeden serwis/signer na proces; wykrywa docs bez skanu treści i zapisu configu."""
    controller = getattr(manager, '_sessions', None)
    selection = (lambda: controller.project_selection) if controller is not None else None
    sources = KnowledgeSources(config_store, roots, selection,
                               evaluate=lambda *args, **kw: evaluate_bundles(*args, **kw))

    def authorize(state, path):
        with config_store.lock:
            relative = path.relative_to(state.entry.root_path).as_posix()
            resolved = resolve_path(roots, relative, state.entry.root)
            return resolved.path == path and str(resolved.root.path) == state.entry.root_path

    def central(bundle_id):
        # submit runs under the service/config lock, after _sync evaluated the
        # current scope. The model never chooses the private queue capability.
        state = service._states.get(bundle_id)
        return state is not None and state.entry.scope == 'central'

    queue = RefreshQueue(manager, panel_available, central)
    service = KnowledgeService(sources.source, authorize, queue.submit, queue.status,
                               lock=config_store.lock, current_source=sources.current,
                               project_selection=selection,
                               active_project=lambda: roots.default().alias if roots.default() else None, **kwargs)
    service.project_reason = sources.project_reason
    if controller is not None:
        @contextmanager
        def coordinate():
            with manager._cond, controller._lock:
                yield
        service._dispatch_context = coordinate
    service.bundles()  # wykrycie przy starcie; indeks dopiero na żądanie
    return service


def start_background_refresh(service, manager):
    """After cache attachment/restore; catch sessions started while panel booted."""
    controller = getattr(manager, '_sessions', None)
    if controller is None:
        return service.refresh_available_background()

    def refresh_projects():
        with manager._cond, controller._lock:
            if not manager._stopped and not controller.snapshot()['execution_blocked']:
                return service.refresh_available_background(scope='project')
        return {}

    controller.add_activation_listener(refresh_projects)
    result = service.refresh_available_background(scope='central')
    result.update(refresh_projects())
    return result


def _call(operation, *args, **kwargs):
    try:
        return {"success": True, "data": operation(*args, **kwargs)}
    except (KnowledgeError, DocumentError) as exc:
        result = {"success": False, "error": exc.code}
        if exc.code == "RESULT_TOO_LARGE":
            result["hint"] = "Zwiększ max_chars (maksymalnie 80000)."
        return result
    except SearchError:
        return {"success": False, "error": "INVALID_ARGUMENT"}
    except Exception:
        return {"success": False, "error": "KNOWLEDGE_INTERNAL_ERROR"}


def knowledge_bundles_impl(service):
    return _call(service.bundles)


def knowledge_search_impl(service, query="", bundle_id=None, scope="all", type=None,
                          tags=None, status=None, query_mode="terms", browse=False,
                          include_deprecated=False, limit=10, max_total_chars=12000,
                          refresh="auto"):
    return _call(service.search, query, bundle_id, scope, type, tags, status, query_mode,
                 browse, include_deprecated, limit, max_total_chars, refresh)


def knowledge_get_impl(service, bundle_id, concept_id, view="outline", section_id=None,
                       section_mode="subtree", expected_revision=None, max_chars=12000):
    return _call(service.get, bundle_id, concept_id, view, section_id,
                 section_mode, expected_revision, max_chars)
