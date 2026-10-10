"""Testy tasks/expiry.py: wygaszanie zadań awaiting_approval."""
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from tasks.approval import ApprovalService
from tasks.expiry import ExpiryWatcher, expire_overdue
from tasks.manager import TaskManager
from tasks.store import TaskStore


@pytest.fixture
def env(tmp_path):
    store = TaskStore(str(tmp_path / "expiry.db"))
    approval = ApprovalService()
    manager = TaskManager(store, approval, max_parallel=2, max_queued=50, max_wait_s=8)

    def submit(needs_approval=True, executor=None):
        def default_executor(cancel_event):
            return {"exit_code": 0, "stdout": "ok", "stderr": "", "duration_s": 0.01}

        fields = dict(skill="demo", source="central", script="scripts/run.py", args=[],
                      timeout=30)
        return manager.submit(fields, executor or default_executor, needs_approval=needs_approval)

    class Env:
        pass

    e = Env()
    e.store, e.manager, e.approval, e.submit = store, manager, approval, submit
    yield e
    manager.shutdown()
    store.close()


def future(seconds):
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def test_overdue_task_is_rejected_with_timeout(env):
    t = env.submit()
    expired = expire_overdue(env.manager, 60, now=future(61))
    assert expired == [t.id]
    task = env.manager.get(t.id)
    assert task.status.value == "rejected"
    assert task.decision == "timeout"
    assert "Brak odpowiedzi" in task.error


def test_young_task_untouched(env):
    t = env.submit()
    assert expire_overdue(env.manager, 60, now=future(30)) == []
    assert env.manager.get(t.id).status.value == "awaiting_approval"


def test_boundary_age_expires(env):
    t = env.submit()
    created = datetime.fromisoformat(env.manager.get(t.id).created_at)
    assert expire_overdue(env.manager, 60, now=created + timedelta(seconds=60)) == [t.id]


def test_other_states_untouched(env):
    done = env.submit(needs_approval=False)
    env.manager.wait(done.id, 5)
    queued_blocker = threading.Event()
    running_started = threading.Event()

    def blocking(cancel_event):
        running_started.set()
        queued_blocker.wait(10)
        return {"exit_code": 0}

    running = env.submit(needs_approval=False, executor=blocking)
    assert running_started.wait(5)
    cancelled = env.submit()
    env.manager.cancel(cancelled.id)
    try:
        assert expire_overdue(env.manager, 1, now=future(3600)) == []
        assert env.manager.get(done.id).status.value == "done"
        assert env.manager.get(running.id).status.value == "running"
        assert env.manager.get(cancelled.id).status.value == "cancelled"
    finally:
        queued_blocker.set()


def test_only_overdue_among_many(env):
    old = env.submit()
    # symulacja: młodsze zadanie = 'now' tuż po utworzeniu starego, więc wiek ustawiamy przez now
    created_old = datetime.fromisoformat(env.manager.get(old.id).created_at)
    time.sleep(0.05)
    young = env.submit()
    created_young = datetime.fromisoformat(env.manager.get(young.id).created_at)
    now = created_old + timedelta(seconds=100)
    assert created_young > created_old
    expired = expire_overdue(env.manager, 100, now=now)
    assert old.id in expired
    assert young.id not in expired
    assert env.manager.get(young.id).status.value == "awaiting_approval"


def test_disabled_when_limit_not_positive(env):
    t = env.submit()
    assert expire_overdue(env.manager, 0, now=future(10 ** 6)) == []
    assert expire_overdue(env.manager, -5, now=future(10 ** 6)) == []
    assert env.manager.get(t.id).status.value == "awaiting_approval"


def test_race_with_user_decision_is_ignored(env, monkeypatch):
    t = env.submit()
    real_list = env.manager.list

    def list_then_decide(status=None, limit=20):
        tasks = real_list(status, limit)
        env.manager.resolve_approval(t.id, "deny")  # użytkownik zdążył przed strażnikiem
        return tasks

    monkeypatch.setattr(env.manager, "list", list_then_decide)
    assert expire_overdue(env.manager, 1, now=future(3600)) == []
    task = env.manager.get(t.id)
    assert task.status.value == "rejected" and task.decision == "deny"


def test_invalid_created_at_skipped(env, monkeypatch):
    t = env.submit()
    env.store.update(t.id, created_at="nie-data")
    assert expire_overdue(env.manager, 1, now=future(3600)) == []
    assert env.manager.get(t.id).status.value == "awaiting_approval"


def test_naive_created_at_treated_as_utc(env):
    t = env.submit()
    env.store.update(t.id, created_at="2000-01-01T00:00:00")
    assert expire_overdue(env.manager, 60) == [t.id]


def test_watcher_expires_task_and_stops(env):
    t = env.submit()
    env.store.update(t.id, created_at=(datetime.now(timezone.utc)
                                       - timedelta(hours=2)).isoformat())
    watcher = ExpiryWatcher(env.manager, max_age_s=60, interval_s=0.05)
    watcher.start()
    try:
        assert watcher.running
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if env.manager.get(t.id).status.value == "rejected":
                break
            time.sleep(0.05)
        assert env.manager.get(t.id).status.value == "rejected"
    finally:
        watcher.stop()
    assert not watcher.running


def test_watcher_not_started_when_disabled(env):
    watcher = ExpiryWatcher(env.manager, max_age_s=0, interval_s=0.05)
    watcher.start()
    assert not watcher.running
    watcher.stop()


def test_watcher_survives_errors(env, monkeypatch):
    calls = {"n": 0}

    def boom(manager, max_age_s, now=None):
        calls["n"] += 1
        raise RuntimeError("awaria")

    monkeypatch.setattr("tasks.expiry.expire_overdue", boom)
    watcher = ExpiryWatcher(env.manager, max_age_s=60, interval_s=0.05)
    watcher.start()
    try:
        deadline = time.monotonic() + 5
        while calls["n"] < 3 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert calls["n"] >= 3
        assert watcher.running
    finally:
        watcher.stop()
