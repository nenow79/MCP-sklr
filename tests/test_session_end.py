"""Discard lifecycle: task draining, RAM invalidation and restart exclusion."""
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from server.session_admin import SessionAdmin
from server.session_tools import session_control
from tasks.approval import ApprovalService
from tasks.checkpoint_store import CheckpointStore
from tasks.manager import TaskManager
from tasks.resume_store import ResumeStore
from tasks.session_controller import SessionBlocked, SessionController
from tasks.session_models import SessionConflict
from tasks.session_store import SessionStore
from tasks.store import TaskStore


@pytest.fixture
def env(tmp_path):
    sessions = SessionStore(tmp_path / 'sessions.db')
    tasks = TaskStore(str(tmp_path / 'tasks.db'))
    controller = SessionController(sessions, CheckpointStore(), ResumeStore())
    manager = TaskManager(tasks, ApprovalService(), max_parallel=1, session_controller=controller)
    entries = [{'alias': 'old', 'valid': True}, {'alias': 'new', 'valid': True}]
    admin = SessionAdmin(sessions, controller, SimpleNamespace(list_status=lambda: entries))
    admin.attach_manager(manager)
    releases = []
    yield SimpleNamespace(sessions=sessions, tasks=tasks, controller=controller,
                          manager=manager, admin=admin, releases=releases)
    for release in releases:
        release.set()
    manager.shutdown()
    for worker in manager._workers:
        worker.join(timeout=6)
    assert not any(worker.is_alive() for worker in manager._workers)
    tasks.close()
    sessions.close()


def test_discard_checkpoint_handshake_and_start_with_other_roots(env):
    active = env.admin.start(['old'])
    paused = env.admin.pause(active['session_id'], active['revision'])
    saved = env.controller.save_checkpoint({'goal': 'Old goal', 'next_step': 'Wait'}, paused['revision'])
    env.admin.prepare_resume(active['session_id'], saved['revision'], 'Old supplement', 0, False, True)
    context = env.controller.get_resume_context()
    state = env.admin.status()
    ended = env.admin.end(state['session_id'], state['revision'])
    assert ended['session_id'] is None and ended['execution_blocked']
    assert len(env.controller._checkpoints) == 0
    assert env.controller._resumes.get() is None
    assert env.admin.saved_sessions()['sessions'] == []
    with pytest.raises(SessionConflict):
        env.controller.confirm_resume_plan('Old plan', context['revision'], context['preparation_token'])
    fresh = env.admin.start(['new'])
    assert fresh['session_id'] != active['session_id']
    assert fresh['roots'] == ['new'] and not fresh['execution_blocked']
    assert not fresh['checkpoint_content_available'] and not fresh['resume_pending']
    with pytest.raises(SessionConflict):
        env.admin.end(active['session_id'], state['revision'])
    assert env.admin.status() == fresh


def test_end_drains_running_cancels_pending_and_never_requests_checkpoint(env):
    active = env.admin.start(['old'])
    entered, release = threading.Event(), threading.Event()
    env.releases.append(release)
    def run(cancel):
        entered.set()
        assert release.wait(5)
        assert not cancel.is_set()
        return {'exit_code': 0}
    running = env.manager.submit({}, run, False)
    assert entered.wait(5)
    calls = []
    queued = env.manager.submit({}, lambda cancel: calls.append(True), False)
    approval = env.manager.submit({}, lambda cancel: calls.append(True), True)
    ended = env.admin.end(active['session_id'], active['revision'])
    assert ended['state'] == 'ending' and ended['running_actions'] == 1
    assert ended['checkpoint_state'] == 'unavailable'
    control = session_control(env.controller)
    assert control['code'] == 'session_ending'
    assert 'Nie zapisuj checkpointu' in control['instruction']
    for task in (queued, approval):
        assert env.manager.get(task.id).status.value == 'cancelled'
    with pytest.raises(SessionBlocked):
        env.controller.begin_action()
    with pytest.raises(SessionConflict):
        env.controller.save_checkpoint({'goal': 'Discard', 'next_step': 'Wait'}, ended['revision'])
    with pytest.raises(SessionConflict):
        env.admin.change_folders(lambda: None, ended)
    with pytest.raises(SessionConflict):
        env.admin.start(['new'])
    release.set()
    assert env.manager.wait(running.id, 5).status.value == 'done'
    # Joining workers after shutdown also waits for their session-token cleanup.
    env.manager.shutdown()
    for worker in env.manager._workers:
        worker.join(timeout=6)
    assert env.admin.status()['session_id'] is None and calls == []
    assert env.sessions.get(active['session_id']).lifecycle == 'ended'
    assert env.sessions.get_task_link(running.id).session_id == active['session_id']


def test_partial_cancellation_keeps_gate_closed_until_explicit_retry(env, monkeypatch):
    active = env.admin.start(['old'])
    pending = env.manager.submit({}, lambda cancel: None, True)
    update = env.tasks.update
    def fail(*args, **kwargs):
        raise RuntimeError('storage error')
    monkeypatch.setattr(env.tasks, 'update', fail)
    with pytest.raises(RuntimeError):
        env.admin.end(active['session_id'], active['revision'])
    state = env.admin.status()
    assert state['state'] == 'ending' and state['execution_blocked']
    assert state['session_id'] == active['session_id']
    assert env.manager.get(pending.id).status.value == 'awaiting_approval'
    with pytest.raises(SessionConflict):
        env.admin.start(['new'])
    monkeypatch.setattr(env.tasks, 'update', update)
    assert env.admin.end(state['session_id'], state['revision'])['session_id'] is None
    assert env.manager.get(pending.id).status.value == 'cancelled'


def test_end_marker_survives_restart_and_cannot_be_recovered(env):
    active = env.admin.start(['old'])
    # Simulate process exit after the discard marker, before draining/finalization.
    env.sessions.request_end(active['session_id'], active['revision'])
    assert env.sessions.recover_after_restart() == 0
    item = env.sessions.get(active['session_id'])
    assert item.lifecycle == 'ended'
    restarted = SessionController(env.sessions, CheckpointStore(), ResumeStore())
    with pytest.raises(SessionConflict):
        restarted.bind(item.id, item.revision)
    with pytest.raises(SessionConflict):
        restarted.recover_selection(item.id, item.revision)
    assert env.sessions.list_saved_sessions() == ([], False)
    assert restarted.snapshot()['session_id'] is None


def test_v1_migration_preserves_session_and_links(tmp_path):
    from tasks.session_store import _SCHEMA
    path = tmp_path / 'v1.db'
    sid = 'a' * 32
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        conn.execute('PRAGMA user_version=1')
        conn.execute('INSERT INTO work_sessions VALUES (?,?,?,?,?,?,?,?)',
                     (sid, '["old"]', 'active', 'unavailable', 1, '', 'before', 'before'))
        conn.execute('INSERT INTO session_task_links VALUES (?,?,?)', (42, sid, 1))
    store = SessionStore(path)
    try:
        assert store.get(sid).lifecycle == 'open'
        assert store.get_task_link(42).session_id == sid
        store.request_end(sid, 1)
        store.finish_end(sid, 2)
        assert store.recover_after_restart() == 0
        assert store.get(sid).lifecycle == 'ended'
    finally:
        store.close()


def test_ending_revokes_autopilot_and_next_session_starts_manual(tmp_path):
    from autopilot_fixtures import environment
    from server.autopilot_admin import AutopilotAdmin
    with environment(tmp_path) as e:
        e.approve([e.file_rule('read_file')])
        admin = SessionAdmin(e.session_store, e.sessions, e.roots)
        admin.attach_manager(e.manager)
        ap_admin = AutopilotAdmin(e.runtime)
        ap_admin._draft = {'old': True}
        ap_admin._approved = {'old': True}
        state = admin.status()
        assert e.autopilot.engaged
        ended = admin.end(state['session_id'], state['revision'], ap_admin)
        assert ended['session_id'] is None
        assert not e.autopilot.engaged
        assert e.autopilot.snapshot()['state'] == 'disabled'
        assert ap_admin._draft is None and ap_admin._approved is None
        assert e.autopilot.resume_scope is None
        fresh = admin.start(['other'])
        assert fresh['roots'] == ['other'] and not fresh['execution_blocked']
        ap_admin._draft = {'fresh': True}
        with pytest.raises(SessionConflict):
            admin.end(state['session_id'], state['revision'], ap_admin)
        assert ap_admin._draft == {'fresh': True}


def test_uncertain_final_commit_requires_explicit_retry_and_never_reopens(env, monkeypatch):
    active = env.admin.start(['old'])
    finish = env.sessions.finish_end
    def commit_then_fail(sid, revision):
        finish(sid, revision)
        raise RuntimeError('Uncertain commit')
    monkeypatch.setattr(env.sessions, 'finish_end', commit_then_fail)
    with pytest.raises(RuntimeError):
        env.admin.end(active['session_id'], active['revision'])
    state = env.admin.status()
    assert state['state'] == 'ending' and state['execution_blocked']
    assert env.sessions.get(active['session_id']).lifecycle == 'ended'
    assert env.admin.saved_sessions()['sessions'] == []
    with pytest.raises(SessionConflict):
        env.admin.start(['new'])
    monkeypatch.setattr(env.sessions, 'finish_end', finish)
    assert env.admin.end(state['session_id'], state['revision'])['session_id'] is None
