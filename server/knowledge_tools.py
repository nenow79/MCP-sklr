"""Adapter odczytowych narzędzi OKF i kolejki; bez zależności od FastMCP.

Żadne query, dokumenty ani rewizje nie trafiają do TaskManager/SQLite. Executor
przechwytuje także nieoczekiwane wyjątki przed ogólnym loggerem kolejki.
"""
import json

from okf_engine.bundles import evaluate_bundles
from okf_engine.discovery import effective_knowledge
from okf_engine.documents import DocumentError
from okf_engine.limits import parse_limits
from okf_engine.search import SearchError
from okf_engine.service import KnowledgeError, KnowledgeService
from server.path_guard import resolve_path
from state import resolve_central_skills_dir
from tasks.manager import QueueFullError, TaskStateError


class RefreshQueue:
    def __init__(self, manager, panel_available):
        self.manager = manager
        self.panel_available = panel_available

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
                    return {"started": False, "exit_code": 1, "error": "Odświeżanie OKF przerwane.",
                            "cancelled": summary.get("cancelled", False)}
                # Jawna lista pól: nawet zmiana serwisu nie utrwali treści dokumentów.
                safe = {"index_state": summary["index_state"],
                        "indexed_documents": summary["indexed_documents"],
                        "partial": summary["partial"]}
                return {"exit_code": 0, "stdout": json.dumps(safe, ensure_ascii=False)}
            except Exception:
                return {"started": False, "exit_code": 1, "error": "Odświeżanie OKF nie powiodło się."}

        try:
            task = self.manager.submit(
                {"kind": "knowledge_refresh", "title": "Odświeżanie indeksu OKF",
                 "payload": {"bundle_id": bundle_id}, "offer_trust": False,
                 "sensitive": True}, executor, needs_approval=False)
        except QueueFullError:
            raise KnowledgeError("QUEUE_FULL") from None
        except TaskStateError:
            raise KnowledgeError("REFRESH_UNAVAILABLE") from None
        return task.id


def create_service(config_store, roots, manager, panel_available, **kwargs):
    """Jeden serwis/signer na proces; wykrywa docs bez skanu treści i zapisu configu."""
    def source():
        with config_store.lock:
            config = config_store.snapshot()
            knowledge = config.get("knowledge", {})
            root_list = roots.list()
            knowledge = effective_knowledge(knowledge, root_list)
            skills = resolve_central_skills_dir(config)
            limits, _ = parse_limits(knowledge.get("limits", {}) if isinstance(knowledge, dict) else None)
            states = evaluate_bundles(knowledge, root_list, skills, allow_project=True)
            # Prywatny token konfiguracji, bez treści plików; nigdy do odpowiedzi/logów.
            version = json.dumps([knowledge, [root.to_dict() for root in root_list], str(skills)],
                                 sort_keys=True, ensure_ascii=False)
            return states, limits, version

    def authorize(state, path):
        with config_store.lock:
            relative = path.relative_to(state.entry.root_path).as_posix()
            resolved = resolve_path(roots, relative, state.entry.root)
            return resolved.path == path and str(resolved.root.path) == state.entry.root_path

    queue = RefreshQueue(manager, panel_available)
    service = KnowledgeService(source, authorize, queue.submit, queue.status,
                               lock=config_store.lock,
                               active_project=lambda: roots.default().alias if roots.default() else None, **kwargs)
    service.bundles()  # wykrycie przy starcie; indeks dopiero na żądanie
    return service


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
