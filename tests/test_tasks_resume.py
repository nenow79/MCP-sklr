"""Detached resume coordinator tests with fake executors; no production main."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

from server.session_resume import ResumeRejected
from tasks.approval import ApprovalService
from tasks.manager import TaskManager, TaskResumeError, TaskStateError
from tasks.models import TaskStatus
from tasks.resume_store import ResumeStore
from tasks.session_controller import SessionController, SessionBlocked
from tasks.session_models import SessionConflict
from tasks.session_store import SessionStore
from tasks.store import TaskStore


@pytest.fixture
def env(tmp_path):
    sessions = SessionStore(tmp_path / 'sessions.db')
    tasks = TaskStore(str(tmp_path / 'tasks.db'))
    controller = SessionController(sessions, resumes=ResumeStore())
    session = sessions.create(['sklr'])
    controller.bind(session.id, session.revision)
    approval = ApprovalService()
    manager = TaskManager(tasks, approval, max_queued=100, session_controller=controller)
    yield SimpleNamespace(sessions=sessions, tasks=tasks, controller=controller,
                          session=session, manager=manager, approval=approval)
    manager.shutdown()
    for worker in manager._workers:
        worker.join(timeout=6)
    assert not any(worker.is_alive() for worker in manager._workers)
    tasks.close()
    sessions.close()


def pending(env, count=1):
    return [env.manager.submit({}, lambda event: pytest.fail('old executor must not run'), True)
            for _ in range(count)]


def pause(env):
    return env.controller.request_pause(env.controller.snapshot()['revision'])


def prepare(env):
    return env.manager.prepare_session_resume('', env.controller.snapshot()['revision'])


def confirm(env):
    context = env.controller.get_resume_context()
    return env.controller.confirm_resume_plan('New plan', context['revision'],
                                              context['preparation_token'])


def test_all_links_over_twenty_and_both_pending_statuses_are_cancelled(env):
    rows = pending(env, 25)
    pause(env)
    env.manager.resolve_approval(rows[0].id, 'allow')  # queued, gate closed
    assert len(env.sessions.list_task_links(env.session.id)) == 25
    result = prepare(env)
    assert result['cancelled_count'] == 25 and result['prepared']
    assert all(env.manager.get(row.id).status == TaskStatus.CANCELLED for row in rows)
    assert all(row.id not in env.manager._ctx for row in rows)
    assert env.controller.snapshot()['execution_blocked']


def test_other_session_terminal_and_missing_rows_are_untouched(env):
    row = pending(env)[0]
    other = env.sessions.create(['other'])
    foreign = env.tasks.create(status=TaskStatus.AWAITING_APPROVAL)
    env.sessions.link_task(other.id, foreign.id, other.revision)
    done = env.tasks.create(status=TaskStatus.DONE)
    env.sessions.link_task(env.session.id, done.id, 1)
    env.sessions.link_task(env.session.id, 9999, 1)  # retained link, no task row
    pause(env)
    result = prepare(env)
    assert result['cancelled_count'] == 1
    assert env.manager.get(row.id).status == TaskStatus.CANCELLED
    assert env.manager.get(foreign.id).status == TaskStatus.AWAITING_APPROVAL
    assert env.manager.get(done.id).status == TaskStatus.DONE


def test_validation_precedes_cancellation(env):
    row = pending(env)[0]
    state = pause(env)
    with pytest.raises(ResumeRejected):
        env.manager.prepare_session_resume('API_TOKEN=private', state['revision'])
    assert env.manager.get(row.id).status == TaskStatus.AWAITING_APPROVAL
    assert not env.controller.snapshot()['resume_pending']
    assert prepare(env)['cancelled_count'] == 1


def test_running_row_prevents_any_cancellation(env):
    rows = pending(env, 2)
    pause(env)
    env.tasks.update(rows[1].id, status=TaskStatus.RUNNING)  # inconsistent row; fail closed
    with pytest.raises(TaskResumeError) as info:
        prepare(env)
    assert info.value.cancelled_count == 0
    assert env.manager.get(rows[0].id).status == TaskStatus.AWAITING_APPROVAL
    assert env.manager.get(rows[1].id).status == TaskStatus.RUNNING
    assert env.controller.snapshot()['execution_blocked']


@pytest.mark.parametrize('commit_first', [False, True])
def test_partial_or_uncertain_cancellation_keeps_gate_closed(env, monkeypatch, commit_first):
    rows = pending(env, 3)
    pause(env)
    original = env.tasks.update
    def fail_second(task_id, **fields):
        if task_id == rows[1].id and fields.get('status') == TaskStatus.CANCELLED:
            if commit_first:
                original(task_id, **fields)
            raise OSError('PRIVATE-DB-PATH')
        return original(task_id, **fields)
    monkeypatch.setattr(env.tasks, 'update', fail_second)
    with pytest.raises(TaskResumeError) as info:
        prepare(env)
    assert str(info.value) == 'resume_coordination_failed'
    assert info.value.cancelled_count == 1  # only update which returned successfully
    assert 'PRIVATE-DB-PATH' not in str(info.value)
    assert env.manager.get(rows[0].id).status == TaskStatus.CANCELLED
    assert env.manager.get(rows[1].id).status == (
        TaskStatus.CANCELLED if commit_first else TaskStatus.AWAITING_APPROVAL)
    assert env.manager.get(rows[2].id).status == TaskStatus.AWAITING_APPROVAL
    assert env.controller.get_resume_context() == {'available': False}
    assert env.controller.snapshot()['execution_blocked']


def test_prepare_failure_after_cancellation_does_not_restore_tasks(env, monkeypatch):
    row = pending(env)[0]
    pause(env)
    def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE-PREPARE-ERROR')
    monkeypatch.setattr(env.controller, 'prepare_resume_internal', fail)
    with pytest.raises(TaskResumeError) as info:
        prepare(env)
    assert info.value.cancelled_count == 1
    assert env.manager.get(row.id).status == TaskStatus.CANCELLED
    assert env.controller.snapshot()['execution_blocked']


def test_approval_race_does_not_run_old_executor(env):
    row = pending(env)[0]
    pause(env)
    barrier = threading.Barrier(2)
    def approval():
        barrier.wait(timeout=3)
        try:
            env.manager.resolve_approval(row.id, 'allow')
        except TaskStateError:
            pass  # cancellation won before approval
    def resume():
        barrier.wait(timeout=3)
        return prepare(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        approved = pool.submit(approval)
        resumed = pool.submit(resume)
        assert resumed.result(timeout=5)['cancelled_count'] == 1
        approved.result(timeout=5)
    assert env.manager.get(row.id).status == TaskStatus.CANCELLED


def test_after_handshake_new_task_requires_fresh_approval(env):
    old = pending(env)[0]
    pause(env)
    prepare(env)
    confirm(env)
    calls = []
    new = env.manager.submit({}, lambda event: calls.append(1) or {'exit_code': 0}, True)
    assert env.manager.get(old.id).status == TaskStatus.CANCELLED
    assert new.status == TaskStatus.AWAITING_APPROVAL and calls == []
    env.manager.resolve_approval(new.id, 'allow')
    assert env.manager.wait(new.id, 5).status == TaskStatus.DONE
    assert calls == [1]


def test_old_dispatch_is_cancelled_without_closing_resumed_session(env):
    old = pending(env)[0]
    pause(env)
    # Exercise stale dispatch defense independently of coordinator cancellation.
    state = env.controller.snapshot()
    env.controller.prepare_resume_internal('', state['revision'])
    confirm(env)
    env.manager.resolve_approval(old.id, 'allow')
    assert env.manager.wait(old.id, 5).status == TaskStatus.CANCELLED
    assert not env.controller.snapshot()['execution_blocked']
    token = env.controller.begin_action()
    env.controller.finish_action(token)


def test_missing_link_still_fails_closed(env):
    with pytest.raises(SessionBlocked):
        env.controller.begin_task(999)
    assert env.controller.snapshot()['execution_blocked']


def test_really_stale_pause_invalidates_handshake(env):
    state = pause(env)
    prepare(env)
    context = env.controller.get_resume_context()
    assert context['revision'] > state['revision']
    with pytest.raises(SessionConflict):
        env.controller.request_pause(state['revision'])
    assert env.controller.get_resume_context() == {'available': False}
    with pytest.raises(SessionConflict):
        env.controller.confirm_resume_plan('Plan', context['revision'], context['preparation_token'])
    assert env.controller.snapshot()['execution_blocked']
