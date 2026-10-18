"""5b B1: edit_file i create_file (server/edit_tools.py) z prawdziwą kolejką zadań."""
import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from roots import MODE_RO, MODE_RW, Zones
from roots_registry import RootsRegistry
from server import edit_tools as et
from server.edit_tools import create_file_impl, edit_file_impl
from server.file_changes import SensitivePolicy
from server.file_transaction import FileTransaction
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
                          manager=manager, approval=approval, db_path=db_path,
                          tx=FileTransaction(registry, store.lock), tmp=tmp_path)
    manager.shutdown()
    task_store.close()


def put(base, name="a.txt", raw=b"old"):
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def edit(env, changes, **kwargs):
    return edit_file_impl(env.registry, env.manager, env.tx, changes, **kwargs)


def create(env, filepath, content, **kwargs):
    return create_file_impl(env.registry, env.manager, env.tx, filepath, content, **kwargs)


def change(old="old", new="new", filepath="a.txt", **extra):
    return dict(filepath=filepath, old_text=old, new_text=new, **extra)


def no_tasks(env):
    return env.manager.list(limit=10) == []


def no_temps(env):
    for base in (env.app, env.other, env.docs):
        assert not list(base.rglob(".sklr-*.tmp"))


def sqlite_dump(env):
    conn = sqlite3.connect(str(env.db_path))
    try:
        return json.dumps(conn.execute("SELECT * FROM tasks").fetchall(), ensure_ascii=False, default=str)
    finally:
        conn.close()


def decide(env, task_id, decision="allow", wait=5):
    env.approval.resolve(task_id, decision)
    return env.manager.wait(task_id, wait)


class StubTx:
    """Atrapa transakcji: test adaptera wyniku bez dotykania plików."""

    def __init__(self, result=None, error=None, verify=None):
        self.result, self.error, self._verify = result, error, verify

    def verify(self, plan):
        return self._verify

    def execute(self, plan, cancel_event=None):
        if self.error is not None:
            raise self.error
        return self.result


def tx_result(success=True, committed=True, cancelled=False, rollback=True, cleanup=True,
              published=1, error=None):
    result = {"success": success, "data": {
        "committed": committed, "cancelled": cancelled, "rollback_complete": rollback,
        "cleanup_complete": cleanup, "published_count": published}}
    if error:
        result["error"] = error
    return result


def submit_with(env, tx):
    put(env.app)
    result = edit_file_impl(env.registry, env.manager, tx, [change()])
    assert result["success"] is True
    return result["data"]["task_id"]


def test_create_and_edit_notify_only_after_commit(env):
    calls = []

    def notify(paths, *, uncertain=False):
        calls.append((paths, uncertain))

    result = create(env, 'contract.md', 'old', after_write=notify)
    assert calls == []
    done = decide(env, result['data']['task_id'])
    assert done.status.value == 'done'
    assert calls == [((env.app / 'contract.md',), False)]
    calls.clear()
    result = edit(env, [change(filepath='contract.md')], after_write=notify)
    assert decide(env, result['data']['task_id']).status.value == 'done'
    assert calls == [((env.app / 'contract.md',), False)]


def test_denied_file_write_does_not_notify_index(env):
    calls = []
    result = create(env, 'contract.md', 'text', after_write=lambda *args, **kwargs: calls.append(args))
    assert decide(env, result['data']['task_id'], 'deny').status.value == 'rejected'
    assert calls == []


def test_index_notification_failure_does_not_fail_committed_file(env):
    def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE_SENTINEL')

    result = create(env, 'contract.md', 'text', after_write=fail)
    done = decide(env, result['data']['task_id'])
    assert done.status.value == 'done' and (env.app / 'contract.md').read_text() == 'text'
    assert 'Aktualizacja indeksu' in done.stdout
    assert 'PRIVATE_SENTINEL' not in json.dumps(done.to_dict())


def test_uncertain_rollback_notifies_index_conservatively(env):
    put(env.app)
    calls = []
    tx = StubTx(tx_result(success=False, committed=False, rollback=False))
    result = edit_file_impl(env.registry, env.manager, tx, [change()],
                            after_write=lambda paths, **kwargs: calls.append((paths, kwargs)))
    assert decide(env, result['data']['task_id']).status.value == 'failed'
    assert calls == [((env.app / 'a.txt',), {'uncertain': True})]


# --- błędne argumenty: brak zadania ---

@pytest.mark.parametrize("changes", ["x", None, [], [5], [{"filepath": "a.txt"}],
                                      [change(extra=1)], [change(old="")], [change(old=5)]])
def test_edit_bad_changes_no_task(env, changes):
    put(env.app)
    assert edit(env, changes)["success"] is False
    assert no_tasks(env)


def test_edit_match_errors_no_task(env):
    put(env.app, raw=b"old old")
    assert "2 dopasowań" in edit(env, [change()])["error"]
    assert "0 dopasowań" in edit(env, [change(old="brak")])["error"]
    assert no_tasks(env)


def test_edit_path_errors_no_task(env):
    put(env.app)
    put(env.docs)
    assert edit(env, [change(filepath="../x.txt")])["success"] is False
    assert edit(env, [change(root="nope")])["success"] is False
    assert edit(env, [change(root="docs")])["success"] is False  # root ro
    assert edit(env, [change(filepath="brak.txt")])["success"] is False
    assert no_tasks(env)


def test_blocked_p12_no_task(env):
    put(env.app, "k.p12", b"old")
    assert edit(env, [change(filepath="k.p12")])["success"] is False
    assert create(env, "n.p12", "x")["success"] is False
    assert no_tasks(env)


def test_noop_is_error_without_task(env):
    put(env.app)
    result = edit(env, [change(old="old", new="old")])
    assert result["success"] is False and "Brak zmian" in result["error"]
    assert create(env, "a.txt", "old", overwrite=True)["success"] is False
    assert no_tasks(env)


def test_create_errors_no_task(env):
    put(env.app)
    assert "overwrite" in create(env, "a.txt", "x")["error"]
    assert create(env, "sub/new.txt", "x")["success"] is False  # brak rodzica
    assert create(env, "b.txt", 5)["success"] is False
    assert create(env, "b.txt", "x", overwrite="yes")["success"] is False
    assert create(env, "b.txt", "x", root="docs")["success"] is False
    assert create(env, "../b.txt", "x")["success"] is False
    assert no_tasks(env)


def test_unexpected_exception_is_generic(env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("SENTINEL")
    monkeypatch.setattr(et, "prepare_edit", boom)
    result = edit(env, [change()])
    assert result == {"success": False, "error": et.INTERNAL_ERROR}
    assert no_tasks(env)


# --- description ---

@pytest.mark.parametrize("bad", [5, "x" * 501, "a\nb", "a\tb", "a\u202eb", "a\u200bb", "a\u2028b", "a\x00b"])
def test_bad_description_no_task(env, bad):
    put(env.app)
    assert edit(env, [change()], description=bad)["success"] is False
    assert create(env, "n.txt", "x", description=bad)["success"] is False
    assert no_tasks(env)


def test_description_stored_trimmed_and_limit(env):
    put(env.app)
    ok = edit(env, [change()], description="  powód zmiany  ")
    assert env.manager.get(ok["data"]["task_id"]).payload["description"] == "powód zmiany"
    edge = create(env, "n.txt", "x", description="x" * 500)
    assert edge["success"] is True
    none = create(env, "m.txt", "x", description=None)
    assert env.manager.get(none["data"]["task_id"]).payload["description"] == ""


# --- poprawne zadanie ---

def test_edit_creates_task_without_writing(env):
    path = put(env.app, raw=b"alpha old beta")
    result = edit(env, [change()], description="refaktor")
    assert result["success"] is True
    data = result["data"]
    assert data["status"] == "awaiting_approval" and data["kind"] == "file_edit"
    assert data["paths"] == ["app:a.txt"] and data["sensitive"] is False
    assert "poczekaj" in data["hint"] and "list_tasks" in data["hint"]
    task = env.manager.get(data["task_id"])
    assert task.kind == "file_edit" and task.sensitive is False and task.offer_trust is False
    assert task.title == "Edytuj app:a.txt"
    assert task.payload["root"] == "app" and task.payload["paths"] == ["app:a.txt"]
    assert task.payload["description"] == "refaktor" and task.payload["truncated"] is False
    assert "+alpha new beta" in task.payload["diff"] and "=== app:a.txt [zmiana] ===" in task.payload["diff"]
    assert set(task.payload) == {"root", "paths", "diff", "files", "truncated", "description"}
    assert path.read_bytes() == b"alpha old beta"  # nic nie zapisano przed decyzją


def test_create_task_and_allow(env):
    result = create(env, "n.txt", "hello\n", description="nowy")
    data = result["data"]
    assert data["kind"] == "file_write" and data["status"] == "awaiting_approval"
    task = env.manager.get(data["task_id"])
    assert task.title == "Utwórz app:n.txt" and task.offer_trust is False
    assert "+hello" in task.payload["diff"] and "[nowy]" in task.payload["diff"]
    assert not (env.app / "n.txt").exists()
    done = decide(env, data["task_id"])
    assert done.status.value == "done" and done.exit_code == 0
    assert done.stdout == "Zapisano plików: 1 z 1." and "hello" not in done.stdout
    assert (env.app / "n.txt").read_bytes() == b"hello\n"
    no_temps(env)


def test_overwrite_task_title_and_allow(env):
    put(env.app, raw=b"old\n")
    data = create(env, "a.txt", "new\n", overwrite=True)["data"]
    assert env.manager.get(data["task_id"]).title == "Nadpisz app:a.txt"
    assert decide(env, data["task_id"]).status.value == "done"
    assert (env.app / "a.txt").read_bytes() == b"new\n"


def test_edit_allow_writes_and_trust_is_downgraded(env):
    path = put(env.app)
    task_id = edit(env, [change()])["data"]["task_id"]
    done = decide(env, task_id, "trust")
    assert done.status.value == "done" and done.decision == "allow" and done.exit_code == 0
    assert path.read_bytes() == b"new"
    no_temps(env)


def test_multi_root_one_task_one_decision(env):
    first, second = put(env.app), put(env.other)
    result = edit(env, [change(new="one"), change(new="two", root="other")])
    task = env.manager.get(result["data"]["task_id"])
    assert task.title == "Edytuj pliki (2)" and task.payload["root"] is None
    assert task.payload["paths"] == ["app:a.txt", "other:a.txt"]
    assert len(env.manager.list(limit=10)) == 1
    done = decide(env, task.id)
    assert done.status.value == "done" and done.stdout == "Zapisano plików: 2 z 2."
    assert first.read_bytes() == b"one" and second.read_bytes() == b"two"
    no_temps(env)


def test_call_level_root_is_used(env):
    put(env.other, raw=b"old")
    task_id = edit(env, [change()], root="other")["data"]["task_id"]
    assert env.manager.get(task_id).payload["root"] == "other"
    assert decide(env, task_id).status.value == "done"
    assert (env.other / "a.txt").read_bytes() == b"new"


# --- deny, cancel, konflikty ---

def test_deny_and_cancel_before_execution(env):
    path = put(env.app)
    first = edit(env, [change()])["data"]["task_id"]
    assert decide(env, first, "deny").status.value == "rejected"
    second = edit(env, [change()])["data"]["task_id"]
    assert env.manager.cancel(second).status.value == "cancelled"
    assert path.read_bytes() == b"old"
    no_temps(env)


def test_external_change_after_request_fails(env):
    path = put(env.app)
    task_id = edit(env, [change()])["data"]["task_id"]
    path.write_bytes(b"external")
    done = decide(env, task_id)
    assert done.status.value == "failed" and "Konflikt" in done.error
    assert path.read_bytes() == b"external"
    no_temps(env)


def test_create_target_appeared_after_request_fails(env):
    task_id = create(env, "n.txt", "mine")["data"]["task_id"]
    path = put(env.app, "n.txt", b"external")
    done = decide(env, task_id)
    assert done.status.value == "failed" and "Konflikt" in done.error
    assert path.read_bytes() == b"external"


def test_root_removed_after_request_fails(env):
    path = put(env.app)
    task_id = edit(env, [change()])["data"]["task_id"]
    env.registry.remove("app")
    done = decide(env, task_id)
    assert done.status.value == "failed" and "Konflikt" in done.error
    assert path.read_bytes() == b"old"


def test_cancel_in_progress_rolls_back(env):
    path = put(env.app)
    entered = threading.Event()

    class SlowTx:
        def verify(self, plan):
            return env.tx.verify(plan)

        def execute(self, plan, cancel_event):
            entered.set()
            assert cancel_event.wait(5)
            return env.tx.execute(plan, cancel_event)

    task_id = edit_file_impl(env.registry, env.manager, SlowTx(), [change()])["data"]["task_id"]
    env.approval.resolve(task_id, "allow")
    assert entered.wait(5)
    task = env.manager.cancel(task_id)
    assert task.status.value == "cancelled" and task.exit_code is None
    assert path.read_bytes() == b"old"
    no_temps(env)


# --- adapter wyniku transakcji ---

def test_adapter_incomplete_rollback_fails_with_summary(env):
    tx = StubTx(tx_result(success=False, committed=False, rollback=False, published=1, error="x"))
    done = decide(env, submit_with(env, tx))
    assert done.status.value == "failed" and "Niepełny rollback" in done.error
    assert "committed=false" in done.stdout and "rollback_complete=false" in done.stdout


def test_adapter_cleanup_failure_after_commit_reports_committed(env):
    tx = StubTx(tx_result(success=False, committed=True, cleanup=False))
    done = decide(env, submit_with(env, tx))
    assert done.status.value == "failed" and "sprzątanie" in done.error
    assert "nie ponawiaj" in done.error and "committed=true" in done.stdout


def test_adapter_cancelled_result(env):
    tx = StubTx(tx_result(success=False, committed=False, cancelled=True, published=0, error="Anulowano"))
    done = decide(env, submit_with(env, tx))
    assert done.status.value == "cancelled" and done.exit_code is None


def test_adapter_conflict_result_is_failed(env):
    tx = StubTx(tx_result(success=False, committed=False, published=0,
                          error="Transakcja przerwana: konflikt lub błąd operacji plikowej."))
    done = decide(env, submit_with(env, tx))
    assert done.status.value == "failed" and "konflikt" in done.error


def test_adapter_exception_never_leaks(env):
    done = decide(env, submit_with(env, StubTx(error=RuntimeError("SENTINEL"))))
    assert done.status.value == "failed" and done.error == et.EXEC_INTERNAL_ERROR
    assert "SENTINEL" not in json.dumps(done.to_dict()) and "SENTINEL" not in sqlite_dump(env)


def test_adapter_verify_message_fails_task(env):
    tx = StubTx(tx_result(), verify="Konflikt: test.")
    done = decide(env, submit_with(env, tx))
    assert done.status.value == "failed" and done.error == "Konflikt: test."


# --- sekrety i hashe ---

def test_secret_file_never_leaks_into_task_or_sqlite(env):
    raw = b"TOKEN=oldsecret123\n"
    path = put(env.app, ".env", raw)
    result = edit(env, [change(old="oldsecret123", new="newsecret456", filepath=".env")])
    assert result["success"] is True and result["data"]["sensitive"] is True
    task = env.manager.get(result["data"]["task_id"])
    assert "ukryta zmiana" in task.payload["diff"] and "wrażliwy" in task.payload["diff"]
    hashes = [hashlib.sha256(raw).hexdigest(), hashlib.sha256(b"TOKEN=newsecret456\n").hexdigest()]
    done = decide(env, task.id)
    assert done.status.value == "done" and path.read_bytes() == b"TOKEN=newsecret456\n"
    dumped = json.dumps(done.to_dict(), ensure_ascii=False) + sqlite_dump(env) + json.dumps(result)
    for value in ("oldsecret123", "newsecret456", *hashes):
        assert value not in dumped
    no_temps(env)


def test_secret_zero_match_error_has_no_secret(env):
    put(env.app, ".env", b"TOKEN=oldsecret123\n")
    result = edit(env, [change(old="brak", new="x", filepath=".env")])
    assert result["success"] is False and "oldsecret123" not in json.dumps(result, ensure_ascii=False)
    assert no_tasks(env)


def test_create_secret_file_leaks_nothing(env):
    result = create(env, ".env", "API_KEY=supersecret789\n")
    task = env.manager.get(result["data"]["task_id"])
    assert task.sensitive is True
    dumped = json.dumps(task.to_dict(), ensure_ascii=False) + sqlite_dump(env) + json.dumps(result)
    assert "supersecret789" not in dumped
    assert hashlib.sha256(b"API_KEY=supersecret789\n").hexdigest() not in dumped


def test_plain_file_has_no_hashes_in_task(env):
    raw = b"old"
    put(env.app, raw=raw)
    task = env.manager.get(edit(env, [change()])["data"]["task_id"])
    dumped = json.dumps(task.to_dict(), ensure_ascii=False) + sqlite_dump(env)
    assert hashlib.sha256(raw).hexdigest() not in dumped
    assert hashlib.sha256(b"new").hexdigest() not in dumped


# --- sensitive ---

@pytest.mark.parametrize("name", ["main.py", "panel/x.js", "config.json", ".git/config", "tasks.db-wal"])
def test_p10_paths_are_sensitive(env, name):
    put(env.app, name, b"old")
    result = edit(env, [change(filepath=name)])
    assert result["success"] is True and result["data"]["sensitive"] is True
    task = env.manager.get(result["data"]["task_id"])
    assert task.sensitive is True and "wrażliwa" in task.approval_reason
    assert task.payload["files"][0]["sensitive_reasons"]


def test_server_repo_is_sensitive_only_for_real_server_dir(env):
    put(env.app, "server/x.py", b"old")
    plain = edit(env, [change(filepath="server/x.py")])
    assert plain["data"]["sensitive"] is False  # inny katalog o tej samej nazwie
    policy = SensitivePolicy(server_dir=env.app)
    guarded = edit(env, [change(filepath="server/x.py")], policy=policy)
    assert guarded["data"]["sensitive"] is True
    assert "repo działającego serwera" in env.manager.get(
        guarded["data"]["task_id"]).payload["files"][0]["sensitive_reasons"]


def test_sensitive_if_any_file_is_sensitive(env):
    put(env.app)
    put(env.other, "main.py", b"old")
    result = edit(env, [change(), change(filepath="main.py", root="other")])
    assert result["data"]["sensitive"] is True


# --- kolejka i panel ---

def test_queue_full(env):
    for index in range(3):
        assert create(env, f"n{index}.txt", "x")["success"] is True
    result = create(env, "extra.txt", "x")
    assert result["success"] is False and "pełna" in result["error"]


def test_panel_unavailable_reports_error(env):
    env.approval.set_notifier(lambda task: env.approval.resolve(task.id, "deny"))
    put(env.app)
    result = edit(env, [change()])
    assert result["success"] is False and "panel" in result["error"]
    assert result["data"]["status"] == "rejected"
    assert (env.app / "a.txt").read_bytes() == b"old"


def test_payload_marks_truncated_diff(env):
    put(env.app, raw=b"old")
    result = edit(env, [change(new="x\n" * 300000)])  # ok. 600 KB, ponad budżet generowania diffu
    assert result["success"] is True and result["data"]["truncated"] is True
    task = env.manager.get(result["data"]["task_id"])
    assert task.payload["truncated"] is True and task.payload["files"][0]["truncated"] is True
    assert "skrócony" in task.payload["diff"]
    assert len(task.payload["diff"].encode("utf-8")) < 210 * 1024
