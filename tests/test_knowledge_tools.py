"""Integracja C2c z rzeczywistą kolejką i tymczasową konfiguracją/SQLite."""
import json
import threading
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from roots_registry import RootsRegistry
from server import knowledge_tools as tools
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore


@pytest.fixture
def env(tmp_path):
    base = tmp_path / "knowledge"
    base.mkdir()
    config = {
        "skills": {"central_dir": str(tmp_path / "skills")},
        "roots": [{"alias": "test", "path": str(base.resolve()), "mode": "ro"}],
        "knowledge": {"bundles": [{"id": "general", "label": "General", "scope": "central",
                                  "root": "test", "root_path": str(base.resolve()),
                                  "subpath": "", "enabled": True}]}}
    store = ConfigStore(config, tmp_path / "config.json")
    roots = RootsRegistry(store)
    task_store = TaskStore(str(tmp_path / "tasks.db"))
    approvals = []
    manager = TaskManager(task_store, ApprovalService(notifier=approvals.append), max_parallel=1)
    panel = {"available": True}
    service = tools.create_service(store, roots, manager, lambda: panel["available"])
    (base / "note.md").write_text(
        "---\ntype: Reference\npassword: META_SENTINEL\n---\n# Alpha\nBODY_CONTENT_SENTINEL\n"
        "token: SECRET_SENTINEL\n", encoding="utf-8")
    yield SimpleNamespace(base=base, store=store, roots=roots, service=service,
                          manager=manager, approvals=approvals, panel=panel, task_store=task_store)
    # Testy nie zostawiają pracujących executorów przy zamykaniu SQLite.
    for task in manager.list(limit=100):
        if not task.is_terminal:
            manager.wait(task.id, 8)
    manager.shutdown()
    task_store.close()


def search(env, **kwargs):
    return tools.knowledge_search_impl(env.service, **dict({"query": "alpha"}, **kwargs))


def complete(env, result):
    task_id = result["data"]["refresh_tasks"][0]["task_id"]
    task = env.manager.wait(task_id, 8)
    assert task.is_terminal
    return task


def test_real_queue_search_get_and_safe_persistence(env):
    result = search(env, query="BODY_CONTENT_SENTINEL QUERY_SENTINEL")
    assert result["success"] and result["data"]["partial"]
    done = complete(env, result)
    assert done.status.value == "done" and done.kind == "knowledge_refresh"
    assert done.payload == {"bundle_id": "general"}
    assert env.approvals == [] and not done.offer_trust
    assert json.loads(done.stdout)["indexed_documents"] == 1
    hit = search(env, refresh="cached")["data"]["results"][0]
    outline = tools.knowledge_get_impl(env.service, "general", "note")
    assert outline["success"] and outline["data"]["revision"] == hit["revision"]
    section = tools.knowledge_get_impl(env.service, "general", "note", "section", "s1",
                                       expected_revision=hit["revision"])
    assert section["success"] and "BODY_CONTENT_SENTINEL" in section["data"]["content"]
    assert "SECRET_SENTINEL" not in json.dumps(section)
    persisted = json.dumps(env.task_store.get(done.id).to_dict())
    for text in ("BODY_CONTENT_SENTINEL", "QUERY_SENTINEL", "META_SENTINEL",
                 "SECRET_SENTINEL", hit["revision"], str(env.base)):
        assert text not in persisted


def test_panel_down_no_task_or_scan(env):
    env.panel["available"] = False
    result = search(env)
    assert result["data"]["refresh_errors"] == {"general": "PANEL_UNAVAILABLE"}
    assert env.manager.list() == []
    assert env.service.index.snapshot("general") is None
    # Świeży odczyt nie wymaga zadania ani kolejnej zgody.
    assert tools.knowledge_get_impl(env.service, "general", "note")["success"]


def test_queue_full_no_synchronous_fallback(env, monkeypatch):
    from tasks.manager import QueueFullError

    def full(*args, **kwargs):
        raise QueueFullError("SECRET_SENTINEL")

    monkeypatch.setattr(env.manager, "submit", full)
    result = search(env)
    assert result["data"]["refresh_errors"] == {"general": "QUEUE_FULL"}
    assert "SECRET_SENTINEL" not in json.dumps(result)
    assert env.service.index.snapshot("general") is None


def test_executor_exceptions_not_logged_or_persisted(env, caplog):
    queue = tools.RefreshQueue(env.manager, lambda: True)

    def broken(cancel):
        raise RuntimeError("SECRET_SENTINEL")

    task_id = queue.submit("general", broken)
    task = env.manager.wait(task_id, 8)
    assert task.status.value == "failed"
    assert "SECRET_SENTINEL" not in caplog.text + json.dumps(task.to_dict())


def test_config_update_revokes_access_and_discards_cache(env):
    complete(env, search(env))
    env.store.update(lambda config: config["knowledge"]["bundles"][0].update(enabled=False))
    assert tools.knowledge_bundles_impl(env.service)["data"]["count"] == 0
    assert env.service.index.snapshot("general") is None
    assert search(env, bundle_id="general", refresh="cached")["error"] == "BUNDLE_UNAVAILABLE"
    assert tools.knowledge_get_impl(env.service, "general", "note")["error"] == "BUNDLE_UNAVAILABLE"


def test_root_removed_prevents_get_and_search(env):
    complete(env, search(env))
    env.store.update(lambda config: config.update(roots=[]))
    assert tools.knowledge_get_impl(env.service, "general", "note")["error"] == "BUNDLE_UNAVAILABLE"
    assert search(env, refresh="cached")["data"]["reasons"] == ["no_available_bundles"]


def test_slow_scan_returns_task_and_parallel_force_deduplicates(env, monkeypatch):
    from okf_engine import service as module
    entered, release = threading.Event(), threading.Event()
    original = module.scan_bundle

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "scan_bundle", blocked)
    try:
        first = search(env)
        assert entered.wait(5)
        second = search(env, refresh="force")
        assert first["data"]["refresh_tasks"] == second["data"]["refresh_tasks"]
        assert len(env.manager.list()) == 1
        # Zmiana podczas zablokowanego skanu: jego wynik nie może wrócić do indeksu.
        env.store.update(lambda config: config["knowledge"]["bundles"][0].update(enabled=False))
        tools.knowledge_bundles_impl(env.service)
    finally:
        release.set()
    task = complete(env, first)
    assert task.status.value == "cancelled"
    assert env.service.index.snapshot("general") is None


def test_publication_preparation_does_not_hold_config_lock(env):
    entered, release = threading.Event(), threading.Event()
    original = env.service.index._prepare

    def blocked(document):
        entered.set()
        assert release.wait(5)
        return original(document)

    env.service.index._prepare = blocked
    try:
        result = search(env)
        assert entered.wait(5)
        # Config i katalog muszą być dostępne także podczas przygotowania indeksu.
        env.store.update(lambda config: config["knowledge"]["bundles"][0].update(enabled=False))
        assert tools.knowledge_bundles_impl(env.service)["data"]["count"] == 0
    finally:
        release.set()
    assert complete(env, result).status.value == "cancelled"
    assert env.service.index.snapshot("general") is None


@pytest.mark.parametrize("params", [
    {"query": ""}, {"query": "", "browse": True}, {"scope": "bad"},
    {"refresh": "bad"}, {"limit": 0}, {"tags": "bad"},
])
def test_bad_search_arguments_do_not_queue(env, params):
    result = search(env, **params)
    assert not result["success"] and result["error"] == "INVALID_ARGUMENT"
    assert env.manager.list() == []


def test_safe_metadata_budget_error_and_bad_paths(env):
    (env.base / "note.md").write_text("---\ntype: Reference\ndescription: " + "x" * 2000 +
                                      "\n---\n# Alpha\n", encoding="utf-8")
    result = tools.knowledge_get_impl(env.service, "general", "note", max_chars=1000)
    assert result["error"] == "RESULT_TOO_LARGE" and "max_chars" in result["hint"]
    for path in ("../note", "/note", "C:/note", "note.md"):
        result = tools.knowledge_get_impl(env.service, "general", path)
        assert not result["success"]
        assert str(env.base) not in json.dumps(result)
