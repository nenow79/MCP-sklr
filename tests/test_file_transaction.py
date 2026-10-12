"""5b A2: publikacja, konflikty, rollback, cleanup i współdzielone blokady."""
import codecs
import json
import os
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from roots import MODE_RO, MODE_RW, Zones
from roots_registry import RootsRegistry
from server import file_transaction as ft
from server.file_changes import PreparedChanges, prepare_edit, prepare_write
from server.file_transaction import FileTransaction
from state import load_config


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.json"
    store = ConfigStore(load_config(config_path), config_path)
    central = tmp_path / "central"
    central.mkdir()
    registry = RootsRegistry(store, zones=Zones(), central_dir=central)
    app, other = tmp_path / "app", tmp_path / "other"
    app.mkdir()
    other.mkdir()
    registry.add(app, MODE_RW, alias="app")
    registry.add(other, MODE_RW, alias="other")
    return SimpleNamespace(registry=registry, store=store, app=app, other=other,
                           tx=FileTransaction(registry, store.lock))


def put(base, name="a.txt", raw=b"old"):
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def edit(env, names=("a.txt",)):
    return prepare_edit(env.registry, [dict(filepath=name, old_text="old", new_text="new")
                                       for name in names])


def no_temps(env):
    assert not list(env.app.rglob(".sklr-*.tmp"))
    assert not list(env.other.rglob(".sklr-*.tmp"))


def assert_failed(result, rollback=True):
    assert not result["success"] and not result["data"]["committed"]
    assert result["data"]["rollback_complete"] is rollback


@pytest.mark.parametrize("raw", [b"old\r\n", codecs.BOM_UTF8 + b"old\n",
                                  "old żółć\r\n".encode("cp1250")])
def test_publish_preserves_prepared_bytes(env, raw):
    path = put(env.app, raw=raw)
    plan = edit(env)
    assert env.tx.verify(plan) is None
    result = env.tx.execute(plan)
    assert result["success"] and result["data"]["committed"]
    assert path.read_bytes() == plan.files[0].after
    no_temps(env)


def test_multi_root_publish(env):
    first, second = put(env.app), put(env.other)
    plan = prepare_edit(env.registry, [
        dict(filepath="a.txt", old_text="old", new_text="one"),
        dict(filepath="a.txt", old_text="old", new_text="two", root="other"),
    ])
    assert env.tx.execute(plan)["success"]
    assert first.read_bytes() == b"one" and second.read_bytes() == b"two"
    no_temps(env)


def test_create_overwrite_and_noop(env):
    path = env.app / "a.txt"
    assert env.tx.execute(prepare_write(env.registry, "a.txt", ""))["success"]
    assert path.read_bytes() == b""
    assert env.tx.execute(prepare_write(env.registry, "a.txt", "old", True))["success"]
    plan = prepare_write(env.registry, "a.txt", "old", True)
    inode = path.stat().st_ino
    result = env.tx.execute(plan)
    assert result["success"] and result["data"]["published_count"] == 0
    assert path.stat().st_ino == inode
    no_temps(env)


@pytest.mark.parametrize("action", ["content", "delete", "directory", "identity"])
def test_changed_file_before_execute_rejected(env, action):
    path = put(env.app)
    plan = edit(env)
    if action == "content":
        path.write_bytes(b"external")
    elif action == "delete":
        path.unlink()
    elif action == "directory":
        path.unlink()
        path.mkdir()
    else:
        new = put(env.app, "replacement.txt")
        os.replace(new, path)
    assert env.tx.verify(plan) is not None
    assert_failed(env.tx.execute(plan))
    if action == "content":
        assert path.read_bytes() == b"external"
    no_temps(env)


def test_create_does_not_overwrite_newly_appeared_target(env):
    plan = prepare_write(env.registry, "a.txt", "new")
    path = put(env.app, raw=b"external")
    assert_failed(env.tx.execute(plan))
    assert path.read_bytes() == b"external"
    no_temps(env)


@pytest.mark.parametrize("action", ["remove", "rename", "ro", "nested_ro", "nested_rw", "repoint"])
def test_root_changes_rejected(env, action):
    path = put(env.app, "inner/a.txt")
    plan = edit(env, ("inner/a.txt",))
    if action == "remove":
        env.registry.remove("app")
    elif action == "rename":
        env.registry.update("app", new_alias="renamed")
    elif action == "ro":
        env.registry.update("app", mode=MODE_RO)
    elif action.startswith("nested"):
        env.registry.add(env.app / "inner", MODE_RO if action == "nested_ro" else MODE_RW, alias="inner")
    else:
        env.registry.remove("app")
        env.registry.remove("other")
        env.registry.add(env.other, MODE_RW, alias="app")
    assert_failed(env.tx.execute(plan))
    assert path.read_bytes() == b"old"
    no_temps(env)


def test_changed_default_or_description_does_not_redirect_plan(env):
    path = put(env.app)
    plan = edit(env)
    env.registry.set_default("other")
    env.registry.update("app", description="nowy opis")
    assert env.tx.execute(plan)["success"]
    assert path.read_bytes() == b"new" and not (env.other / "a.txt").exists()


def test_conflict_after_staging_prevents_all_publication(env, monkeypatch):
    first, second = put(env.app), put(env.app, "b.txt")
    plan = edit(env, ("a.txt", "b.txt"))
    original = env.tx._stage
    calls = 0
    def stage(*args):
        nonlocal calls
        temp = original(*args)
        calls += 1
        if calls == 4:
            second.write_bytes(b"external")
        return temp
    monkeypatch.setattr(env.tx, "_stage", stage)
    assert_failed(env.tx.execute(plan))
    assert first.read_bytes() == b"old" and second.read_bytes() == b"external"
    no_temps(env)


def test_conflict_immediately_before_second_replace_rolls_back_first(env, monkeypatch):
    first, second = put(env.app), put(env.app, "b.txt")
    plan = edit(env, ("a.txt", "b.txt"))
    original = env.tx._publish
    calls = 0
    def publish(staged):
        nonlocal calls
        calls += 1
        if calls == 2:
            second.write_bytes(b"external")
        original(staged)
    monkeypatch.setattr(env.tx, "_publish", publish)
    result = env.tx.execute(plan)
    assert_failed(result)
    assert result["data"]["published_count"] == 1
    assert first.read_bytes() == b"old" and second.read_bytes() == b"external"
    no_temps(env)


@pytest.mark.parametrize("failure", ["mkstemp", "fsync", "chmod", "replace"])
def test_io_failure_is_safe_and_cleans_temps(env, monkeypatch, failure):
    path = put(env.app)
    plan = edit(env)
    def fail(*args, **kwargs):
        raise OSError("SENTINEL-RAW-EXCEPTION")
    if failure == "mkstemp":
        monkeypatch.setattr(ft.tempfile, "mkstemp", fail)
    else:
        # Przy chmod błąd jednorazowy, aby cleanup mógł działać.
        original = getattr(ft.os, failure)
        called = False
        def once(*args, **kwargs):
            nonlocal called
            if not called:
                called = True
                return fail()
            return original(*args, **kwargs)
        monkeypatch.setattr(ft.os, failure, once)
    result = env.tx.execute(plan)
    assert_failed(result)
    assert result["data"]["cleanup_complete"]
    assert "SENTINEL" not in json.dumps(result)
    assert path.read_bytes() == b"old"
    no_temps(env)


def test_partial_replace_failure_rolls_back_in_reverse_order(env, monkeypatch):
    paths = [put(env.app, name) for name in ("a.txt", "b.txt", "c.txt")]
    plan = edit(env, ("a.txt", "b.txt", "c.txt"))
    original = ft.os.replace
    targets = []
    def publish(source, target):
        targets.append(Path(target).name)
        if len(targets) == 3:
            raise OSError("publication failed")
        return original(source, target)
    monkeypatch.setattr(ft.os, "replace", publish)
    assert_failed(env.tx.execute(plan))
    assert targets == ["a.txt", "b.txt", "c.txt", "b.txt", "a.txt"]
    assert all(p.read_bytes() == b"old" for p in paths)
    no_temps(env)


def test_rollback_removes_created_file(env, monkeypatch):
    existing = put(env.app, "b.txt")
    created = prepare_write(env.registry, "a.txt", "new")
    plan = PreparedChanges(created.files + edit(env, ("b.txt",)).files)
    original = ft.os.replace
    def publish(source, target):
        if Path(target) == existing:
            raise OSError("second failed")
        return original(source, target)
    monkeypatch.setattr(ft.os, "replace", publish)
    assert_failed(env.tx.execute(plan))
    assert not (env.app / "a.txt").exists() and existing.read_bytes() == b"old"
    no_temps(env)


def test_rollback_does_not_overwrite_external_change(env, monkeypatch):
    first, second = put(env.app), put(env.app, "b.txt")
    plan = edit(env, ("a.txt", "b.txt"))
    original = env.tx._publish
    def publish(staged):
        if staged.item.path == second:
            first.write_bytes(b"external")
            raise OSError("second failed")
        original(staged)
    monkeypatch.setattr(env.tx, "_publish", publish)
    result = env.tx.execute(plan)
    assert_failed(result, rollback=False)
    assert "Niepełny rollback" in result["error"]
    assert first.read_bytes() == b"external" and second.read_bytes() == b"old"
    no_temps(env)


def test_rollback_failure_reported_and_cleanup_attempted(env, monkeypatch):
    first, second = put(env.app), put(env.app, "b.txt")
    plan = edit(env, ("a.txt", "b.txt"))
    original = ft.os.replace
    calls = 0
    def publish(source, target):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise PermissionError("SENTINEL")
        return original(source, target)
    monkeypatch.setattr(ft.os, "replace", publish)
    result = env.tx.execute(plan)
    assert_failed(result, rollback=False)
    assert first.read_bytes() == b"new" and second.read_bytes() == b"old"
    assert "SENTINEL" not in json.dumps(result)
    no_temps(env)


@pytest.mark.parametrize("when", ["before", "staging", "published"])
def test_cancel_before_during_and_after_publication(env, monkeypatch, when):
    path = put(env.app)
    plan = edit(env)
    event = threading.Event()
    if when == "before":
        event.set()
    else:
        attr = "_stage" if when == "staging" else "_publish"
        original = getattr(env.tx, attr)
        def wrapped(*args):
            result = original(*args)
            event.set()
            return result
        monkeypatch.setattr(env.tx, attr, wrapped)
    result = env.tx.execute(plan, event)
    assert_failed(result)
    assert result["data"]["cancelled"] and path.read_bytes() == b"old"
    no_temps(env)


def test_cancel_while_waiting_for_shared_lock(env):
    put(env.app)
    plan = edit(env)
    event, entered = threading.Event(), threading.Event()
    result = []
    def worker():
        entered.set()
        result.append(env.tx.execute(plan, event))
    with ft.WRITE_LOCK:
        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(2)
        event.set()
        thread.join(2)
        assert not thread.is_alive()
    assert result[0]["data"]["cancelled"]
    no_temps(env)


def test_two_instances_share_lock_and_second_plan_conflicts(env, monkeypatch):
    path = put(env.app)
    plan = edit(env)
    other_tx = FileTransaction(env.registry, env.store.lock)
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    original = env.tx._stage
    result = {}
    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(env.tx, "_stage", blocked)
    def first():
        result["first"] = env.tx.execute(plan)
    def second():
        second_started.set()
        result["second"] = other_tx.execute(plan)
    t1, t2 = threading.Thread(target=first), threading.Thread(target=second)
    t1.start()
    try:
        assert entered.wait(2)
        t2.start()
        assert second_started.wait(2)
    finally:
        release.set()
        t1.join(4)
        if t2.ident is not None:
            t2.join(4)
    assert not t1.is_alive() and not t2.is_alive()
    assert result["first"]["success"]
    assert_failed(result["second"])
    assert path.read_bytes() == b"new"
    no_temps(env)


def test_config_lock_held_through_staging_and_publication(env, monkeypatch):
    put(env.app)
    plan = edit(env)
    acquired = []
    original = env.tx._stage
    def stage(*args):
        def contender():
            got = env.store.lock.acquire(blocking=False)
            acquired.append(got)
            if got:
                env.store.lock.release()
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
        return original(*args)
    monkeypatch.setattr(env.tx, "_stage", stage)
    assert env.tx.execute(plan)["success"]
    assert acquired and not any(acquired)


def test_staging_tamper_rejected(env, monkeypatch):
    path = put(env.app)
    plan = edit(env)
    original = env.tx._stage
    first_temp = []
    def stage(*args):
        temp = original(*args)
        if not first_temp:
            first_temp.append(temp)
        else:
            first_temp[0].path.write_bytes(b"tampered")
        return temp
    monkeypatch.setattr(env.tx, "_stage", stage)
    assert_failed(env.tx.execute(plan))
    assert path.read_bytes() == b"old"
    no_temps(env)


def test_cleanup_failure_reports_committed_without_rollback(env, monkeypatch):
    path = put(env.app)
    plan = edit(env)
    original = Path.unlink
    def fail(temp, *args, **kwargs):
        if temp.name.startswith(".sklr-"):
            raise PermissionError("SENTINEL")
        return original(temp, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail)
    result = env.tx.execute(plan)
    assert not result["success"] and result["data"]["committed"]
    assert not result["data"]["cleanup_complete"]
    assert path.read_bytes() == b"new" and "SENTINEL" not in json.dumps(result)
    monkeypatch.undo()
    for temp in env.app.glob(".sklr-*.tmp"):
        temp.unlink()


def test_secret_result_contains_no_values_or_hashes(env):
    path = put(env.app, ".env", b"TOKEN=old\n")
    plan = edit(env, (".env",))
    result = env.tx.execute(plan)
    assert result["success"] and path.read_bytes() == b"TOKEN=new\n"
    public = json.dumps(result)
    for value in ("TOKEN=old", "TOKEN=new", plan.files[0].before.sha256, plan.files[0].after_hash):
        assert value not in public
    no_temps(env)


@pytest.mark.skipif(os.name != "nt", reason="junction specyficzny dla Windows")
def test_junction_retargeted_after_preparation_rejected(env):
    real, other = env.app / "real", env.app / "other-real"
    real.mkdir()
    other.mkdir()
    first, second = put(real), put(other)
    link = env.app / "link"
    def junction(target):
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, text=True)
        if result.returncode:
            pytest.skip("nie udało się utworzyć junctiona")
    junction(real)
    try:
        plan = edit(env, ("link/a.txt",))
        link.rmdir()
        junction(other)
        assert_failed(env.tx.execute(plan))
        assert first.read_bytes() == b"old" and second.read_bytes() == b"old"
        no_temps(env)
    finally:
        if link.exists():
            link.rmdir()


def test_symlink_retargeted_after_preparation_rejected(env):
    first, second = put(env.app), put(env.app, "b.txt")
    link = env.app / "link.txt"
    try:
        os.symlink(first, link)
    except (OSError, NotImplementedError):
        pytest.skip("brak uprawnień do symlinków")
    plan = edit(env, ("link.txt",))
    link.unlink()
    os.symlink(second, link)
    assert_failed(env.tx.execute(plan))
    assert first.read_bytes() == b"old" and second.read_bytes() == b"old"
    no_temps(env)
