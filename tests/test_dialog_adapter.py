import threading
import time

import pytest

from tasks.approval import ApprovalService
from tasks.dialog_adapter import build_message, build_title, make_dialog_notifier
from tasks.manager import TaskManager
from tasks.models import TaskStatus
from tasks.store import TaskStore

OK = {"started": True, "exit_code": 0, "stdout": "out", "stderr": "", "stdout_truncated": False,
      "stderr_truncated": False, "duration_s": 0.1, "timed_out": False, "cancelled": False}

FIELDS = dict(skill="demo", source="central", script="scripts/run.py", skill_dir="C:\\s\\demo",
              script_preview="print('hi')", args=["a"], timeout=10, command=["py", "run.py"],
              skill_hash="abcdef0123456789ffff", approval_reason="domyślnie", offer_trust=False)


@pytest.fixture
def parts(tmp_path):
    store = TaskStore(str(tmp_path / "t.db"))
    approval = ApprovalService()
    manager = TaskManager(store, approval, max_wait_s=5)
    yield manager, approval
    manager.shutdown()
    store.close()


def _executor(_event):
    return dict(OK)


def test_message_and_title_content(parts):
    manager, approval = parts
    task = manager.submit(FIELDS, _executor, True)
    msg = build_message(task)
    assert "#%d" % task.id in msg
    assert "demo (central)" in msg and "scripts/run.py" in msg
    assert "['a']" in msg and "py" in msg and "C:\\s\\demo" in msg
    assert "abcdef0123456789..." in msg and "domyślnie" in msg
    assert "print('hi')" in msg
    assert build_title(task) == "Uruchomienie skryptu skilla"
    trust_task = manager.submit({**FIELDS, "offer_trust": True}, _executor, True)
    assert build_title(trust_task) == "Ufaj temu skillowi?"


@pytest.mark.parametrize("answer,expected", [
    ("allow", TaskStatus.DONE), ("trust", TaskStatus.DONE),
    ("deny", TaskStatus.REJECTED), ("timeout", TaskStatus.REJECTED),
])
def test_decisions_are_mapped(parts, answer, expected):
    manager, approval = parts
    calls = []

    def ask(title, message, allow_trust, timeout_s):
        calls.append((title, allow_trust, timeout_s))
        return answer

    approval._notifier = make_dialog_notifier(approval, ask, 77)
    task = manager.submit({**FIELDS, "offer_trust": True}, _executor, True)
    assert manager.wait(task.id, 5).status == expected
    assert calls == [("Ufaj temu skillowi?", True, 77)]


def test_notifier_does_not_block_submit(parts):
    manager, approval = parts
    release = threading.Event()

    def ask(*_args):
        release.wait(5)
        return "allow"

    approval._notifier = make_dialog_notifier(approval, ask, 10)
    begin = time.monotonic()
    task = manager.submit(FIELDS, _executor, True)
    assert time.monotonic() - begin < 1
    assert manager.get(task.id).status == TaskStatus.AWAITING_APPROVAL
    release.set()
    assert manager.wait(task.id, 5).status == TaskStatus.DONE


def test_cancel_during_dialog_is_ignored(parts):
    manager, approval = parts
    shown, release = threading.Event(), threading.Event()

    def ask(*_args):
        shown.set()
        release.wait(5)
        return "allow"

    approval._notifier = make_dialog_notifier(approval, ask, 10)
    task = manager.submit(FIELDS, _executor, True)
    assert shown.wait(5)
    assert manager.cancel(task.id).status == TaskStatus.CANCELLED
    release.set()
    time.sleep(0.3)  # worker okna próbuje resolve -> TaskStateError, ignorowany
    assert manager.get(task.id).status == TaskStatus.CANCELLED


def test_ask_exception_means_deny(parts):
    manager, approval = parts

    def ask(*_args):
        raise RuntimeError("no display")

    approval._notifier = make_dialog_notifier(approval, ask, 10)
    task = manager.submit(FIELDS, _executor, True)
    assert manager.wait(task.id, 5).status == TaskStatus.REJECTED


def test_unknown_answer_means_deny(parts):
    manager, approval = parts
    approval._notifier = make_dialog_notifier(approval, lambda *a: "whatever", 10)
    task = manager.submit(FIELDS, _executor, True)
    done = manager.wait(task.id, 5)
    assert done.status == TaskStatus.REJECTED and done.decision == "deny"
