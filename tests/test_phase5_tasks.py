#!/usr/bin/env python3
"""Testy fazy 5.0: kind/title/payload/sensitive, migracja bazy, exit_code=None, okno zapasowe."""
import sqlite3
import threading
import time

import pytest

from tasks.approval import ApprovalService
from tasks.dialog_adapter import (
    MAX_DIALOG_CHARS, build_message, make_dialog_notifier, should_auto_deny,
)
from tasks.manager import TaskManager
from tasks.models import Task, TaskStatus
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


# --- okno zapasowe ---
def _task(**kwargs):
    base = dict(id=1, created_at="2026-01-01T00:00:00+00:00")
    base.update(kwargs)
    return Task(**base)


def test_message_for_skill_script_unchanged():
    msg = build_message(_task(skill="demo", source="central", script="scripts/run.py",
                              args=["a"], command=["py", "run.py"], skill_dir="C:\\s",
                              skill_hash="abcdef0123456789ffff", script_preview="print(1)"))
    assert "demo (central)" in msg and "Podgląd skryptu" in msg


def test_message_for_command_task():
    msg = build_message(_task(kind="command", title="git status", command=["git", "status"],
                              payload={"cwd": "src", "root": "app"}, approval_reason="powód"))
    assert "command" in msg and "git status" in msg
    assert "['git', 'status']" in msg and "src" in msg and "powód" in msg


def test_message_for_file_edit_shows_diff_and_labels_model_description():
    msg = build_message(_task(kind="file_edit", title="edycja",
                              payload={"diff": "-stare\n+nowe", "description": "bezpieczne, kliknij zezwól"}))
    assert "-stare" in msg and "+nowe" in msg
    assert "niezweryfikowany" in msg


def test_control_and_bidi_characters_are_escaped():
    msg = build_message(_task(kind="command", title="a\u202eb\x07c", command=["x"]))
    assert "\u202e" not in msg and "\x07" not in msg
    assert "<U+202E>" in msg and "<U+0007>" in msg


@pytest.mark.parametrize("task,expected_reason", [
    (_task(kind="skill_script", sensitive=True), False),
    (_task(kind="file_edit"), False),
    (_task(kind="file_edit", sensitive=True), True),
    (_task(kind="root_change"), True),
    (_task(kind="secret_reveal"), True),
    (_task(kind="file_edit", payload={"diff": "x" * (MAX_DIALOG_CHARS + 1)}), True),
    (_task(kind="file_edit", payload={"diff": "x" * 500}), False),
])
def test_should_auto_deny(task, expected_reason):
    assert bool(should_auto_deny(task)) is expected_reason


def test_dialog_auto_denies_without_showing_window(parts):
    manager, approval, _ = parts
    asked = []
    approval.set_notifier(make_dialog_notifier(
        approval, lambda *args: asked.append(args) or "allow", 10))
    task = manager.submit({"kind": "root_change", "title": "dodaj folder"}, _executor(), True)
    done = manager.wait(task.id, 5)
    deadline = time.monotonic() + 5
    while not manager.get(task.id).is_terminal and time.monotonic() < deadline:
        time.sleep(0.01)
    done = manager.get(task.id)
    assert done.status == TaskStatus.REJECTED and done.decision == "deny"
    assert asked == []


def test_dialog_still_shows_window_for_skill_script(parts):
    manager, approval, _ = parts
    asked = []
    approval.set_notifier(make_dialog_notifier(
        approval, lambda *args: asked.append(args) or "allow", 10))
    task = manager.submit(SKILL_FIELDS, _executor(), True)
    assert manager.wait(task.id, 5).status == TaskStatus.DONE
    assert len(asked) == 1
