"""C2: wyłącznie atrapy sekretów; bez rejestracji MCP i pełnego audytu DB/WAL."""
import codecs
import hashlib
import json
import os
import threading
from dataclasses import fields
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from roots import MODE_RO, MODE_RW, Zones
from roots_registry import RootsRegistry
from server import secret_tools as st
from server.file_changes import FileSnapshot
from state import load_config
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.models import TaskStatus
from tasks.store import TaskStore
from tasks.volatile_results import VolatileResult

PRIVATE = "SENTINEL-C2-PRIVATE"
RAW = ("TOKEN=" + PRIVATE + "\nOTHER=neighbor\n").encode()


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.json"
    config = ConfigStore(load_config(config_path), config_path)
    central = tmp_path / "central"
    central.mkdir()
    registry = RootsRegistry(config, zones=Zones(), central_dir=central)
    app, docs, other = (tmp_path / name for name in ("app", "docs", "other"))
    for base in (app, docs, other):
        base.mkdir()
    registry.add(app, MODE_RW, alias="app")
    registry.add(docs, MODE_RO, alias="docs")
    registry.add(other, MODE_RW, alias="other")
    store = TaskStore(str(tmp_path / "tasks.db"))
    approval = ApprovalService()
    manager = TaskManager(store, approval, max_queued=3)
    yield SimpleNamespace(config=config, registry=registry, app=app, docs=docs,
                          other=other, store=store, approval=approval, manager=manager)
    manager.shutdown()
    store.close()


def put(env, raw=RAW, name=".env", base=None):
    path = (base or env.app) / name
    path.write_bytes(raw)
    return path


def reveal(env, filepath=".env", **kwargs):
    return st.secret_reveal_impl(env.registry, env.manager, env.config.lock, filepath, **kwargs)


def pending(env, **kwargs):
    response = reveal(env, **kwargs)
    assert response["success"], response
    return response["data"]["task_id"]


def finish(env, task_id, decision="allow"):
    env.approval.resolve(task_id, decision)
    return env.manager.wait(task_id, 5)


def content(env, task_id):
    return env.manager.get_volatile_result(task_id)["data"]["content"]


def plan(env):
    return st._prepare(env.registry, env.config.lock, ".env", None,
                       st._selector("TOKEN", None, None), 12000)


def assert_private_absent(env, caplog):
    tasks = [task.to_dict() for task in env.manager.list()]
    with env.store._lock:
        dump = "\n".join(env.store._conn.iterdump())
    public = json.dumps(tasks) + dump + caplog.text
    assert PRIVATE not in public
    assert hashlib.sha256(RAW).hexdigest() not in public


@pytest.mark.parametrize("kwargs", [
    {}, {"key": "TOKEN", "start_line": 1}, {"start_line": 1}, {"end_line": 1},
    {"start_line": True, "end_line": 2}, {"start_line": 0, "end_line": 2},
    {"start_line": 2, "end_line": 1}, {"start_line": 1, "end_line": "2"},
    {"key": ""}, {"key": " TOKEN"}, {"key": "A=B"}, {"key": "a\nb"},
    {"key": "TOKEN", "max_chars": True}, {"key": "TOKEN", "max_chars": 0},
    {"key": "TOKEN", "max_chars": 80001}, {"key": "TOKEN", "max_chars": "12"},
    {"key": "TOKEN", "description": "a\nb"},
    {"key": "TOKEN", "description": "a" + chr(0x202E)},
    {"key": "TOKEN", "description": "x" * 501},
])
def test_bad_parameters_no_task(env, kwargs):
    put(env)
    assert not reveal(env, **kwargs)["success"]
    assert env.manager.list() == []


@pytest.mark.parametrize("raw,expected", [
    (b"TOKEN=value=part\n", "value=part"),
    (b" export TOKEN = ' value ' \n", " value "),
    (b'TOKEN="value"\n', "value"), (b"TOKEN=\n", ""),
    (b"TOKEN=''\n", ""), (b"# TOKEN=comment\nTOKEN=value\n", "value"),
    (b"TOKEN=${OTHER}\\n\n", "${OTHER}" + chr(92) + "n"),
])
def test_key_extraction_literal(env, raw, expected):
    path = put(env, raw)
    task_id = pending(env, key="TOKEN")
    assert finish(env, task_id).status == TaskStatus.DONE
    assert content(env, task_id) == expected
    assert path.read_bytes() == raw


@pytest.mark.parametrize("raw", [
    b"token=x\n", b"TOKEN=x\nTOKEN=y\n", b"TOKEN='unclosed\n",
    b"TOKEN=x # comment\n", b"TOKEN=x;comment\n", b"TOKEN=x\\\nMORE=y\n",
    b"TOKEN='first\nsecond'\n", b"TOKEN=x\ncontinuation\n",
])
def test_ambiguous_key_no_task(env, raw):
    put(env, raw)
    assert reveal(env, key="TOKEN") == {"success": False, "error": st.KEY_ERROR}
    assert env.manager.list() == []


@pytest.mark.parametrize("name", ["pip.ini", "pip.conf", ".npmrc"])
def test_ini_sections_duplicates_and_continuation(env, name):
    put(env, b"[first]\nTOKEN=x\n[second]\nTOKEN=y\n", name)
    assert not reveal(env, name, key="TOKEN")["success"]
    put(env, b"[first]\nTOKEN=x\n  TOKEN=y\n", name)
    assert not reveal(env, name, key="TOKEN")["success"]
    task_id = pending(env, filepath=name, start_line=2, end_line=3)
    assert finish(env, task_id).status == TaskStatus.DONE
    assert content(env, task_id) == "TOKEN=x\n  TOKEN=y\n"


@pytest.mark.parametrize("name", ["pip.ini", ".npmrc"])
def test_ini_key_and_export_not_supported(env, name):
    put(env, b"[global]\nTOKEN = value\n", name)
    task_id = pending(env, filepath=name, key="TOKEN")
    assert finish(env, task_id).status == TaskStatus.DONE
    assert content(env, task_id) == "value"
    put(env, b"export TOKEN=value\n", name)
    assert not reveal(env, name, key="TOKEN")["success"]


@pytest.mark.parametrize("raw,expected", [
    (b"one\r\ntwo\r\nlast", "two\r\n"),
    (b"one\ntwo\nlast", "two\n"), (b"one\rtwo\rlast", "two\r"),
    (codecs.BOM_UTF8 + b"one\r\ntwo\nlast", "two\n"),
    ("one\nżółć\r\nlast".encode("cp1250"), "żółć\r\n"),
    ("one\na\fb".encode(), "a\fb"),
    ("one\na" .encode() + chr(0x2028).encode() + b"b", "a" + chr(0x2028) + "b"),
])
def test_range_physical_eol_encoding(env, raw, expected):
    put(env, raw)
    task_id = pending(env, start_line=2, end_line=2)
    assert finish(env, task_id).status == TaskStatus.DONE
    assert content(env, task_id) == expected


@pytest.mark.parametrize("raw,start,end", [(b"", 1, 1), (b"x\n", 1, 2), (b"x", 2, 2)])
def test_range_out_of_bounds_no_clamp(env, raw, start, end):
    put(env, raw)
    assert not reveal(env, start_line=start, end_line=end)["success"]
    assert not env.manager.list()


@pytest.mark.parametrize("name", ["id.pem", "id.key"])
def test_pem_raw_key_range_only(env, name):
    put(env, b"first\nPRIVATE-BODY\nlast\n", name)
    assert not reveal(env, name, key="TOKEN")["success"]
    task_id = pending(env, filepath=name, start_line=2, end_line=2)
    assert finish(env, task_id).status == TaskStatus.DONE
    assert content(env, task_id) == "PRIVATE-BODY\n"


@pytest.mark.parametrize("name,raw", [
    (".env.example", RAW), (".env.sample", RAW), (".env.template", RAW),
    ("ordinary.txt", RAW), ("id.pfx", RAW), ("id.p12", RAW),
    (".env", b"a\x00b"), (".env", "TOKEN=x".encode("utf-16")),
    (".env", codecs.BOM_UTF16_BE + b"\x41\x42"),
    (".env", codecs.BOM_UTF8 + b"\xff"),
])
def test_unsupported_files_no_task(env, name, raw):
    put(env, raw, name)
    assert not reveal(env, name, start_line=1, end_line=1)["success"]
    assert not env.manager.list()


def test_limits_without_truncation(env, monkeypatch):
    put(env, b"TOKEN=abcd\n")
    assert not reveal(env, key="TOKEN", max_chars=3)["success"]
    task_id = pending(env, key="TOKEN", max_chars=4)
    assert finish(env, task_id).status == TaskStatus.DONE
    assert content(env, task_id) == "abcd"
    original_bytes_limit = st.MAX_CONTENT_BYTES
    monkeypatch.setattr(st, "MAX_CONTENT_BYTES", 3)
    assert reveal(env, key="TOKEN") == {"success": False, "error": st.LIMIT_ERROR}
    monkeypatch.setattr(st, "MAX_CONTENT_BYTES", original_bytes_limit)
    monkeypatch.setattr(st, "MAX_METADATA_BYTES", 100)
    assert reveal(env, key="TOKEN") == {"success": False, "error": st.METADATA_ERROR}


def test_file_size_limit(env, monkeypatch):
    from server import file_changes
    monkeypatch.setattr(file_changes, "MAX_FILE_BYTES", 8)
    put(env)
    assert not reveal(env, key="TOKEN")["success"]
    assert not env.manager.list()


def test_pending_plan_payload_and_success_separate_channel(env, caplog):
    path = put(env)
    names = set(env.app.iterdir())
    task_id = pending(env, key="TOKEN", description="  test  ")
    task = env.manager.get(task_id)
    assert task.status == TaskStatus.AWAITING_APPROVAL
    assert task.sensitive and not task.offer_trust
    assert set(task.payload) == {"root", "filepath", "selector", "max_chars", "description"}
    assert task.payload["description"] == "test"
    ctx = env.manager._ctx[task_id]
    for callback in (ctx.executor, ctx.verify):
        captured = [cell.cell_contents for cell in callback.__closure__]
        assert not any(isinstance(value, (FileSnapshot, bytes)) for value in captured)
        plans = [value for value in captured if isinstance(value, st._Plan)]
        assert len(plans) == 1
        assert {f.name for f in fields(plans[0])} == {
            "binding", "sha256", "identity", "mode", "selector", "max_chars"}
        assert PRIVATE not in repr(plans[0])
    assert env.manager.get_volatile_result(task_id) == {"state": "unavailable"}
    assert_private_absent(env, caplog)
    assert finish(env, task_id).status == TaskStatus.DONE
    assert content(env, task_id) == PRIVATE
    assert content(env, task_id) == PRIVATE
    assert "neighbor" not in content(env, task_id)
    assert path.read_bytes() == RAW and set(env.app.iterdir()) == names
    assert_private_absent(env, caplog)


@pytest.mark.parametrize("decision", ["deny", "cancel", "timeout"])
def test_denied_cancelled_no_result(env, decision):
    put(env)
    task_id = pending(env, key="TOKEN")
    if decision == "cancel":
        env.manager.cancel(task_id)
    else:
        finish(env, task_id, decision)
    assert env.manager.get_volatile_result(task_id) == {"state": "unavailable"}


@pytest.mark.parametrize("action", ["content", "delete", "replace", "root", "mode", "alias", "owner"])
def test_conflicts_no_result(env, action, caplog):
    if action == "owner":
        inner = env.app / "inner"
        inner.mkdir()
        path = put(env, base=inner)
        task_id = pending(env, filepath="inner/.env", key="TOKEN")
    else:
        path = put(env)
        task_id = pending(env, key="TOKEN")
    if action == "content":
        path.write_bytes(b"TOKEN=changed\n")
    elif action == "delete":
        path.unlink()
    elif action == "replace":
        replacement = put(env, RAW, ".env.replacement")
        os.replace(replacement, path)
    elif action == "root":
        env.registry.remove("app")
    elif action == "mode":
        env.registry.update("app", mode=MODE_RO)
    elif action == "alias":
        env.registry.update("app", new_alias="renamed")
    else:
        env.registry.add(inner, MODE_RO, alias="inner")
    assert finish(env, task_id).status == TaskStatus.FAILED
    assert env.manager.get_volatile_result(task_id) == {"state": "unavailable"}
    assert_private_absent(env, caplog)


def test_ro_read_default_pinned_and_access_invalidation(env, monkeypatch):
    path = put(env, base=env.docs)
    task_id = pending(env, key="TOKEN", root="docs")
    env.registry.set_default("other")
    assert finish(env, task_id).status == TaskStatus.DONE
    entry = env.manager._volatile._entries[task_id]
    captured = [cell.cell_contents for cell in entry.access_check.__closure__]
    assert any(isinstance(value, st._Binding) for value in captured)
    assert not any(isinstance(value, (st._Plan, FileSnapshot, bytes)) for value in captured)
    def fail_read(*args, **kwargs):
        raise AssertionError("callback must not read snapshot")
    monkeypatch.setattr(st, "read_snapshot", fail_read)
    path.write_bytes(b"TOKEN=new-content\n")
    assert content(env, task_id) == PRIVATE
    env.registry.remove("docs")
    assert env.manager.get_volatile_result(task_id) == {"state": "denied"}
    assert env.manager.get_volatile_result(task_id) == {"state": "unavailable"}


def test_extraction_uses_same_verified_snapshot(env, monkeypatch):
    path = put(env)
    prepared = plan(env)
    original = st._extract
    calls = []
    def extract(snapshot, *args):
        calls.append(snapshot.sha256)
        # Modyfikacja zewnętrzna po odczycie: ekstrakcja nadal z tego snapshotu.
        path.write_bytes(b"TOKEN=external\n")
        return original(snapshot, *args)
    monkeypatch.setattr(st, "_extract", extract)
    outcome = st._make_executor(env.registry, env.config.lock, prepared)(threading.Event())
    assert isinstance(outcome, VolatileResult)
    assert outcome.data["content"] == PRIVATE
    assert calls == [prepared.sha256]


def test_cancel_while_waiting_for_write_lock(env):
    put(env)
    execute = st._make_executor(env.registry, env.config.lock, plan(env))
    event, entered = threading.Event(), threading.Event()
    outcomes = []
    def worker():
        entered.set()
        outcomes.append(execute(event))
    with st.WRITE_LOCK:
        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(2)
        event.set()
        thread.join(2)
        assert not thread.is_alive()
    assert outcomes == [{"cancelled": True}]


@pytest.mark.parametrize("failing", ["write", "config"])
def test_release_failure_discards_result_and_attempts_both(env, monkeypatch, failing):
    put(env)
    prepared = plan(env)
    events = []
    class Lock:
        def __init__(self, name):
            self.name = name
        def acquire(self, timeout):
            events.append("acquire:" + self.name)
            return True
        def release(self):
            events.append("release:" + self.name)
            if self.name == failing:
                raise RuntimeError(PRIVATE)
    monkeypatch.setattr(st, "WRITE_LOCK", Lock("write"))
    outcome = st._make_executor(env.registry, Lock("config"), prepared)(threading.Event())
    assert outcome == {"started": False, "error": st.CONFLICT_ERROR}
    assert events == ["acquire:write", "acquire:config", "release:config", "release:write"]


def test_safe_exceptions_and_queue(env, monkeypatch, caplog):
    put(env)
    for _ in range(3):
        pending(env, key="TOKEN")
    assert reveal(env, key="TOKEN") == {"success": False, "error": st.QUEUE_ERROR}
    def fail(*args, **kwargs):
        raise RuntimeError(PRIVATE)
    monkeypatch.setattr(st, "read_snapshot", fail)
    response = reveal(env, key="TOKEN")
    assert not response["success"] and PRIVATE not in repr(response)
    assert_private_absent(env, caplog)


def test_panel_denial_and_stopped_manager(env):
    put(env)
    env.approval.set_notifier(lambda task: env.approval.resolve(task.id, "deny"))
    response = reveal(env, key="TOKEN")
    assert not response["success"] and response["data"]["status"] == "rejected"
    env.manager.shutdown()
    assert reveal(env, key="TOKEN") == {"success": False, "error": st.STATE_ERROR}
