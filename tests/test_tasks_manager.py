#!/usr/bin/env python3
"""Testy TaskManager i ApprovalService (atrapy executora, bez prawdziwych procesów)."""
import threading
import time

import pytest

from tasks.approval import ApprovalService
from tasks.manager import QueueFullError, TaskManager, TaskNotFoundError, TaskStateError
from tasks.models import TaskStatus
from tasks.store import TaskStore

OK = {"started": True, "exit_code": 0, "stdout": "out", "stderr": "",
      "stdout_truncated": False, "stderr_truncated": False, "duration_s": 0.1,
      "timed_out": False, "cancelled": False}

FIELDS = dict(skill="s", source="central", script="a.py", args=[], timeout=10,
              command=["py", "a.py"], skill_hash="h", approval_reason="r", offer_trust=False)


def _until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _executor(result=None, block=None, started=None, calls=None):
    """Atrapa: opcjonalnie blokuje do block.set(); reaguje na cancel_event."""
    def run(cancel_event):
        if calls is not None:
            calls.append(1)
        if started is not None:
            started.set()
        if block is not None:
            while not block.is_set():
                if cancel_event.is_set():
                    return {"started": True, "cancelled": True, "timed_out": False,
                            "exit_code": -1, "stdout": "partial", "stderr": ""}
                time.sleep(0.01)
        return dict(result or OK)
    return run


@pytest.fixture
def make_manager(tmp_path):
    created = []

    def factory(notifier=None, **kwargs):
        store = TaskStore(str(tmp_path / f"t{len(created)}.db"))
        approval = ApprovalService(notifier=notifier)
        kwargs.setdefault("max_wait_s", 5)
        mgr = TaskManager(store, approval, **kwargs)
        created.append((mgr, store))
        return mgr, approval

    yield factory
    for mgr, store in created:
        mgr.shutdown()
        # shutdown intentionally does not join running work. Tests must not
        # close SQLite underneath the preceding test's finalization, or its
        # traceback can contaminate the next test's confidentiality assertions.
        for worker in mgr._workers:
            worker.join(timeout=5)
        assert not any(worker.is_alive() for worker in mgr._workers)
        store.close()


@pytest.fixture
def mgr(make_manager):
    return make_manager(max_parallel=2, max_queued=3)[0]


# --- zakończenia ---
def test_quick_task_done(mgr):
    t = mgr.submit(FIELDS, _executor(), needs_approval=False)
    done = mgr.wait(t.id, 5)
    assert done.status == TaskStatus.DONE
    assert done.exit_code == 0 and done.stdout == "out" and done.duration_s == 0.1
    assert done.started_at and done.finished_at


def test_nonzero_exit_failed(mgr):
    t = mgr.submit(FIELDS, _executor({**OK, "exit_code": 3}), False)
    done = mgr.wait(t.id, 5)
    assert done.status == TaskStatus.FAILED and done.exit_code == 3


def test_not_started_failed(mgr):
    t = mgr.submit(FIELDS, _executor({"started": False, "error": "boom"}), False)
    done = mgr.wait(t.id, 5)
    assert done.status == TaskStatus.FAILED and done.error == "boom"


def test_timed_out(mgr):
    t = mgr.submit(FIELDS, _executor({**OK, "exit_code": -1, "timed_out": True}), False)
    assert mgr.wait(t.id, 5).status == TaskStatus.TIMED_OUT


def test_executor_exception_does_not_kill_worker(mgr):
    def bad(_event):
        raise RuntimeError("kaboom")
    t = mgr.submit(FIELDS, bad, False)
    done = mgr.wait(t.id, 5)
    assert done.status == TaskStatus.FAILED and "kaboom" in done.error
    t2 = mgr.submit(FIELDS, _executor(), False)
    assert mgr.wait(t2.id, 5).status == TaskStatus.DONE


# --- zatwierdzanie ---
def test_approval_allow(make_manager):
    seen = []
    mgr, approval = make_manager(notifier=seen.append)
    calls = []
    t = mgr.submit(FIELDS, _executor(calls=calls), needs_approval=True)
    assert t.status == TaskStatus.AWAITING_APPROVAL
    assert [x.id for x in seen] == [t.id]
    time.sleep(0.1)
    assert calls == []
    approval.resolve(t.id, "allow")
    done = mgr.wait(t.id, 5)
    assert done.status == TaskStatus.DONE
    assert done.decision == "allow" and done.decided_at


def test_trust_calls_on_trust_only_when_offered(make_manager):
    mgr, approval = make_manager()
    trusted = []
    # trust + offer_trust -> on_trust
    t1 = mgr.submit({**FIELDS, "offer_trust": True}, _executor(), True,
                    on_trust=lambda: trusted.append("t1"))
    approval.resolve(t1.id, "trust")
    assert mgr.wait(t1.id, 5).status == TaskStatus.DONE
    # allow -> brak on_trust
    t2 = mgr.submit({**FIELDS, "offer_trust": True}, _executor(), True,
                    on_trust=lambda: trusted.append("t2"))
    approval.resolve(t2.id, "allow")
    assert mgr.wait(t2.id, 5).status == TaskStatus.DONE
    # trust bez offer_trust -> traktowane jak allow
    t3 = mgr.submit(FIELDS, _executor(), True, on_trust=lambda: trusted.append("t3"))
    approval.resolve(t3.id, "trust")
    done3 = mgr.wait(t3.id, 5)
    assert done3.status == TaskStatus.DONE and done3.decision == "allow"
    assert trusted == ["t1"]


def test_deny_and_timeout_reject(make_manager):
    mgr, approval = make_manager()
    calls = []
    t1 = mgr.submit(FIELDS, _executor(calls=calls), True)
    t2 = mgr.submit(FIELDS, _executor(calls=calls), True)
    r1 = approval.resolve(t1.id, "deny")
    r2 = approval.resolve(t2.id, "timeout")
    assert r1.status == TaskStatus.REJECTED and r1.decision == "deny"
    assert r2.status == TaskStatus.REJECTED and "Brak odpowiedzi" in r2.error
    assert r1.finished_at and r2.finished_at
    time.sleep(0.1)
    assert calls == []


def test_resolve_state_errors(make_manager):
    mgr, approval = make_manager()
    done = mgr.submit(FIELDS, _executor(), False)
    mgr.wait(done.id, 5)
    with pytest.raises(TaskStateError):
        approval.resolve(done.id, "allow")
    with pytest.raises(TaskNotFoundError):
        approval.resolve(999, "allow")
    with pytest.raises(ValueError):
        approval.resolve(done.id, "maybe")


def test_approval_service_unbound():
    with pytest.raises(RuntimeError):
        ApprovalService().resolve(1, "allow")


def test_notifier_error_does_not_break_task(make_manager):
    def boom(_task):
        raise RuntimeError("ui down")
    mgr, approval = make_manager(notifier=boom)
    t = mgr.submit(FIELDS, _executor(), True)
    assert t.status == TaskStatus.AWAITING_APPROVAL
    approval.resolve(t.id, "allow")
    assert mgr.wait(t.id, 5).status == TaskStatus.DONE


def test_verify_failure_blocks_execution(make_manager):
    mgr, approval = make_manager()
    calls, trusted = [], []
    t = mgr.submit({**FIELDS, "offer_trust": True}, _executor(calls=calls), True,
                   verify=lambda: "Skill zmienił się.", on_trust=lambda: trusted.append(1))
    approval.resolve(t.id, "trust")
    done = mgr.wait(t.id, 5)
    assert done.status == TaskStatus.FAILED and "zmienił" in done.error
    assert calls == [] and trusted == []


def test_verify_ok_runs(mgr):
    t = mgr.submit(FIELDS, _executor(), False, verify=lambda: None)
    assert mgr.wait(t.id, 5).status == TaskStatus.DONE


# --- anulowanie ---
def test_cancel_awaiting(make_manager):
    mgr, approval = make_manager()
    calls = []
    t = mgr.submit(FIELDS, _executor(calls=calls), True)
    assert mgr.cancel(t.id).status == TaskStatus.CANCELLED
    with pytest.raises(TaskStateError):
        approval.resolve(t.id, "allow")
    assert calls == []


def test_cancel_queued_is_skipped_by_worker(make_manager):
    mgr, _ = make_manager(max_parallel=1, max_queued=5)
    block, started1, calls2 = threading.Event(), threading.Event(), []
    t1 = mgr.submit(FIELDS, _executor(block=block, started=started1), False)
    assert started1.wait(5)
    t2 = mgr.submit(FIELDS, _executor(calls=calls2), False)
    assert mgr.get(t2.id).status == TaskStatus.QUEUED
    assert mgr.cancel(t2.id).status == TaskStatus.CANCELLED
    block.set()
    assert mgr.wait(t1.id, 5).status == TaskStatus.DONE
    t3 = mgr.submit(FIELDS, _executor(), False)  # worker przeszedł obok anulowanego
    assert mgr.wait(t3.id, 5).status == TaskStatus.DONE
    assert calls2 == []
    assert mgr.get(t2.id).status == TaskStatus.CANCELLED


def test_cancel_running(mgr):
    block, started = threading.Event(), threading.Event()
    t = mgr.submit(FIELDS, _executor(block=block, started=started), False)
    assert started.wait(5)
    assert mgr.get(t.id).status == TaskStatus.RUNNING
    cancelled = mgr.cancel(t.id)
    assert cancelled.status == TaskStatus.CANCELLED
    assert cancelled.stdout == "partial"


def test_cancel_terminal_unchanged(mgr):
    t = mgr.submit(FIELDS, _executor(), False)
    mgr.wait(t.id, 5)
    assert mgr.cancel(t.id).status == TaskStatus.DONE


def test_unknown_task(mgr):
    assert mgr.get(999) is None
    with pytest.raises(TaskNotFoundError):
        mgr.cancel(999)
    with pytest.raises(TaskNotFoundError):
        mgr.wait(999, 1)


# --- równoległość i limity ---
def test_parallel_limit(make_manager):
    mgr, _ = make_manager(max_parallel=2, max_queued=10)
    blocks = [threading.Event() for _ in range(3)]
    started = [threading.Event() for _ in range(3)]
    tasks = [mgr.submit(FIELDS, _executor(block=blocks[i], started=started[i]), False)
             for i in range(3)]
    assert started[0].wait(5) and started[1].wait(5)
    time.sleep(0.2)
    assert not started[2].is_set()
    assert mgr.get(tasks[2].id).status == TaskStatus.QUEUED
    blocks[0].set()
    assert started[2].wait(5)
    for b in blocks:
        b.set()
    for t in tasks:
        assert mgr.wait(t.id, 5).status == TaskStatus.DONE


def test_queue_full(make_manager):
    mgr, _ = make_manager(max_parallel=1, max_queued=2)
    block, started = threading.Event(), threading.Event()
    mgr.submit(FIELDS, _executor(block=block, started=started), False)
    assert started.wait(5)
    mgr.submit(FIELDS, _executor(), False)
    mgr.submit(FIELDS, _executor(), False)
    with pytest.raises(QueueFullError):
        mgr.submit(FIELDS, _executor(), False)
    block.set()


def test_queue_full_counts_awaiting(make_manager):
    mgr, _ = make_manager(max_queued=2)
    mgr.submit(FIELDS, _executor(), True)
    mgr.submit(FIELDS, _executor(), True)
    with pytest.raises(QueueFullError):
        mgr.submit(FIELDS, _executor(), True)


# --- wait ---
def test_wait_returns_when_terminal(mgr):
    block, started = threading.Event(), threading.Event()
    t = mgr.submit(FIELDS, _executor(block=block, started=started), False)
    assert started.wait(5)
    threading.Timer(0.3, block.set).start()
    begin = time.monotonic()
    done = mgr.wait(t.id, 5)
    assert done.status == TaskStatus.DONE
    assert time.monotonic() - begin < 3


def test_wait_timeout_returns_active_task(make_manager):
    mgr, _ = make_manager(max_wait_s=1)
    block, started = threading.Event(), threading.Event()
    t = mgr.submit(FIELDS, _executor(block=block, started=started), False)
    assert started.wait(5)
    begin = time.monotonic()
    task = mgr.wait(t.id, 30)  # obcięte do max_wait_s=1
    elapsed = time.monotonic() - begin
    assert task.status == TaskStatus.RUNNING
    assert 0.8 <= elapsed < 3
    block.set()


def test_submit_after_shutdown(mgr):
    mgr.shutdown()
    with pytest.raises(TaskStateError):
        mgr.submit(FIELDS, _executor(), False)


# --- C1: poufne wyniki poza Task/SQLite ---
from tasks.volatile_results import VolatileResult, VolatileResultStore

PRIVATE = "SENTINEL-MANAGER-SECRET"
SECRET_FIELDS = dict(kind="secret_reveal", sensitive=True, offer_trust=False,
                     title="Ujawnij testowy klucz", payload={"key": "DEMO_TOKEN"})


def secret_executor(check=None):
    def run(_event):
        return VolatileResult({"content": PRIVATE, "key": "DEMO_TOKEN"}, check)
    return run


def allow_secret(manager, approval, executor=None, **kwargs):
    task = manager.submit(SECRET_FIELDS, executor or secret_executor(), True, **kwargs)
    approval.resolve(task.id, "allow")
    return manager.wait(task.id, 5)


def assert_no_private(manager, task_id, caplog):
    task = manager.get(task_id)
    assert PRIVATE not in repr(task.to_dict())
    assert PRIVATE not in repr([t.to_dict() for t in manager.list()])
    assert PRIVATE not in repr(manager.wait(task_id, 0).to_dict())
    with manager._store._lock:
        rows = manager._store._conn.execute("SELECT * FROM tasks").fetchall()
        dump = "\n".join(manager._store._conn.iterdump())
    assert PRIVATE not in repr([tuple(row) for row in rows]) + dump + caplog.text
    assert all(record.exc_info is None for record in caplog.records
               if record.name == "tasks.manager")


def test_secret_success_only_in_separate_channel(make_manager, caplog):
    manager, approval = make_manager()
    pending = manager.submit(SECRET_FIELDS, secret_executor(), True)
    assert manager.get_volatile_result(pending.id) == {"state": "unavailable"}
    approval.resolve(pending.id, "trust")  # brak Ufaj degraduje do allow
    done = manager.wait(pending.id, 5)
    assert done.status == TaskStatus.DONE and done.exit_code == 0
    assert done.decision == "allow" and done.stdout and not done.stderr
    for _ in range(2):
        assert manager.get_volatile_result(done.id)["data"]["content"] == PRIVATE
    assert manager.cancel(done.id).status == TaskStatus.DONE
    assert_no_private(manager, done.id, caplog)


@pytest.mark.parametrize("changes,needs,on_trust", [
    ({}, False, None), ({"sensitive": False}, True, None),
    ({"sensitive": 1}, True, None), ({"offer_trust": True}, True, None),
    ({"offer_trust": None}, True, None), ({}, True, lambda: None),
])
def test_secret_bad_submit_contract_no_task(make_manager, changes, needs, on_trust):
    manager, _ = make_manager()
    with pytest.raises(TaskStateError):
        manager.submit({**SECRET_FIELDS, **changes}, secret_executor(), needs,
                       on_trust=on_trust)
    assert manager.list() == []
    assert not manager._volatile._entries


def test_volatile_result_from_wrong_kind_failed(make_manager, caplog):
    manager, _ = make_manager()
    task = manager.submit(FIELDS, secret_executor(), False)
    assert manager.wait(task.id, 5).status == TaskStatus.FAILED
    assert manager.get_volatile_result(task.id) == {"state": "unavailable"}
    assert_no_private(manager, task.id, caplog)


@pytest.mark.parametrize("decision", ["deny", "timeout", "cancel"])
def test_secret_rejected_or_cancelled_never_executed(make_manager, decision):
    manager, approval = make_manager()
    calls = []
    def run(_event):
        calls.append(1)
        return VolatileResult({"content": PRIVATE})
    task = manager.submit(SECRET_FIELDS, run, True)
    if decision == "cancel":
        manager.cancel(task.id)
    else:
        approval.resolve(task.id, decision)
    assert calls == []
    assert manager.get_volatile_result(task.id) == {"state": "unavailable"}


@pytest.mark.parametrize("source", ["verify_message", "verify_exception", "executor_exception", "dict"])
def test_secret_errors_do_not_persist_or_log_private(make_manager, caplog, source):
    manager, approval = make_manager()
    def fail():
        raise RuntimeError(PRIVATE)
    verify = (lambda: PRIVATE) if source == "verify_message" else fail if source == "verify_exception" else None
    executor = secret_executor()
    if source == "executor_exception":
        executor = lambda _event: fail()
    elif source == "dict":
        executor = lambda _event: {"exit_code": 0, "stdout": PRIVATE,
                                  "stderr": PRIVATE, "error": PRIVATE}
    done = allow_secret(manager, approval, executor, verify=verify)
    assert done.status == TaskStatus.FAILED
    assert manager.get_volatile_result(done.id) == {"state": "unavailable"}
    assert_no_private(manager, done.id, caplog)


def test_secret_cancel_running_before_publication(make_manager, caplog):
    manager, approval = make_manager()
    entered = threading.Event()
    def run(event):
        entered.set()
        assert event.wait(5)
        return VolatileResult({"content": PRIVATE})
    task = manager.submit(SECRET_FIELDS, run, True)
    approval.resolve(task.id, "allow")
    assert entered.wait(5)
    assert manager.cancel(task.id).status == TaskStatus.CANCELLED
    assert manager.get_volatile_result(task.id) == {"state": "unavailable"}
    assert_no_private(manager, task.id, caplog)


def test_secret_callback_outside_manager_lock_and_access_revoked(make_manager):
    manager, approval = make_manager()
    acquired = []
    allowed = [True]
    def check():
        def contender():
            got = manager._cond.acquire(blocking=False)
            acquired.append(got)
            if got:
                manager._cond.release()
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
        return None if allowed[0] else "no access"
    done = allow_secret(manager, approval, secret_executor(check))
    assert manager.get_volatile_result(done.id)["state"] == "available"
    assert acquired == [True]
    allowed[0] = False
    assert manager.get_volatile_result(done.id) == {"state": "denied"}
    assert manager.get_volatile_result(done.id) == {"state": "unavailable"}


def test_secret_ttl_and_restart_without_result(make_manager):
    now = [100.0]
    volatile = VolatileResultStore(ttl_seconds=5, clock=lambda: now[0])
    manager, approval = make_manager(volatile_store=volatile)
    done = allow_secret(manager, approval)
    now[0] += 4
    assert manager.get_volatile_result(done.id)["state"] == "available"
    now[0] += 1
    assert manager.get_volatile_result(done.id) == {"state": "unavailable"}
    second = allow_secret(manager, approval)
    manager.shutdown()
    fresh_approval = ApprovalService()
    fresh = TaskManager(manager._store, fresh_approval)
    try:
        assert fresh.get(second.id).status == TaskStatus.DONE
        assert fresh.get_volatile_result(second.id) == {"state": "unavailable"}
    finally:
        fresh.shutdown()


@pytest.mark.parametrize("failure", ["done", "activate", "full"])
def test_secret_finalization_failure_discards_result(make_manager, monkeypatch, caplog, failure):
    volatile = VolatileResultStore(max_results=1)
    manager, approval = make_manager(volatile_store=volatile)
    if failure == "full":
        allow_secret(manager, approval)
    elif failure == "activate":
        def fail(*_args):
            raise RuntimeError(PRIVATE)
        monkeypatch.setattr(volatile, "activate", fail)
    else:
        original = manager._store.update
        def update(task_id, **fields):
            if fields.get("status") == TaskStatus.DONE:
                raise RuntimeError(PRIVATE)
            return original(task_id, **fields)
        monkeypatch.setattr(manager._store, "update", update)
    done = allow_secret(manager, approval)
    assert done.status == TaskStatus.FAILED and done.exit_code is None
    assert manager.get_volatile_result(done.id) == {"state": "unavailable"}
    assert done.id not in volatile._entries
    assert_no_private(manager, done.id, caplog)


def test_secret_shutdown_blocks_late_publication(make_manager, caplog):
    manager, approval = make_manager()
    entered, release = threading.Event(), threading.Event()
    def run(_event):
        entered.set()
        assert release.wait(5)
        return VolatileResult({"content": PRIVATE})
    task = manager.submit(SECRET_FIELDS, run, True)
    approval.resolve(task.id, "allow")
    assert entered.wait(5)
    manager.shutdown()
    release.set()
    assert manager.wait(task.id, 5).status == TaskStatus.FAILED
    assert manager.get_volatile_result(task.id) == {"state": "unavailable"}
    assert not manager._volatile._entries
    assert_no_private(manager, task.id, caplog)


def test_secret_shutdown_during_access_check_no_result(make_manager):
    manager, approval = make_manager()
    done = allow_secret(manager, approval, secret_executor(manager.shutdown))
    assert manager.get_volatile_result(done.id) == {"state": "unavailable"}


@pytest.mark.parametrize("task_id", [True, 0, -1, "1", None, 999])
def test_volatile_get_invalid_or_missing_id(make_manager, task_id):
    manager, _ = make_manager()
    assert manager.get_volatile_result(task_id) == {"state": "unavailable"}
