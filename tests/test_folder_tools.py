"""5b B2: manage_folder (server/folder_tools.py) z prawdziwą kolejką zadań."""
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from roots import MODE_RO, MODE_RW, Zones
from roots_registry import RootsRegistry
from server import folder_tools as ftl
from server.file_changes import SensitivePolicy
from server.file_transaction import WRITE_LOCK
from server.folder_tools import manage_folder_impl
from state import load_config
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore

BACKSLASH = chr(92)


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.json"
    store = ConfigStore(load_config(config_path), config_path)
    central = tmp_path / "central"
    central.mkdir()
    registry = RootsRegistry(store, zones=Zones(), central_dir=central)
    app, other, docs = tmp_path / "app", tmp_path / "other", tmp_path / "docs"
    for folder in (app, other, docs):
        folder.mkdir()
    registry.add(app, MODE_RW, alias="app")
    registry.add(other, MODE_RW, alias="other")
    registry.add(docs, MODE_RO, alias="docs")
    db_path = tmp_path / "tasks.db"
    task_store = TaskStore(str(db_path))
    approval = ApprovalService()
    manager = TaskManager(task_store, approval, max_queued=3)
    yield SimpleNamespace(registry=registry, store=store, app=app, other=other, docs=docs,
                          manager=manager, approval=approval, db_path=db_path, tmp=tmp_path)
    manager.shutdown()
    task_store.close()


def folder(env, action, path, new_name=None, **kwargs):
    return manage_folder_impl(env.registry, env.manager, env.store.lock, action, path, new_name, **kwargs)


def no_tasks(env):
    return env.manager.list(limit=10) == []


def decide(env, task_id, decision="allow", wait=5):
    env.approval.resolve(task_id, decision)
    return env.manager.wait(task_id, wait)


def sqlite_dump(env):
    conn = sqlite3.connect(str(env.db_path))
    try:
        return json.dumps(conn.execute("SELECT * FROM tasks").fetchall(), ensure_ascii=False, default=str)
    finally:
        conn.close()


def make_link(link, target):
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, text=True)
        if result.returncode:
            pytest.skip("nie udało się utworzyć junctiona")
    else:
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("brak uprawnień do symlinków")


def drop_link(link):
    try:
        os.rmdir(link)
    except OSError:
        link.unlink()


# --- błędne argumenty: brak zadania ---

@pytest.mark.parametrize("action", ["delete", "move", "", None, 5, "CREATE"])
def test_bad_action_no_task(env, action):
    assert folder(env, action, "x")["success"] is False
    assert no_tasks(env)


def test_bad_path_and_arguments_no_task(env):
    (env.app / "src").mkdir()
    assert folder(env, "create", "")["success"] is False
    assert folder(env, "create", None)["success"] is False
    assert folder(env, "create", 5)["success"] is False
    assert folder(env, "create", "x", "y")["success"] is False  # new_name tylko w rename
    assert folder(env, "rename", "src")["success"] is False
    assert folder(env, "rename", "src", None)["success"] is False
    assert no_tasks(env)


@pytest.mark.parametrize("bad", ["", "  ", None, 5, "..", ".", "a/b", "a" + BACKSLASH + "b", "x:y",
                                  "con", "nul.txt", "x.", "x ", "x*y", "a\x00b", "x" * 256])
def test_bad_new_name_no_task(env, bad):
    (env.app / "src").mkdir()
    assert folder(env, "rename", "src", bad)["success"] is False
    assert no_tasks(env)
    assert (env.app / "src").is_dir()


@pytest.mark.parametrize("bad", [5, "x" * 501, "a\nb", "a\u202eb", "a\u200bb"])
def test_bad_description_no_task(env, bad):
    (env.app / "src").mkdir()
    assert folder(env, "create", "new", description=bad)["success"] is False
    assert folder(env, "rename", "src", "dst", description=bad)["success"] is False
    assert no_tasks(env)


def test_path_errors_no_task(env):
    (env.docs / "src").mkdir()
    assert folder(env, "create", "../x")["success"] is False
    assert folder(env, "create", str(env.tmp / "zz"))["success"] is False  # poza rootami
    assert folder(env, "create", "x", root="nope")["success"] is False
    assert folder(env, "create", "x", root="docs")["success"] is False  # ro
    assert folder(env, "rename", "src", "dst", root="docs")["success"] is False
    assert folder(env, "create", "con")["success"] is False
    assert no_tasks(env)
    assert not (env.app / "x").exists()


def test_unexpected_exception_is_generic(env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("SENTINEL")
    monkeypatch.setattr(ftl, "_build", boom)
    result = folder(env, "create", "x")
    assert result == {"success": False, "error": ftl.INTERNAL_ERROR}
    assert no_tasks(env)


# --- create ---

def test_create_task_then_allow(env):
    result = folder(env, "create", "newdir", description="  powód  ")
    assert result["success"] is True
    data = result["data"]
    assert data["status"] == "awaiting_approval" and data["kind"] == "folder" and data["action"] == "create"
    assert data["paths"] == ["app:newdir"] and data["sensitive"] is False
    assert "poczekaj" in data["hint"] and "list_tasks" in data["hint"]
    task = env.manager.get(data["task_id"])
    assert task.kind == "folder" and task.sensitive is False and task.offer_trust is False
    assert task.title == "Utwórz folder app:newdir"
    assert task.payload == {"root": "app", "action": "create", "path": "newdir", "new_name": None,
                            "target": "newdir", "paths": ["app:newdir"], "diff": "+ app:newdir/\n",
                            "description": "powód"}
    assert not (env.app / "newdir").exists()  # nic przed decyzją
    done = decide(env, task.id)
    assert done.status.value == "done" and done.exit_code == 0 and done.stdout == "Utworzono folder."
    assert (env.app / "newdir").is_dir()


def test_create_in_other_root_and_nested(env):
    (env.other / "sub").mkdir()
    task_id = folder(env, "create", "sub/deeper", root="other")["data"]["task_id"]
    assert env.manager.get(task_id).payload["root"] == "other"
    assert decide(env, task_id).status.value == "done"
    assert (env.other / "sub" / "deeper").is_dir()


def test_create_errors_no_task(env):
    (env.app / "exists").mkdir()
    (env.app / "file.txt").write_bytes(b"x")
    assert "istnieje" in folder(env, "create", "exists")["error"]
    assert "istnieje" in folder(env, "create", "file.txt")["error"]
    assert "nadrzędny" in folder(env, "create", "missing/child")["error"]
    assert folder(env, "create", ".")["success"] is False  # sam root
    assert no_tasks(env)


def test_create_dangling_link_target_rejected(env):
    link = env.app / "dangling"
    make_link(link, env.tmp / "nowhere")
    try:
        assert folder(env, "create", "dangling")["success"] is False
        assert no_tasks(env)
    finally:
        drop_link(link)


def test_create_on_vanished_root_path_rejected(env):
    deep = env.app / "sub" / "deep"
    deep.mkdir(parents=True)
    env.registry.add(deep, MODE_RO, alias="deep")
    shutil.rmtree(deep)
    result = folder(env, "create", "sub/deep")
    assert result["success"] is False and no_tasks(env)


def test_create_under_nested_ro_root_rejected(env):
    deep = env.app / "sub" / "deep"
    deep.mkdir(parents=True)
    env.registry.add(deep, MODE_RO, alias="deep")
    assert folder(env, "create", "sub/deep/x")["success"] is False
    assert no_tasks(env)


# --- rename ---

def test_rename_task_then_allow(env):
    (env.app / "old").mkdir()
    (env.app / "old" / "keep.txt").write_bytes(b"data")
    result = folder(env, "rename", "old", "new", description="porządki")
    assert result["success"] is True
    data = result["data"]
    assert data["kind"] == "folder" and data["action"] == "rename"
    assert data["paths"] == ["app:old", "app:new"] and data["sensitive"] is False
    task = env.manager.get(data["task_id"])
    assert task.title == "Zmień nazwę app:old → new" and task.offer_trust is False
    assert task.payload == {"root": "app", "action": "rename", "path": "old", "new_name": "new",
                            "target": "new", "paths": ["app:old", "app:new"],
                            "diff": "- app:old/\n+ app:new/\n", "description": "porządki"}
    assert (env.app / "old").is_dir() and not (env.app / "new").exists()
    done = decide(env, task.id, "trust")
    assert done.status.value == "done" and done.decision == "allow" and done.exit_code == 0
    assert done.stdout == "Zmieniono nazwę folderu."
    assert not (env.app / "old").exists() and (env.app / "new" / "keep.txt").read_bytes() == b"data"


def test_rename_nested_source(env):
    (env.app / "a" / "b").mkdir(parents=True)
    task_id = folder(env, "rename", "a/b", "c")["data"]["task_id"]
    assert decide(env, task_id).status.value == "done"
    assert (env.app / "a" / "c").is_dir() and not (env.app / "a" / "b").exists()


def test_rename_errors_no_task(env):
    (env.app / "src").mkdir()
    (env.app / "taken").mkdir()
    (env.app / "file.txt").write_bytes(b"x")
    assert "istnieje" in folder(env, "rename", "src", "taken")["error"]
    (env.app / "plik").write_bytes(b"x")
    assert "istnieje" in folder(env, "rename", "src", "plik")["error"]
    assert "katalogiem" in folder(env, "rename", "file.txt", "dir")["error"]
    assert folder(env, "rename", "missing", "dir")["success"] is False
    assert folder(env, "rename", "src", "src")["success"] is False
    assert no_tasks(env)


@pytest.mark.skipif(os.name != "nt", reason="wielkość liter istotna tylko na Windows")
def test_rename_case_only_rejected(env):
    (env.app / "Foo").mkdir()
    assert folder(env, "rename", "Foo", "foo")["success"] is False
    assert no_tasks(env)


def test_rename_root_and_ancestor_of_root_rejected(env):
    assert folder(env, "rename", ".", "x")["success"] is False
    assert folder(env, "rename", "", "x")["success"] is False
    deep = env.app / "sub" / "deep"
    deep.mkdir(parents=True)
    env.registry.add(deep, MODE_RO, alias="deep")
    assert "root" in folder(env, "rename", "sub", "renamed")["error"]
    assert folder(env, "rename", str(deep), "x", root="deep")["success"] is False
    assert no_tasks(env)
    assert deep.is_dir()


def test_rename_junction_or_symlink_source_rejected(env):
    (env.app / "real").mkdir()
    link = env.app / "link"
    make_link(link, env.app / "real")
    try:
        result = folder(env, "rename", "link", "x")
        assert result["success"] is False and "link" in result["error"].lower()
        assert no_tasks(env)
    finally:
        drop_link(link)
    assert (env.app / "real").is_dir()


def test_rename_through_intermediate_link_rejected(env):
    (env.app / "real" / "sub").mkdir(parents=True)
    link = env.app / "link"
    make_link(link, env.app / "real")
    try:
        assert folder(env, "rename", "link/sub", "x")["success"] is False
        assert no_tasks(env)
    finally:
        drop_link(link)
    assert (env.app / "real" / "sub").is_dir()


# --- sensitive ---

@pytest.mark.parametrize("name", ["panel", ".git", "tasks.db-wal"])
def test_create_p10_names_are_sensitive(env, name):
    result = folder(env, "create", name)
    assert result["success"] is True and result["data"]["sensitive"] is True
    task = env.manager.get(result["data"]["task_id"])
    assert task.sensitive is True and "wrażliwa" in task.approval_reason


def test_rename_to_or_from_sensitive_name(env):
    (env.app / "foo").mkdir()
    (env.app / "panel").mkdir()
    (env.app / "panel" / "x").mkdir()
    to_sensitive = folder(env, "rename", "foo", ".git")
    assert to_sensitive["data"]["sensitive"] is True
    from_sensitive = folder(env, "rename", "panel", "gui")
    assert from_sensitive["data"]["sensitive"] is True


def test_plain_folder_is_not_sensitive(env):
    (env.app / "src").mkdir()
    assert folder(env, "create", "assets")["data"]["sensitive"] is False
    assert folder(env, "rename", "src", "lib")["data"]["sensitive"] is False


def test_ancestor_of_server_dir_is_sensitive(env):
    inner = env.app / "srv" / "inner"
    inner.mkdir(parents=True)
    policy = SensitivePolicy(server_dir=inner)
    result = folder(env, "rename", "srv", "srv2", policy=policy)
    assert result["data"]["sensitive"] is True
    task = env.manager.get(result["data"]["task_id"])
    assert "repo działającego serwera" in task.approval_reason
    assert folder(env, "rename", "srv", "srv3")["data"]["sensitive"] is False  # domyślna polityka


# --- decyzje, konflikty ---

def test_deny_and_cancel_before_execution(env):
    (env.app / "src").mkdir()
    first = folder(env, "rename", "src", "dst")["data"]["task_id"]
    assert decide(env, first, "deny").status.value == "rejected"
    second = folder(env, "create", "x")["data"]["task_id"]
    assert env.manager.cancel(second).status.value == "cancelled"
    assert (env.app / "src").is_dir() and not (env.app / "dst").exists() and not (env.app / "x").exists()


def test_create_target_appeared_after_request_fails(env):
    task_id = folder(env, "create", "x")["data"]["task_id"]
    (env.app / "x").mkdir()
    (env.app / "x" / "mine.txt").write_bytes(b"1")
    done = decide(env, task_id)
    assert done.status.value == "failed" and "Konflikt" in done.error
    assert (env.app / "x" / "mine.txt").read_bytes() == b"1"


def test_rename_source_removed_after_request_fails(env):
    (env.app / "src").mkdir()
    task_id = folder(env, "rename", "src", "dst")["data"]["task_id"]
    (env.app / "src").rmdir()
    done = decide(env, task_id)
    assert done.status.value == "failed" and "Konflikt" in done.error
    assert not (env.app / "dst").exists()


def test_rename_source_replaced_after_request_fails(env):
    (env.app / "src").mkdir()
    (env.app / "src" / "orig.txt").write_bytes(b"o")
    task_id = folder(env, "rename", "src", "dst")["data"]["task_id"]
    os.rename(env.app / "src", env.app / "moved")  # stary katalog nadal istnieje: nowy ma inną tożsamość
    (env.app / "src").mkdir()
    done = decide(env, task_id)
    assert done.status.value == "failed" and "Konflikt" in done.error
    assert not (env.app / "dst").exists() and (env.app / "moved" / "orig.txt").is_file()


def test_rename_target_appeared_after_request_fails(env):
    (env.app / "src").mkdir()
    task_id = folder(env, "rename", "src", "dst")["data"]["task_id"]
    (env.app / "dst").mkdir()
    done = decide(env, task_id)
    assert done.status.value == "failed" and "Konflikt" in done.error
    assert (env.app / "src").is_dir()


@pytest.mark.parametrize("change", ["remove", "ro"])
def test_root_changed_after_request_fails(env, change):
    task_id = folder(env, "create", "x")["data"]["task_id"]
    if change == "remove":
        env.registry.remove("app")
    else:
        env.registry.update("app", mode="ro")
    done = decide(env, task_id)
    assert done.status.value == "failed" and "Konflikt" in done.error
    assert not (env.app / "x").exists()


# --- wykonanie: adapter, blokady, anulowanie ---

def test_os_error_is_generic_and_does_not_leak(env, monkeypatch):
    (env.app / "src").mkdir()
    task_id = folder(env, "rename", "src", "dst")["data"]["task_id"]
    original = os.rename

    def fail(src, dst, *args, **kwargs):
        if os.path.basename(str(src)) == "src":
            raise PermissionError("SENTINEL")
        return original(src, dst, *args, **kwargs)
    monkeypatch.setattr(ftl.os, "rename", fail)
    done = decide(env, task_id)
    assert done.status.value == "failed" and done.error == ftl.OP_ERROR
    assert "SENTINEL" not in json.dumps(done.to_dict()) and "SENTINEL" not in sqlite_dump(env)
    assert (env.app / "src").is_dir() and not (env.app / "dst").exists()


def test_mkdir_error_is_generic(env, monkeypatch):
    task_id = folder(env, "create", "x")["data"]["task_id"]

    def fail(path, *args, **kwargs):
        raise OSError("SENTINEL")
    monkeypatch.setattr(ftl.os, "mkdir", fail)
    done = decide(env, task_id)
    assert done.status.value == "failed" and done.error == ftl.OP_ERROR
    assert "SENTINEL" not in sqlite_dump(env)


def test_unexpected_exception_in_executor_is_generic(env, monkeypatch):
    task_id = folder(env, "create", "x")["data"]["task_id"]

    def boom(*args, **kwargs):
        raise RuntimeError("SENTINEL")
    monkeypatch.setattr(ftl.os, "mkdir", boom)
    done = decide(env, task_id)
    assert done.status.value == "failed" and done.error == ftl.EXEC_INTERNAL_ERROR
    assert "SENTINEL" not in sqlite_dump(env)


def test_execution_waits_for_shared_write_lock(env):
    task_id = folder(env, "create", "x")["data"]["task_id"]
    WRITE_LOCK.acquire()
    released = False
    try:
        env.approval.resolve(task_id, "allow")
        time.sleep(0.4)
        assert env.manager.get(task_id).status.value == "running"
        assert not (env.app / "x").exists()
        WRITE_LOCK.release()
        released = True
        done = env.manager.wait(task_id, 5)
    finally:
        if not released:
            WRITE_LOCK.release()
    assert done.status.value == "done" and (env.app / "x").is_dir()


def test_cancel_while_waiting_for_lock_changes_nothing(env):
    task_id = folder(env, "create", "x")["data"]["task_id"]
    WRITE_LOCK.acquire()
    try:
        env.approval.resolve(task_id, "allow")
        time.sleep(0.3)
        assert env.manager.get(task_id).status.value == "running"
        task = env.manager.cancel(task_id)
    finally:
        WRITE_LOCK.release()
    assert task.status.value == "cancelled" and task.exit_code is None
    assert not (env.app / "x").exists()


def test_config_lock_is_held_during_operation(env, monkeypatch):
    task_id = folder(env, "create", "x")["data"]["task_id"]
    acquired = []
    original = os.mkdir

    def mkdir(path, *args, **kwargs):
        def contender():
            got = env.store.lock.acquire(blocking=False)
            acquired.append(got)
            if got:
                env.store.lock.release()
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(2)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(ftl.os, "mkdir", mkdir)
    assert decide(env, task_id).status.value == "done"
    assert acquired and not any(acquired)


# --- kolejka i panel ---

def test_queue_full(env):
    for index in range(3):
        assert folder(env, "create", f"d{index}")["success"] is True
    result = folder(env, "create", "extra")
    assert result["success"] is False and "pełna" in result["error"]
    assert not (env.app / "extra").exists()


def test_panel_unavailable_reports_error(env):
    env.approval.set_notifier(lambda task: env.approval.resolve(task.id, "deny"))
    result = folder(env, "create", "x")
    assert result["success"] is False and "panel" in result["error"]
    assert result["data"]["status"] == "rejected"
    assert not (env.app / "x").exists()
