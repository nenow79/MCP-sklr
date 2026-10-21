"""Detached recovery regressions: real SQLite/RAM, fake task executors.

These tests do not certify production transport, browser or harness behavior.
"""
from types import SimpleNamespace

import pytest

from server.session_checkpoint import CheckpointRejected
from server.session_resume import ResumeRejected
from tasks.approval import ApprovalService
from tasks.checkpoint_store import CheckpointStore
from tasks.manager import TaskManager, TaskResumeError
from tasks.models import TaskStatus
from tasks.resume_store import ResumeStore
from tasks.session_controller import (
    CheckpointOperationError, ResumeOperationError, SessionBlocked, SessionController,
)
from tasks.session_models import SessionConflict
from tasks.session_store import SessionStore
from tasks.store import TaskStore


@pytest.fixture
def env(tmp_path):
    sessions = SessionStore(tmp_path / 'sessions.db')
    tasks = TaskStore(str(tmp_path / 'tasks.db'))
    checkpoints, resumes = CheckpointStore(), ResumeStore()
    controller = SessionController(sessions, checkpoints=checkpoints, resumes=resumes)
    session = sessions.create(['sklr'])
    controller.bind(session.id, session.revision)
    manager = TaskManager(tasks, ApprovalService(), max_queued=100,
                          session_controller=controller)
    yield SimpleNamespace(sessions=sessions, tasks=tasks, checkpoints=checkpoints,
                          resumes=resumes, controller=controller, manager=manager,
                          sid=session.id, path=tmp_path / 'sessions.db')
    manager.shutdown()
    for worker in manager._workers:
        worker.join(timeout=6)
    assert not any(worker.is_alive() for worker in manager._workers)
    tasks.close()
    sessions.close()


def pending(env, count=1):
    return [env.manager.submit({}, lambda event: pytest.fail('old executor ran'), True)
            for _ in range(count)]


def pause(env, checkpoint=True):
    state = env.controller.request_pause(env.controller.snapshot()['revision'])
    if checkpoint:
        env.controller.save_checkpoint(
            {'goal': 'checkpoint-marker', 'next_step': 'Review plan'}, state['revision'])
    return env.controller.snapshot()


def prepare(env, allow_missing=False):
    return env.manager.prepare_session_resume(
        'supplement-marker', env.controller.snapshot()['revision'],
        allow_missing_checkpoint=allow_missing)


def confirm(env):
    context = env.controller.get_resume_context()
    return env.controller.confirm_resume_plan('plan-marker', context['revision'],
                                              context['preparation_token'])


def uncertain_confirmation(env, monkeypatch, commit_first):
    context = env.controller.get_resume_context()
    original = env.sessions.confirm_resume
    def fail(*args):
        if commit_first:
            original(*args)
        raise RuntimeError('PRIVATE-COMMIT')
    with monkeypatch.context() as patch:
        patch.setattr(env.sessions, 'confirm_resume', fail)
        with pytest.raises(ResumeOperationError, match='^resume_confirm_failed$'):
            confirm(env)
    return context


def assert_blocked(env):
    assert env.controller.snapshot()['execution_blocked']
    with pytest.raises(SessionBlocked):
        env.controller.begin_action()


def test_checkpoint_replacement_invalidates_handshake_and_allows_manager_prepare(env):
    pending(env)
    pause(env)
    prepare(env)
    old = env.controller.get_resume_context()
    saved = env.controller.save_checkpoint(
        {'goal': 'Replacement', 'next_step': 'Review updated plan'}, old['revision'])
    assert saved['revision'] == old['revision'] + 1
    assert not env.controller.snapshot()['resume_pending']
    assert env.controller.get_resume_context() == {'available': False}
    with pytest.raises(SessionConflict):
        env.controller.confirm_resume_plan('Old', old['revision'], old['preparation_token'])
    prepare(env)
    new = env.controller.get_resume_context()
    assert new['preparation_token'] != old['preparation_token']
    assert new['checkpoint']['goal'] == 'Replacement'
    assert_blocked(env)


@pytest.mark.parametrize('reason', ['stale', 'secret', 'foreign_task'])
def test_rejected_checkpoint_does_not_destroy_handshake(env, reason):
    pause(env)
    prepare(env)
    old = env.controller.get_resume_context()
    payload = {'goal': 'API_TOKEN=private' if reason == 'secret' else 'Safe',
               'next_step': 'Review plan'}
    if reason == 'foreign_task':
        payload['done_task_ids'] = [9999]
    expected_error = SessionConflict if reason == 'stale' else CheckpointRejected
    expected_message = {
        'stale': r'^Session revision changed\.$',
        'secret': '^secret_detected$',
        'foreign_task': '^foreign_task$',
    }[reason]
    with pytest.raises(expected_error, match=expected_message):
        env.controller.save_checkpoint(payload, old['revision'] - int(reason == 'stale'))
    assert env.controller.get_resume_context() == old
    assert_blocked(env)


@pytest.mark.parametrize('commit_first', [False, True])
def test_uncertain_checkpoint_save_leaves_no_dead_handshake(env, monkeypatch, commit_first):
    pause(env)
    prepare(env)
    old = env.controller.get_resume_context()
    original = env.sessions.mark_checkpoint_saved
    def fail(*args):
        if commit_first:
            original(*args)
        raise RuntimeError('PRIVATE')
    with monkeypatch.context() as patch:
        patch.setattr(env.sessions, 'mark_checkpoint_saved', fail)
        with pytest.raises(CheckpointOperationError):
            env.controller.save_checkpoint(
                {'goal': 'New', 'next_step': 'Review updated plan'}, old['revision'])
    assert env.controller.get_resume_context() == {'available': False}
    assert not env.controller.snapshot()['checkpoint_content_available']
    prepare(env, allow_missing=True)
    assert env.controller.get_resume_context()['preparation_token'] != old['preparation_token']
    assert_blocked(env)


@pytest.mark.parametrize('target', ['prepare', 'confirm'])
def test_ram_mutation_then_error_allows_fresh_preparation(env, monkeypatch, target):
    pause(env)
    if target == 'confirm':
        prepare(env)
    original = getattr(env.resumes, target)
    def fail(*args):
        original(*args)
        raise RuntimeError('PRIVATE-RAM')
    with monkeypatch.context() as patch:
        patch.setattr(env.resumes, target, fail)
        with pytest.raises(TaskResumeError if target == 'prepare' else ResumeOperationError):
            prepare(env) if target == 'prepare' else confirm(env)
    assert env.controller.get_resume_context() == {'available': False}
    assert env.controller.get_checkpoint()['available']
    assert_blocked(env)
    prepare(env)
    assert env.controller.get_resume_context()['available']
    assert_blocked(env)


def test_failed_clear_cannot_revive_leftover_confirmed_ram(env, monkeypatch):
    pause(env)
    prepare(env)
    old = env.controller.get_resume_context()
    original = env.resumes.confirm
    def fail_confirm(*args):
        original(*args)
        raise RuntimeError('PRIVATE')
    def fail_clear():
        raise RuntimeError('PRIVATE')
    with monkeypatch.context() as patch:
        patch.setattr(env.resumes, 'confirm', fail_confirm)
        patch.setattr(env.resumes, 'clear', fail_clear)
        with pytest.raises(ResumeOperationError, match='^resume_ram_failed$'):
            confirm(env)
        assert env.resumes.get().plan is not None
        assert env.controller.get_resume_context() == {'available': False}
        with pytest.raises(SessionConflict):
            env.controller.confirm_resume_plan('Old', old['revision'], old['preparation_token'])
        revision = env.controller.snapshot()['revision']
        with pytest.raises(TaskResumeError):
            prepare(env)
        assert env.controller.snapshot()['revision'] == revision
        assert_blocked(env)
    prepare(env)
    assert env.controller.get_resume_context()['preparation_token'] != old['preparation_token']


@pytest.mark.parametrize('commit_first', [False, True])
def test_uncertain_prepare_commit_requires_fresh_state_and_missing_ack(env, monkeypatch, commit_first):
    pause(env)
    revision = env.controller.snapshot()['revision']
    original = env.sessions.prepare_resume
    def fail(*args):
        if commit_first:
            original(*args)
        raise RuntimeError('PRIVATE')
    with monkeypatch.context() as patch:
        patch.setattr(env.sessions, 'prepare_resume', fail)
        with pytest.raises(TaskResumeError):
            prepare(env)
    assert env.controller.snapshot()['revision'] == revision + int(commit_first)
    assert env.controller.get_resume_context() == {'available': False}
    with pytest.raises(SessionConflict):
        prepare(env)
    prepare(env, allow_missing=True)
    assert env.controller.get_resume_context()['checkpoint_missing']
    assert_blocked(env)


@pytest.mark.parametrize('commit_first', [False, True])
def test_uncertain_confirm_requires_new_coordinated_handshake(env, monkeypatch, commit_first):
    old_task = pending(env)[0]
    pause(env)
    prepare(env)
    old = uncertain_confirmation(env, monkeypatch, commit_first)
    state = env.controller.snapshot()
    assert state['resume_recovery_required']
    assert state['state'] == ('active' if commit_first else 'paused')
    env.controller.bind(env.sid, state['revision'])
    assert_blocked(env)
    with pytest.raises(SessionConflict):
        env.controller.normalize_resume_internal(state['revision'])
    with pytest.raises(SessionConflict):
        env.controller.prepare_resume_internal('', state['revision'], allow_missing_checkpoint=True)
    with pytest.raises(SessionConflict):
        env.controller.confirm_resume_plan('Old', old['revision'], old['preparation_token'])
    with pytest.raises(SessionConflict):
        prepare(env)  # lost/untrusted checkpoint requires a separate acknowledgement
    prepare(env, allow_missing=True)
    new = env.controller.get_resume_context()
    assert new['preparation_token'] != old['preparation_token']
    assert env.controller.snapshot()['state'] == 'paused'
    assert not env.controller.snapshot()['resume_recovery_required']
    assert env.manager.get(old_task.id).status == TaskStatus.CANCELLED
    assert_blocked(env)
    confirm(env)
    assert not env.controller.snapshot()['execution_blocked']


@pytest.mark.parametrize('target', ['request_pause', 'mark_paused'])
@pytest.mark.parametrize('commit_first', [False, True])
def test_normalization_failure_can_be_explicitly_prepared_again(env, monkeypatch, target, commit_first):
    pause(env)
    prepare(env)
    uncertain_confirmation(env, monkeypatch, True)
    original = getattr(env.sessions, target)
    def fail(*args):
        if commit_first:
            original(*args)
        raise RuntimeError('PRIVATE')
    with monkeypatch.context() as patch:
        patch.setattr(env.sessions, target, fail)
        with pytest.raises(TaskResumeError):
            prepare(env, allow_missing=True)
    assert env.controller.snapshot()['resume_recovery_required']
    assert env.controller.get_resume_context() == {'available': False}
    assert_blocked(env)
    prepare(env, allow_missing=True)
    assert env.controller.snapshot()['state'] == 'paused'
    assert_blocked(env)


def test_running_row_and_invalid_input_precede_recovery_mutations(env, monkeypatch):
    pause(env)
    prepare(env)
    uncertain_confirmation(env, monkeypatch, True)
    state = env.controller.snapshot()
    running = env.tasks.create(status=TaskStatus.RUNNING)
    env.sessions.link_task(env.sid, running.id, state['revision'])
    with pytest.raises(ResumeRejected):
        env.manager.prepare_session_resume('API_TOKEN=private', state['revision'],
                                           allow_missing_checkpoint=True)
    with pytest.raises(TaskResumeError):
        prepare(env, allow_missing=True)
    assert env.controller.snapshot() == state
    assert env.manager.get(running.id).status == TaskStatus.RUNNING
    assert_blocked(env)


def test_partial_cancellation_retry_uses_fresh_rows(env, monkeypatch):
    rows = pending(env, 3)
    pause(env)
    original = env.tasks.update
    def fail(task_id, **fields):
        result = original(task_id, **fields)
        if task_id == rows[1].id and fields.get('status') == TaskStatus.CANCELLED:
            raise RuntimeError('PRIVATE')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(env.tasks, 'update', fail)
        with pytest.raises(TaskResumeError) as info:
            prepare(env)
        assert info.value.cancelled_count == 1
    assert prepare(env)['cancelled_count'] == 1
    assert all(env.manager.get(row.id).status == TaskStatus.CANCELLED for row in rows)
    assert_blocked(env)


def test_restart_after_uncertain_confirm_does_not_restore_authorization(env, monkeypatch):
    pause(env)
    prepare(env)
    old = uncertain_confirmation(env, monkeypatch, True)
    env.sessions.recover_after_restart()
    restarted = SessionController(env.sessions, checkpoints=CheckpointStore(), resumes=ResumeStore())
    item = env.sessions.get(env.sid)
    restarted.bind(item.id, item.revision)
    assert restarted.snapshot()['execution_blocked']
    assert restarted.snapshot()['state'] == 'paused'
    assert restarted.get_resume_context() == {'available': False}
    with pytest.raises(SessionBlocked):
        restarted.begin_action()
    with pytest.raises(SessionConflict):
        restarted.confirm_resume_plan('Old', old['revision'], old['preparation_token'])


def test_recovery_text_and_tokens_never_enter_sqlite_or_wal(env, monkeypatch):
    pause(env)
    prepare(env)
    old = uncertain_confirmation(env, monkeypatch, True)
    prepare(env, allow_missing=True)
    new = env.controller.get_resume_context()
    confirm(env)
    markers = ['checkpoint-marker', 'supplement-marker', 'plan-marker',
               old['preparation_token'], new['preparation_token']]
    dump = '\n'.join(env.sessions._conn.iterdump())
    for marker in markers:
        assert marker not in dump
        for path in env.path.parent.glob('sessions.db*'):
            assert marker.encode() not in path.read_bytes()
