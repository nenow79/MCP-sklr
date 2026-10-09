#!/usr/bin/env python3
"""Testy TaskStore (SQLite) i modeli zadań."""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from tasks.models import ACTIVE_STATUSES, TERMINAL_STATUSES, TaskStatus
from tasks.store import TaskStore


@pytest.fixture
def store(tmp_path):
    s = TaskStore(str(tmp_path / "tasks.db"))
    yield s
    s.close()


def _old(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_status_sets():
    assert len(TaskStatus) == 9
    assert ACTIVE_STATUSES | TERMINAL_STATUSES == set(TaskStatus)
    assert not (ACTIVE_STATUSES & TERMINAL_STATUSES)
    assert TaskStatus.DONE in TERMINAL_STATUSES
    assert TaskStatus.RUNNING in ACTIVE_STATUSES


def test_create_and_get_roundtrip(store):
    t = store.create(skill="hello-skill", source="central", script="hello.py",
                     args=["--name", "Zażółć"], command=["python", "hello.py"],
                     timeout=30, offer_trust=True, skill_dir="C:\\skills\\hello",
                     script_preview="print(1)")
    assert t.id == 1
    assert t.skill_dir == "C:\\skills\\hello" and t.script_preview == "print(1)"
    assert t.status == TaskStatus.QUEUED
    assert t.args == ["--name", "Zażółć"]
    assert t.command == ["python", "hello.py"]
    assert t.offer_trust is True
    assert t.stdout_truncated is False
    assert t.exit_code is None
    assert store.get(t.id) == t
    assert store.get(999) is None


def test_ids_increase(store):
    ids = [store.create(skill="a").id for _ in range(3)]
    assert ids == [1, 2, 3]


def test_update(store):
    t = store.create(skill="a", status=TaskStatus.AWAITING_APPROVAL)
    u = store.update(t.id, status=TaskStatus.DONE, exit_code=0, stdout="ok",
                     stdout_truncated=True, duration_s=1.5)
    assert u.status == TaskStatus.DONE
    assert u.is_terminal
    assert (u.exit_code, u.stdout, u.stdout_truncated, u.duration_s) == (0, "ok", True, 1.5)


def test_update_errors(store):
    t = store.create(skill="a")
    with pytest.raises(ValueError):
        store.update(t.id, bogus=1)
    with pytest.raises(ValueError):
        store.create(bogus=1)
    with pytest.raises(KeyError):
        store.update(999, status=TaskStatus.DONE)


def test_list_order_filter_limit(store):
    store.create(skill="a", status=TaskStatus.DONE)
    store.create(skill="b", status=TaskStatus.FAILED)
    store.create(skill="c", status=TaskStatus.DONE)
    assert [t.skill for t in store.list()] == ["c", "b", "a"]
    assert [t.skill for t in store.list(status=TaskStatus.DONE)] == ["c", "a"]
    assert [t.skill for t in store.list(limit=2)] == ["c", "b"]
    assert store.list(status="failed")[0].skill == "b"


def test_summary_and_dict(store):
    t = store.create(skill="a", stdout="x", stderr="y", command=["p"])
    d = t.to_dict()
    assert d["status"] == "queued" and d["stdout"] == "x"
    s = t.to_summary()
    assert "stdout" not in s and "stderr" not in s and "command" not in s
    assert s["id"] == t.id and s["status"] == "queued"


def test_purge_by_age(store):
    old = store.create(skill="old", status=TaskStatus.DONE, created_at=_old(40))
    new = store.create(skill="new", status=TaskStatus.DONE)
    assert store.purge(retention_days=30, retention_max=500) == 1
    assert store.get(old.id) is None
    assert store.get(new.id) is not None


def test_purge_by_count(store):
    ids = [store.create(skill=str(i), status=TaskStatus.DONE).id for i in range(5)]
    assert store.purge(retention_days=30, retention_max=2) == 3
    assert [t.id for t in store.list(limit=10)] == [ids[4], ids[3]]


def test_purge_keeps_active(store):
    a = store.create(skill="a", status=TaskStatus.RUNNING, created_at=_old(100))
    b = store.create(skill="b", status=TaskStatus.AWAITING_APPROVAL, created_at=_old(100))
    c = store.create(skill="c", status=TaskStatus.QUEUED, created_at=_old(100))
    store.create(skill="d", status=TaskStatus.DONE)
    assert store.purge(retention_days=30, retention_max=0) == 1
    assert {t.id for t in store.list()} == {a.id, b.id, c.id}


def test_mark_interrupted(store):
    a = store.create(skill="a", status=TaskStatus.AWAITING_APPROVAL)
    b = store.create(skill="b", status=TaskStatus.QUEUED)
    c = store.create(skill="c", status=TaskStatus.RUNNING)
    d = store.create(skill="d", status=TaskStatus.DONE, exit_code=0)
    e = store.create(skill="e", status=TaskStatus.CANCELLED)
    assert store.mark_interrupted() == 3
    for t in (a, b, c):
        cur = store.get(t.id)
        assert cur.status == TaskStatus.INTERRUPTED
        assert cur.finished_at
        assert cur.error
    assert store.get(d.id).status == TaskStatus.DONE
    assert store.get(e.id).status == TaskStatus.CANCELLED
    assert store.mark_interrupted() == 0


def test_persistence_across_reopen(tmp_path):
    path = str(tmp_path / "tasks.db")
    s1 = TaskStore(path)
    t = s1.create(skill="a", args=["x"], status=TaskStatus.RUNNING)
    s1.close()
    s2 = TaskStore(path)
    try:
        got = s2.get(t.id)
        assert got.args == ["x"]
        assert s2.mark_interrupted() == 1
        assert s2.create(skill="b").id == t.id + 1
    finally:
        s2.close()


def test_in_memory_store():
    s = TaskStore(":memory:")
    try:
        assert s.create(skill="a").id == 1
    finally:
        s.close()


def test_concurrent_create(store):
    ids, errors = [], []
    lock = threading.Lock()

    def worker():
        try:
            for _ in range(25):
                t = store.create(skill="w")
                with lock:
                    ids.append(t.id)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    assert len(ids) == 200 and len(set(ids)) == 200
    assert len(store.list(limit=1000)) == 200


def test_concurrent_update(store):
    tasks = [store.create(skill="u") for _ in range(8)]
    errors = []

    def worker(task):
        try:
            for i in range(25):
                store.update(task.id, stdout=str(i))
            store.update(task.id, status=TaskStatus.DONE, exit_code=0)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in tasks]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    for t in tasks:
        cur = store.get(t.id)
        assert cur.status == TaskStatus.DONE and cur.stdout == "24"
