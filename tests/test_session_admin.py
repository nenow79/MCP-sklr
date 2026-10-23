"""Detached panel adapter tests; no network, production main or endpoints."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from server.session_admin import SessionAdmin
from tasks.session_controller import SessionBlocked, SessionController
from tasks.session_models import SessionConflict
from tasks.session_store import SessionStore


@pytest.fixture
def env(tmp_path):
    store = SessionStore(tmp_path / 'sessions.db')
    controller = SessionController(store)
    entries = [{'alias': 'sklr', 'valid': True}, {'alias': 'missing', 'valid': False}]
    roots = SimpleNamespace(list_status=lambda: entries)
    admin = SessionAdmin(store, controller, roots)
    yield SimpleNamespace(store=store, controller=controller, admin=admin, entries=entries)
    store.close()


def test_status_does_not_start_work(env):
    state = env.admin.status()
    assert state['session_id'] is None and state['execution_blocked']
    assert state['roots'] == [] and state['scope'] == 'instance'
    assert 'checkpoint_available' not in state
    assert not state['resume_available'] and not state['switch_available']
    assert 'wszystkie rozmowy' in state['warning']


def test_start_explicit_roots_and_no_permission_changes(env):
    before = [dict(entry) for entry in env.entries]
    state = env.admin.start(['sklr'])
    assert state['state'] == 'active' and not state['execution_blocked']
    assert state['roots'] == ['sklr'] and state['revision'] == 1
    assert env.entries == before
    assert env.store.get(state['session_id']).roots == ('sklr',)
    token = env.controller.begin_action()
    env.controller.finish_action(token)


@pytest.mark.parametrize('roots', [[], ['unknown'], ['missing'], ['../sklr'],
                                    ['sklr', 'sklr'], 'sklr', None])
def test_invalid_or_unavailable_roots_do_not_start(env, roots):
    with pytest.raises(ValueError):
        env.admin.start(roots)
    assert env.admin.status()['session_id'] is None
    with env.store._lock:
        assert env.store._conn.execute('SELECT COUNT(*) FROM work_sessions').fetchone()[0] == 0


def test_pause_is_idempotent_at_current_revision_and_does_not_save_checkpoint(env):
    active = env.admin.start(['sklr'])
    paused = env.admin.pause(active['session_id'], active['revision'])
    assert paused['state'] == 'paused' and paused['execution_blocked']
    assert not any(key.startswith('checkpoint') for key in paused)
    assert env.admin.pause(paused['session_id'], paused['revision']) == paused
    with pytest.raises(SessionBlocked):
        env.controller.begin_action()
    with pytest.raises(SessionConflict):
        env.admin.start(['sklr'])


def test_pause_waits_for_actions_without_cancelling(env):
    active = env.admin.start(['sklr'])
    token = env.controller.begin_action()
    paused = env.admin.pause(active['session_id'], 1)
    assert paused['state'] == 'pause_requested' and paused['running_actions'] == 1
    env.controller.finish_action(token)
    assert env.admin.status()['state'] == 'paused'


def test_wrong_session_and_missing_selection_rejected(env):
    with pytest.raises(SessionConflict):
        env.admin.pause('0' * 32, 1)
    active = env.admin.start(['sklr'])
    with pytest.raises(SessionConflict):
        env.admin.pause('0' * 32, active['revision'])
    assert env.admin.status() == active


def test_stale_revision_blocks_but_does_not_report_durable_pause(env):
    active = env.admin.start(['sklr'])
    with pytest.raises(SessionConflict):
        env.admin.pause(active['session_id'], 2)
    state = env.admin.status()
    assert state['execution_blocked'] and state['state'] == 'active'
    assert env.admin.pause(active['session_id'], 1)['state'] == 'paused'


def test_concurrent_start_creates_only_one_session(env):
    def start():
        try:
            return env.admin.start(['sklr'])['session_id']
        except SessionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert sum(result is not None for result in results) == 1
    with env.store._lock:
        assert env.store._conn.execute('SELECT COUNT(*) FROM work_sessions').fetchone()[0] == 1


def test_status_exposes_no_checkpoint_or_result_text(env):
    state = env.admin.start(['sklr'])
    assert not {'summary', 'content', 'payload', 'external_result', 'secret_result'} & state.keys()


def test_folder_change_requires_current_full_pause(env):
    active = env.admin.start(['sklr'])
    called = []
    with pytest.raises(SessionConflict):
        env.admin.change_folders(lambda: called.append(True), active)
    token = env.controller.begin_action()
    draining = env.admin.pause(active['session_id'], active['revision'])
    with pytest.raises(SessionConflict):
        env.admin.change_folders(lambda: called.append(True), draining)
    env.controller.finish_action(token)
    paused = env.admin.status()
    with pytest.raises(SessionConflict):
        env.admin.change_folders(lambda: called.append(True), active)
    with pytest.raises(SessionConflict):
        env.admin.change_folders(lambda: called.append(True), {})
    assert called == []
    assert env.admin.status() == paused


def test_paused_folder_change_updates_membership_and_invalidates_old_generation(env):
    active = env.admin.start(['sklr'])
    paused = env.admin.pause(active['session_id'], active['revision'])
    env.controller._confirmed_plan_revision = paused['revision']
    result = env.admin.change_folders(
        lambda: env.entries.append({'alias': 'docs', 'valid': True}) or 'added', paused)
    changed = env.admin.status()
    assert result == 'added'
    assert changed['roots'] == ['sklr', 'docs']
    assert changed['revision'] == paused['revision'] + 1
    assert changed['state'] == 'paused' and changed['execution_blocked']
    assert env.controller._confirmed_plan_revision is None
    with pytest.raises(SessionBlocked):
        env.controller.begin_action()
    with pytest.raises(SessionConflict):
        env.admin.change_folders(lambda: None, paused)


def test_pending_resume_and_last_folder_cannot_be_changed(env):
    active = env.admin.start(['sklr'])
    paused = env.admin.pause(active['session_id'], active['revision'])
    called = []
    with pytest.raises(SessionConflict):
        env.admin.change_folders(lambda: called.append(True), paused, removing='sklr')
    from tasks.resume_store import ResumeStore
    env.controller._resumes = ResumeStore()
    env.controller._resume_pending_revision = paused['revision']
    with pytest.raises(SessionConflict):
        env.admin.change_folders(lambda: called.append(True), paused)
    assert called == []


def test_folder_change_before_start_does_not_create_a_session(env):
    env.admin.change_folders(lambda: env.entries.append({'alias': 'docs', 'valid': True}), {})
    assert env.admin.status()['session_id'] is None
    assert env.admin.start(['sklr', 'docs'])['roots'] == ['sklr', 'docs']


def test_folder_write_failure_keeps_session_blocked(env):
    active = env.admin.start(['sklr'])
    paused = env.admin.pause(active['session_id'], active['revision'])
    def failure():
        raise OSError('write failed')
    with pytest.raises(OSError):
        env.admin.change_folders(failure, paused)
    assert env.admin.status()['execution_blocked']
    assert env.admin.status()['revision'] == paused['revision']


def test_folder_limit_rejects_before_mutating_registry(env):
    active = env.admin.start(['sklr'])
    paused = env.admin.pause(active['session_id'], active['revision'])
    env.entries.extend({'alias': f'root_{i}', 'valid': True} for i in range(31))
    called = []
    with pytest.raises(SessionConflict):
        env.admin.change_folders(lambda: called.append(True), paused, adding=True)
    assert called == []
