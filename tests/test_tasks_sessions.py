"""Session/TaskManager integration with fake executors; no production main."""
import threading
import time
from types import SimpleNamespace

import pytest

from tasks.approval import ApprovalService
from tasks.manager import ExternalApprovalRequired, TaskManager, TaskStateError
from tasks.models import TaskStatus
from tasks.session_controller import SessionController
from tasks.session_store import SessionStore
from tasks.store import TaskStore
from tasks.volatile_results import VolatileResult

OK = {'exit_code': 0, 'stdout': 'ok'}
FOREIGN = 'PRIVATE-SESSION-RESULT'


def until(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), 'Expected state not reached within timeout.'


@pytest.fixture
def env(tmp_path):
    sessions = SessionStore(tmp_path / 'sessions.db')
    tasks = TaskStore(str(tmp_path / 'tasks.db'))
    session = sessions.create(['sklr'])
    controller = SessionController(sessions)
    controller.bind(session.id, 1)
    approval = ApprovalService()
    manager = TaskManager(tasks, approval, max_parallel=1, max_queued=5,
                          session_controller=controller)
    releases = []
    item = SimpleNamespace(sessions=sessions, tasks=tasks, session=session,
                           controller=controller, approval=approval,
                           manager=manager, releases=releases)
    yield item
    for release in releases:
        release.set()
    # Shutdown does not kill fake executors; ensure no database is closed
    # while a worker still finalizes a task or releases its session token.
    manager.shutdown()
    for worker in manager._workers:
        worker.join(timeout=6)
    assert not any(worker.is_alive() for worker in manager._workers)
    tasks.close()
    sessions.close()


def blocking(env, result=None):
    entered, release = threading.Event(), threading.Event()
    env.releases.append(release)
    def run(cancel):
        entered.set()
        assert release.wait(5)
        assert not cancel.is_set(), 'Pause must not cancel started work.'
        return result if result is not None else dict(OK)
    return run, entered, release


def drained(env):
    until(lambda: env.controller.snapshot()['running_actions'] == 0)


def test_assignment_before_execution(env):
    observed = []
    task = env.manager.submit({}, lambda event: observed.append(1) or dict(OK), False)
    assert env.sessions.get_task_link(task.id).session_id == env.session.id
    assert env.manager.wait(task.id, 5).status == TaskStatus.DONE
    drained(env)
    assert observed == [1]


def test_pause_drains_running_and_consumes_queued_without_retry(env, monkeypatch):
    run, entered, release = blocking(env)
    first = env.manager.submit({}, run, False)
    assert entered.wait(5)
    attempts, attempted, calls = [], threading.Event(), []
    original = env.controller.begin_task
    def record(task_id):
        attempts.append(task_id)
        try:
            return original(task_id)
        finally:
            attempted.set()
    monkeypatch.setattr(env.controller, 'begin_task', record)
    second = env.manager.submit({}, lambda event: calls.append(1) or dict(OK), False)
    state = env.controller.request_pause(1)
    assert state['state'] == 'pause_requested'
    release.set()
    assert env.manager.wait(first.id, 5).status == TaskStatus.DONE
    assert attempted.wait(5)
    until(lambda: env.controller.snapshot()['state'] == 'paused')
    assert env.manager.get(second.id).status == TaskStatus.QUEUED
    assert second.id in env.manager._ctx and calls == []
    assert attempts == [second.id]
    # A queued but unstarted task can still be cancelled normally.
    assert env.manager.cancel(second.id).status == TaskStatus.CANCELLED
    assert second.id not in env.manager._ctx


def test_approval_during_pause_preserves_consent_without_execution(env, monkeypatch):
    calls, attempted = [], threading.Event()
    task = env.manager.submit({}, lambda event: calls.append(1) or dict(OK), True)
    env.controller.request_pause(1)
    original = env.controller.begin_task
    def record(task_id):
        try:
            return original(task_id)
        finally:
            attempted.set()
    monkeypatch.setattr(env.controller, 'begin_task', record)
    env.approval.resolve(task.id, 'allow')
    assert attempted.wait(5)
    current = env.manager.get(task.id)
    assert current.status == TaskStatus.QUEUED and current.decision == 'allow'
    assert current.started_at is None and calls == []
    assert env.controller.snapshot()['running_actions'] == 0


@pytest.mark.parametrize('decision', ['deny', 'timeout'])
def test_rejection_or_expiry_during_pause(env, decision):
    task = env.manager.submit({}, lambda event: dict(OK), True)
    env.controller.request_pause(1)
    current = env.approval.resolve(task.id, decision)
    assert current.status == TaskStatus.REJECTED
    assert current.decision == decision and task.id not in env.manager._ctx


def test_blocked_submission_does_not_create_a_task(env):
    env.controller.request_pause(1)
    for _ in range(3):
        with pytest.raises(TaskStateError):
            env.manager.submit({}, lambda event: dict(OK), False)
    assert env.tasks.list() == [] and env.manager._ctx == {}


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_partial_assignment_failure_never_schedules(env, monkeypatch, caplog, cleanup_fails):
    def fail_link(*args):
        raise OSError(FOREIGN)
    monkeypatch.setattr(env.sessions, 'link_task', fail_link)
    if cleanup_fails:
        def fail_update(*args, **kwargs):
            raise OSError(FOREIGN)
        monkeypatch.setattr(env.tasks, 'update', fail_update)
    calls = []
    with pytest.raises(TaskStateError) as exc:
        env.manager.submit({}, lambda event: calls.append(1) or dict(OK), False)
    rows = env.tasks.list()
    assert len(rows) == 1
    assert rows[0].status == (TaskStatus.QUEUED if cleanup_fails else TaskStatus.FAILED)
    assert rows[0].started_at is None
    assert env.manager._ctx == {} and env.manager._queue.empty()
    assert calls == [] and env.controller.snapshot()['execution_blocked']
    assert FOREIGN not in str(exc.value) + caplog.text + repr(rows)
    assert env.sessions.get_task_link(rows[0].id) is None


@pytest.mark.parametrize('mode, status', [
    ('ok', TaskStatus.DONE), ('verify', TaskStatus.FAILED),
    ('error', TaskStatus.FAILED), ('timeout', TaskStatus.TIMED_OUT),
    ('cancel', TaskStatus.CANCELLED),
])
def test_tokens_released_on_all_exit_paths(env, mode, status):
    calls = []
    def run(event):
        calls.append(1)
        if mode == 'error':
            raise RuntimeError('fake executor failure')
        return {**OK, 'timed_out': mode == 'timeout', 'cancelled': mode == 'cancel'}
    verify = (lambda: 'verification refused') if mode == 'verify' else None
    task = env.manager.submit({}, run, False, verify=verify)
    assert env.manager.wait(task.id, 5).status == status
    drained(env)
    assert calls == ([] if mode == 'verify' else [1])


def test_real_cancel_event_releases_token(env):
    entered = threading.Event()
    def run(event):
        entered.set()
        assert event.wait(5)
        return {'cancelled': True, 'stdout': 'partial'}
    task = env.manager.submit({}, run, False)
    assert entered.wait(5)
    assert env.manager.cancel(task.id).status == TaskStatus.CANCELLED
    drained(env)


def test_running_status_write_failure_releases_token(env, monkeypatch):
    original = env.tasks.update
    def fail_running(task_id, **fields):
        if fields.get('status') == TaskStatus.RUNNING:
            raise OSError('fake write failure')
        return original(task_id, **fields)
    monkeypatch.setattr(env.tasks, 'update', fail_running)
    calls = []
    task = env.manager.submit({}, lambda event: calls.append(1) or dict(OK), False)
    assert env.manager.wait(task.id, 5).status == TaskStatus.FAILED
    drained(env)
    assert calls == []


def test_finalization_error_keeps_external_result_and_no_private_logs(env, monkeypatch, caplog):
    run, entered, release = blocking(env, VolatileResult({'content': FOREIGN}))
    fields = {'kind': 'external_mcp', 'sensitive': True, 'offer_trust': False}
    task = env.manager.submit(fields, run, False)
    assert entered.wait(5)
    env.controller.request_pause(1)
    def fail_finalize(*args):
        raise OSError(FOREIGN)
    monkeypatch.setattr(env.sessions, 'mark_paused', fail_finalize)
    release.set()
    assert env.manager.wait(task.id, 5).status == TaskStatus.DONE
    drained(env)
    state = env.controller.snapshot()
    assert state['execution_blocked'] and state['state'] == 'pause_requested'
    assert env.manager.get_external_result(task.id)['data']['content'] == FOREIGN
    assert FOREIGN not in repr(env.manager.get(task.id).to_dict()) + caplog.text
    with env.tasks._lock:
        assert FOREIGN not in '\n'.join(env.tasks._conn.iterdump())
    with env.sessions._lock:
        assert FOREIGN not in '\n'.join(env.sessions._conn.iterdump())
    assert all(record.exc_info is None for record in caplog.records
               if record.name == 'tasks.manager')


def test_return_to_approval_releases_token_and_does_not_bypass_pause(env):
    entered, release = threading.Event(), threading.Event()
    env.releases.append(release)
    calls = []
    def run(event):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        raise ExternalApprovalRequired()
    fields = {'kind': 'external_mcp', 'sensitive': True, 'offer_trust': False}
    task = env.manager.submit(fields, run, False, allow_external_reapproval=True)
    assert entered.wait(5)
    env.controller.request_pause(1)
    release.set()
    until(lambda: env.manager.get(task.id).status == TaskStatus.AWAITING_APPROVAL)
    drained(env)
    assert env.controller.snapshot()['state'] == 'paused'
    env.approval.resolve(task.id, 'allow')
    # Even a repeated queue dispatch cannot cross the closed gate.
    env.manager._run(task.id)
    assert env.manager.get(task.id).status == TaskStatus.QUEUED
    assert calls == [1] and env.controller.snapshot()['running_actions'] == 0
