"""Create-tree regression tests; temporary roots and a real approval queue."""
import json
import os
import threading
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from roots import MODE_RO, MODE_RW, Zones
from roots_registry import RootsRegistry
from server import tree_tools as tree
from state import load_config
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.json"
    store = ConfigStore(load_config(config_path), config_path)
    central = tmp_path / "central"
    central.mkdir()
    registry = RootsRegistry(store, zones=Zones(), central_dir=central)
    app = tmp_path / "app"
    docs = tmp_path / "docs"
    app.mkdir()
    docs.mkdir()
    registry.add(app, MODE_RW, alias="app")
    registry.add(docs, MODE_RO, alias="docs")
    task_store = TaskStore(str(tmp_path / "tasks.db"))
    approval = ApprovalService()
    manager = TaskManager(task_store, approval, max_queued=10)
    yield SimpleNamespace(registry=registry, store=store, app=app, docs=docs,
                          manager=manager, approval=approval)
    manager.shutdown()
    task_store.close()


def submit(env, **kwargs):
    return tree.create_tree_impl(env.registry, env.manager, env.store.lock, **kwargs)


def decide(env, task_id, decision="allow"):
    env.approval.resolve(task_id, decision)
    return env.manager.wait(task_id, 5)


def test_one_approval_creates_entire_tree(env):
    result = submit(env, directories=["skill/assets", "skill/references"], files=[
        {"filepath": "skill/SKILL.md", "content": "hello"},
        {"filepath": "skill/scripts/run.py", "content": "print(1)\n"}])
    assert result["success"]
    assert result["data"]["status"] == "awaiting_approval"
    assert len(env.manager.list(limit=10)) == 1
    task = env.manager.get(result["data"]["task_id"])
    assert task.kind == "tree_create" and not task.offer_trust
    assert "app:skill/scripts/" in task.payload["directories"]
    assert len(task.payload["files"]) == 2
    assert not (env.app / "skill").exists()
    done = decide(env, task.id)
    assert done.status.value == "done" and done.exit_code == 0
    assert (env.app / "skill/scripts/run.py").read_bytes() == b"print(1)\n"
    assert (env.app / "skill/assets").is_dir()


def test_directories_only_deduplicate(env):
    result = submit(env, directories=["a/b", "a/b", "a"])
    assert result["success"]
    assert len(result["data"]["paths"]) == 2
    assert decide(env, result["data"]["task_id"]).status.value == "done"
    assert (env.app / "a/b").is_dir()


def test_files_only_preserves_existing_directory(env):
    (env.app / "existing").mkdir()
    (env.app / "existing/keep").write_bytes(b"keep")
    result = submit(env, files=[{"filepath": "existing/new", "content": ""}])
    assert result["success"]
    assert decide(env, result["data"]["task_id"]).status.value == "done"
    assert (env.app / "existing/new").read_bytes() == b""
    assert (env.app / "existing/keep").read_bytes() == b"keep"


@pytest.mark.parametrize("kwargs", [
    {}, {"directories": "bad"}, {"files": "bad"},
    {"directories": ["../outside"]}, {"directories": ["con"]},
    {"directories": ["x"], "root": "docs"},
    {"directories": ["x"], "root": "unknown"},
    {"directories": ["x"], "files": [{"filepath": "x", "content": "x"}]},
    {"files": [{"filepath": "x", "content": "1"}, {"filepath": "x", "content": "2"}]},
    {"files": [{"filepath": "x.pfx", "content": "x"}]},
    {"files": [{"filepath": "x", "content": "a" + chr(0)}]},
    {"files": [{"filepath": "x", "content": "a", "overwrite": True}]},
    {"directories": ["x"], "description": "bad\ntext"},
    {"directories": [f"d{i}" for i in range(201)]},
    {"files": [{"filepath": f"f{i}", "content": ""} for i in range(101)]},
])
def test_invalid_plan_has_no_task_or_writes(env, kwargs):
    assert not submit(env, **kwargs)["success"]
    assert env.manager.list(limit=10) == []
    assert list(env.app.iterdir()) == []


def test_existing_file_rejects_whole_plan(env):
    (env.app / "taken").write_bytes(b"original")
    result = submit(env, directories=["new/deep"], files=[{"filepath": "taken", "content": "new"}])
    assert not result["success"]
    assert not (env.app / "new").exists()
    assert (env.app / "taken").read_bytes() == b"original"
    assert env.manager.list(limit=10) == []


def test_rejection_does_not_write(env):
    result = submit(env, directories=["a/b"])
    assert decide(env, result["data"]["task_id"], "deny").status.value == "rejected"
    assert not (env.app / "a").exists()


def test_conflict_after_proposal_preserves_external_file(env):
    result = submit(env, files=[{"filepath": "new/file", "content": "ours"}])
    (env.app / "new").mkdir()
    (env.app / "new/file").write_bytes(b"external")
    done = decide(env, result["data"]["task_id"])
    assert done.status.value != "done"
    assert (env.app / "new/file").read_bytes() == b"external"


def test_sensitive_preview_contains_no_raw_secret(env):
    secret = "TREE_SENTINEL_PRIVATE_VALUE_123456789"
    result = submit(env, files=[{"filepath": ".env", "content": "API_KEY=" + secret + "\n"}])
    assert result["success"] and result["data"]["sensitive"]
    task = env.manager.get(result["data"]["task_id"])
    assert secret not in json.dumps(task.payload)
    assert "API_KEY" in task.payload["diff"]


def test_mkdir_failure_rolls_back_owned_directories(env, monkeypatch):
    args = (["a/b"], [], "app", None)
    plan = tree.prepare_tree(env.registry, *args)
    original = os.mkdir
    def fail(path, *a, **kw):
        if path == env.app / "a/b":
            raise OSError("failure")
        return original(path, *a, **kw)
    monkeypatch.setattr(tree.os, "mkdir", fail)
    result = tree._executor(env.registry, env.store.lock, args, plan)(threading.Event())
    assert result.get("started") is False
    assert not (env.app / "a").exists()


def test_cancel_before_execution_writes_nothing(env):
    args = (["a/b"], [], "app", None)
    plan = tree.prepare_tree(env.registry, *args)
    event = threading.Event()
    event.set()
    result = tree._executor(env.registry, env.store.lock, args, plan)(event)
    assert result.get("cancelled")
    assert not (env.app / "a").exists()


def test_nested_read_only_root_rejected(env):
    nested = env.app / "nested"
    nested.mkdir()
    env.registry.add(nested, MODE_RO, alias="nested")
    assert not submit(env, directories=["nested/deep"])["success"]
    assert env.manager.list(limit=10) == []


def test_link_in_path_rejected(env):
    target = env.app / "target"
    target.mkdir()
    link = env.app / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    assert not submit(env, directories=["link/deep"])["success"]
    assert env.manager.list(limit=10) == []
