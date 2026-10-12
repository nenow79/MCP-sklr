#!/usr/bin/env python3
"""Testy fazy 5.0: kind/title/payload/sensitive, migracja bazy, exit_code=None."""
import sqlite3
import threading
import time

import pytest

from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.models import TaskStatus
from tasks.store import SCHEMA_VERSION, TaskStore

OK = {"started": True, "exit_code": 0, "stdout": "out", "stderr": "",
      "stdout_truncated": False, "stderr_truncated": False, "duration_s": 0.1,
      "timed_out": False, "cancelled": False}

SKILL_FIELDS = dict(skill="s", source="central", script="a.py", args=[], timeout=10,
                    command=["py", "a.py"], skill_hash="h", approval_reason="r", offer_trust=False)

OLD_SCHEMA = """
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT NOT NULL,
    skill TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    script TEXT NOT NULL DEFAULT '',
    skill_dir TEXT NOT NULL DEFAULT '',
    script_preview TEXT NOT NULL DEFAULT '',
    args TEXT NOT NULL DEFAULT '[]',
    timeout INTEGER NOT NULL DEFAULT 60,
    command TEXT NOT NULL DEFAULT '[]',
    skill_hash TEXT NOT NULL DEFAULT '',
    approval_reason TEXT NOT NULL DEFAULT '',
    offer_trust INTEGER NOT NULL DEFAULT 0,
    decision TEXT NOT NULL DEFAULT '',
    decided_at TEXT,
    exit_code INTEGER,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    stdout_truncated INTEGER NOT NULL DEFAULT 0,
    stderr_truncated INTEGER NOT NULL DEFAULT 0,
    duration_s REAL,
    error TEXT NOT NULL DEFAULT ''
);
"""


def _executor(result=None, block=None):
    def run(cancel_event):
        if block is not None:
            while not block.is_set():
                if cancel_event.is_set():
                    return {"started": True, "cancelled": True, "timed_out": False,
                            "exit_code": 1, "stdout": "partial", "stderr": ""}
                time.sleep(0.01)
        return dict(result or OK)
    return run


@pytest.fixture
def parts(tmp_path):
    store = TaskStore(str(tmp_path / "t.db"))
    approval = ApprovalService()
    manager = TaskManager(store, approval, max_wait_s=5)
    yield manager, approval, store
    manager.shutdown()
    store.close()


# --- model i magazyn ---
def test_new_fields_have_defaults(parts):
    _, _, store = parts
    t = store.create(skill="a")
    assert (t.kind, t.title, t.payload, t.sensitive) == ("skill_script", "", {}, False)


def test_new_fields_roundtrip(parts):
    _, _, store = parts
    payload = {"root": "app", "paths": ["a.py", "b.py"], "diff": "-x\n+y", "zażółć": 1}
    t = store.create(kind="file_edit", title="Zmiana", payload=payload, sensitive=True)
    again = store.get(t.id)
    assert again.kind == "file_edit" and again.title == "Zmiana"
    assert again.payload == payload
    assert again.sensitive is True
    assert store.update(t.id, payload={"a": 1}).payload == {"a": 1}


def test_summary_excludes_payload_but_dict_keeps_it(parts):
    _, _, store = parts
    t = store.create(kind="file_edit", payload={"diff": "x"})
    assert "payload" not in t.to_summary()
    assert t.to_dict()["payload"] == {"diff": "x"}
    assert t.to_summary()["kind"] == "file_edit"


def test_migration_of_old_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO tasks (created_at, status, skill, script, args) VALUES (?, ?, ?, ?, ?)",
        ("2026-01-01T00:00:00+00:00", "done", "hello-skill", "hello.py", '["x"]'))
    conn.commit()
    conn.close()

    store = TaskStore(str(path))
    try:
        old = store.get(1)
        assert old.skill == "hello-skill" and old.args == ["x"]
        assert (old.kind, old.title, old.payload, old.sensitive) == ("skill_script", "", {}, False)
        new = store.create(kind="command", title="git status", payload={"cwd": "."})
        assert store.get(new.id).payload == {"cwd": "."}
        assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        store.close()
    # ponowne otwarcie nie może się wywrócić (kolumny już istnieją)
    again = TaskStore(str(path))
    try:
        assert len(again.list(limit=10)) == 2
    finally:
        again.close()


# --- exit_code ---
def test_cancelled_exit_code_is_none(parts):
    manager, _, _ = parts
    block = threading.Event()
    task = manager.submit(SKILL_FIELDS, _executor(block=block), needs_approval=False)
    deadline = time.monotonic() + 5
    while manager.get(task.id).status != TaskStatus.RUNNING and time.monotonic() < deadline:
        time.sleep(0.01)
    done = manager.cancel(task.id)
    assert done.status == TaskStatus.CANCELLED
    assert done.exit_code is None
    assert done.stdout == "partial"


def test_timed_out_exit_code_is_none(parts):
    manager, _, _ = parts
    task = manager.submit(SKILL_FIELDS, _executor({**OK, "exit_code": -1, "timed_out": True}), False)
    done = manager.wait(task.id, 5)
    assert done.status == TaskStatus.TIMED_OUT and done.exit_code is None


def test_failed_keeps_real_exit_code(parts):
    manager, _, _ = parts
    task = manager.submit(SKILL_FIELDS, _executor({**OK, "exit_code": 3}), False)
    done = manager.wait(task.id, 5)
    assert done.status == TaskStatus.FAILED and done.exit_code == 3


# --- sensitive ---
def test_trust_is_downgraded_for_sensitive_task(parts):
    manager, approval, _ = parts
    task = manager.submit({**SKILL_FIELDS, "offer_trust": True, "sensitive": True},
                          _executor(), needs_approval=True)
    approval.resolve(task.id, "trust")
    done = manager.wait(task.id, 5)
    assert done.decision == "allow" and done.status == TaskStatus.DONE


def test_trust_still_works_for_normal_task(parts):
    manager, approval, _ = parts
    task = manager.submit({**SKILL_FIELDS, "offer_trust": True}, _executor(), needs_approval=True)
    approval.resolve(task.id, "trust")
    assert manager.wait(task.id, 5).decision == "trust"
