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
