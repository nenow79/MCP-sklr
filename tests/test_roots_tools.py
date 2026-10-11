"""Testy narzędzi list_roots i request_root (server/roots_tools.py)."""
import shutil
from pathlib import Path

import pytest

from config_store import ConfigStore
from roots import MODE_RO, MODE_RW, Zones
from roots_registry import RootsRegistry
from server.roots_tools import list_roots_impl, request_root_impl
from state import load_config
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore


def _dir(base, *parts):
    path = Path(base).joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.json"
    store = ConfigStore(load_config(config_path), config_path)
    registry = RootsRegistry(store, zones=Zones(), central_dir=_dir(tmp_path, "central"))
    task_store = TaskStore(str(tmp_path / "tasks.db"))
    approval = ApprovalService()
    manager = TaskManager(task_store, approval, max_queued=3)

    class Env:
        pass

    e = Env()
    e.registry, e.manager, e.approval, e.tmp, e.store = registry, manager, approval, tmp_path, store
    yield e
    manager.shutdown()
    task_store.close()


# --- list_roots ---

def test_list_roots_empty(env):
    result = list_roots_impl(env.registry)
    assert result["success"] is True
    assert result["data"]["count"] == 0 and result["data"]["default"] is None
    assert "request_root" in result["data"]["hint"]


def test_list_roots_entries(env):
    env.registry.add(_dir(env.tmp, "front"), MODE_RW, description="ui")
    env.registry.add(_dir(env.tmp, "docs"), MODE_RO)
    data = list_roots_impl(env.registry)["data"]
    assert data["count"] == 2 and data["default"] == "front"
    first, second = data["roots"]
    assert first["alias"] == "front" and first["mode"] == "rw" and first["default"] is True
    assert first["description"] == "ui" and first["available"] is True
    assert second["mode"] == "ro" and second["default"] is False
    assert "root" in data["hint"]


def test_list_roots_marks_missing_folder_unavailable(env):
    env.registry.add(_dir(env.tmp, "gone"), MODE_RW)
    shutil.rmtree(env.tmp / "gone")
    assert list_roots_impl(env.registry)["data"]["roots"][0]["available"] is False


# --- request_root: walidacja bez zadania ---

def _no_tasks(env):
    return env.manager.list(limit=10) == []


def test_request_root_bad_arguments(env):
    folder = str(_dir(env.tmp, "a"))
    assert request_root_impl(env.registry, env.manager, "", "ro")["success"] is False
    assert request_root_impl(env.registry, env.manager, None, "ro")["success"] is False
    assert request_root_impl(env.registry, env.manager, folder, "xx")["success"] is False
    assert request_root_impl(env.registry, env.manager, folder, "ro", alias=5)["success"] is False
    assert request_root_impl(env.registry, env.manager, folder, "ro", description=5)["success"] is False
    assert request_root_impl(env.registry, env.manager, folder, "ro", alias="Zły Alias")["success"] is False
    assert _no_tasks(env)


def test_request_root_missing_folder_and_forbidden(env):
    assert request_root_impl(env.registry, env.manager, str(env.tmp / "brak"), "ro")["success"] is False
    assert request_root_impl(env.registry, env.manager, Path(env.tmp).anchor, "rw")["success"] is False
    assert _no_tasks(env)


def test_request_root_duplicates_and_nesting(env):
    env.registry.add(_dir(env.tmp, "outer"), MODE_RO, alias="outer")
    assert request_root_impl(env.registry, env.manager, str(env.tmp / "outer"), "ro",
                             alias="inny")["success"] is False
    inner = _dir(env.tmp, "outer", "inner")
    assert request_root_impl(env.registry, env.manager, str(inner), "rw")["success"] is False
    other = _dir(env.tmp, "other")
    assert request_root_impl(env.registry, env.manager, str(other), "ro", alias="outer")["success"] is False
    assert _no_tasks(env)


# --- request_root: zadanie ---

def test_request_root_creates_sensitive_task_without_adding(env):
    folder = _dir(env.tmp, "App")
    result = request_root_impl(env.registry, env.manager, str(folder), "rw", description="projekt")
    assert result["success"] is True
    data = result["data"]
    assert data["status"] == "awaiting_approval" and data["root"] == "app" and data["mode"] == "rw"
    assert "poczekaj" in data["hint"]
    task = env.manager.get(data["task_id"])
    assert task.kind == "root_change" and task.sensitive is True and task.offer_trust is False
    assert task.payload["mode"] == "rw" and task.payload["root"] == "app"
    assert task.payload["description"] == "projekt" and task.payload["path"] == str(folder.resolve())
    assert "rw" in task.title
    assert env.registry.list() == []  # nic nie dodano przed zatwierdzeniem


def test_request_root_allow_adds_root(env):
    folder = _dir(env.tmp, "front")
    task_id = request_root_impl(env.registry, env.manager, str(folder), "ro", alias="front")["data"]["task_id"]
    env.approval.resolve(task_id, "allow")
    task = env.manager.wait(task_id, 5)
    assert task.status.value == "done"
    root = env.registry.get("front")
    assert root is not None and root.mode == "ro" and root.default is True


def test_request_root_deny_adds_nothing(env):
    folder = _dir(env.tmp, "front")
    task_id = request_root_impl(env.registry, env.manager, str(folder), "rw")["data"]["task_id"]
    env.approval.resolve(task_id, "deny")
    assert env.manager.get(task_id).status.value == "rejected"
    assert env.registry.list() == []


def test_request_root_trust_is_downgraded_to_allow(env):
    folder = _dir(env.tmp, "front")
    task_id = request_root_impl(env.registry, env.manager, str(folder), "ro")["data"]["task_id"]
    env.approval.resolve(task_id, "trust")
    task = env.manager.wait(task_id, 5)
    assert task.decision == "allow" and task.status.value == "done"


def test_request_root_folder_removed_before_approval_fails(env):
    folder = _dir(env.tmp, "temp")
    task_id = request_root_impl(env.registry, env.manager, str(folder), "rw")["data"]["task_id"]
    shutil.rmtree(folder)
    env.approval.resolve(task_id, "allow")
    task = env.manager.wait(task_id, 5)
    assert task.status.value == "failed" and "nie istnieje" in task.error
    assert env.registry.list() == []


def test_request_root_conflict_after_request_fails_on_approval(env):
    folder = _dir(env.tmp, "same")
    task_id = request_root_impl(env.registry, env.manager, str(folder), "ro", alias="one")["data"]["task_id"]
    env.registry.add(folder, MODE_RW, alias="manual")  # użytkownik dodał ten sam folder wcześniej
    env.approval.resolve(task_id, "allow")
    task = env.manager.wait(task_id, 5)
    assert task.status.value == "failed"
    assert [r.alias for r in env.registry.list()] == ["manual"]


def test_request_root_auto_denied_when_only_dialog_available(env):
    env.approval.set_notifier(lambda task: env.approval.resolve(task.id, "deny"))
    result = request_root_impl(env.registry, env.manager, str(_dir(env.tmp, "a")), "ro")
    assert result["success"] is False and "panel" in result["error"]
    assert env.registry.list() == []


def test_request_root_queue_full(env):
    for index in range(3):
        assert request_root_impl(env.registry, env.manager, str(_dir(env.tmp, f"d{index}")), "ro",
                                 alias=f"d{index}")["success"] is True
    result = request_root_impl(env.registry, env.manager, str(_dir(env.tmp, "extra")), "ro")
    assert result["success"] is False and "pełna" in result["error"]
