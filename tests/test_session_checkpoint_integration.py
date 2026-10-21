"""P3a stage 2: real session SQLite + controller + RAM; no server or main wiring."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

from server.session_checkpoint import CheckpointRejected
from tasks.checkpoint_store import CheckpointStore
from tasks.session_controller import CheckpointOperationError, SessionController
from tasks.session_models import SessionConflict, SessionState
from tasks.session_store import SessionStore


def payload(goal='checkpoint-marker-private'):
    return {'goal': goal, 'next_step': 'Review plan', 'done_task_ids': [7]}


@pytest.fixture
def env(tmp_path):
    path = tmp_path / 'work_sessions.db'
    store = SessionStore(path)
    ram = CheckpointStore()
    controller = SessionController(store, checkpoints=ram)
    item = store.create(['sklr'])
    store.link_task(item.id, 7, item.revision)
    controller.bind(item.id, item.revision)
    yield SimpleNamespace(store=store, ram=ram, controller=controller, sid=item.id, path=path)
    store.close()


def pause(env):
    return env.controller.request_pause(env.controller.snapshot()['revision'])


def test_checkpoint_is_optional_and_does_not_change_legacy_snapshot(env):
    legacy = SessionController(env.store)
    assert 'checkpoint_available' not in legacy.snapshot()
    with pytest.raises(CheckpointOperationError, match='^checkpoint_unavailable$'):
        legacy.save_checkpoint(payload(), 1)
    state = env.controller.snapshot()
    assert state['checkpoint_available'] is True
    assert state['checkpoint_content_available'] is False


def test_no_selection_and_active_save_are_rejected(env):
    fresh = SessionController(env.store, checkpoints=CheckpointStore())
    with pytest.raises(SessionConflict):
        fresh.save_checkpoint(payload(), 1)
    with pytest.raises(SessionConflict):
        env.controller.save_checkpoint(payload(), 1)
    with pytest.raises(SessionConflict):
        env.store.mark_checkpoint_saved(env.sid, 1)
    assert env.store.get(env.sid).revision == 1 and len(env.ram) == 0


def test_save_replace_stale_revision_and_public_copy(env):
    paused = pause(env)
    result = env.controller.save_checkpoint(payload(), paused['revision'])
    assert result['saved'] is True and result['revision'] == paused['revision'] + 1
    assert 'checkpoint-marker-private' not in repr(result)
    view = env.controller.get_checkpoint()
    assert view['available'] and view['checkpoint']['goal'] == 'checkpoint-marker-private'
    assert env.controller.snapshot()['checkpoint_content_available'] is True
    view['checkpoint']['done_task_ids'].append(99)
    assert env.controller.get_checkpoint()['checkpoint']['done_task_ids'] == [7]
    with pytest.raises(SessionConflict):
        env.controller.save_checkpoint(payload('stale'), paused['revision'])
    assert env.controller.get_checkpoint()['checkpoint']['goal'] == 'checkpoint-marker-private'
    updated = env.controller.save_checkpoint(payload('replacement'), result['revision'])
    assert updated['revision'] == result['revision'] + 1 and len(env.ram) == 1
    assert env.controller.get_checkpoint()['checkpoint']['goal'] == 'replacement'


def test_checkpoint_saved_during_pause_request_survives_finalization(env):
    token = env.controller.begin_action()
    requested = pause(env)
    assert requested['state'] == 'pause_requested'
    result = env.controller.save_checkpoint(payload(), requested['revision'])
    env.controller.finish_action(token)
    state = env.controller.snapshot()
    assert state['state'] == 'paused' and state['running_actions'] == 0
    assert state['checkpoint_state'] == 'saved' and state['checkpoint_content_available']
    view = env.controller.get_checkpoint()
    assert view['revision'] == result['revision']
    assert view['session_revision'] == result['revision'] + 1


def test_store_save_rejects_bad_revision_and_preserves_state(env):
    paused = pause(env)
    for bad in (0, True, '1'):
        with pytest.raises(ValueError):
            env.store.mark_checkpoint_saved(env.sid, bad)
    with pytest.raises(SessionConflict):
        env.store.mark_checkpoint_saved(env.sid, paused['revision'] - 1)
    item = env.store.mark_checkpoint_saved(env.sid, paused['revision'])
    assert item.state == SessionState.PAUSED and item.reason == 'user_requested_pause'
    assert item.checkpoint_state.value == 'saved'


def test_foreign_task_and_secret_rejection_do_not_mutate_checkpoint(env):
    paused = pause(env)
    other = env.store.create(['sklr'])
    env.store.link_task(other.id, 8, other.revision)
    foreign = payload()
    foreign['done_task_ids'] = [8]
    with pytest.raises(CheckpointRejected, match='^foreign_task$'):
        env.controller.save_checkpoint(foreign, paused['revision'])
    with pytest.raises(CheckpointRejected, match='^secret_detected$'):
        env.controller.save_checkpoint(payload('API_TOKEN=hidden'), paused['revision'])
    assert env.store.get(env.sid).revision == paused['revision'] and len(env.ram) == 0


def test_sqlite_failure_never_publishes_ram(env, monkeypatch):
    paused = pause(env)
    def broken(*args):
        raise RuntimeError('secret storage path')
    monkeypatch.setattr(env.store, 'mark_checkpoint_saved', broken)
    with pytest.raises(CheckpointOperationError, match='^checkpoint_storage_failed$'):
        env.controller.save_checkpoint(payload(), paused['revision'])
    assert len(env.ram) == 0 and env.controller.get_checkpoint() == {'available': False}
    assert env.store.get(env.sid).checkpoint_state.value == 'pending'


def test_commit_then_exception_does_not_advertise_old_ram(env, monkeypatch):
    paused = pause(env)
    first = env.controller.save_checkpoint(payload(), paused['revision'])
    mark = env.store.mark_checkpoint_saved
    def uncertain(*args):
        mark(*args)
        raise RuntimeError('unknown commit outcome')
    monkeypatch.setattr(env.store, 'mark_checkpoint_saved', uncertain)
    with pytest.raises(CheckpointOperationError, match='^checkpoint_storage_failed$'):
        env.controller.save_checkpoint(payload('replacement'), first['revision'])
    assert env.store.get(env.sid).checkpoint_state.value == 'saved'
    assert env.controller.get_checkpoint() == {'available': False}
    assert env.controller.snapshot()['checkpoint_content_available'] is False


def test_ram_failure_and_failed_cleanup_report_missing_content(env, monkeypatch):
    paused = pause(env)
    first = env.controller.save_checkpoint(payload(), paused['revision'])
    def broken(*args):
        raise RuntimeError('RAM private details')
    monkeypatch.setattr(env.ram, 'put', broken)
    monkeypatch.setattr(env.ram, 'discard', broken)
    with pytest.raises(CheckpointOperationError, match='^checkpoint_ram_failed$'):
        env.controller.save_checkpoint(payload('new'), first['revision'])
    assert env.store.get(env.sid).checkpoint_state.value == 'saved'
    assert len(env.ram) == 1  # cleanup failed, old entry is NOT advertised
    assert env.controller.get_checkpoint() == {'available': False}
    assert env.controller.snapshot()['checkpoint_content_available'] is False


def test_restart_and_external_revision_change_invalidate_ram(env):
    paused = pause(env)
    env.controller.save_checkpoint(payload(), paused['revision'])
    env.store.recover_after_restart()
    assert env.controller.get_checkpoint() == {'available': False}
    restarted = SessionController(env.store, checkpoints=CheckpointStore())
    item = env.store.get(env.sid)
    restarted.bind(item.id, item.revision)
    state = restarted.snapshot()
    assert state['checkpoint_state'] == 'unavailable'
    assert not state['checkpoint_content_available'] and state['execution_blocked']


def test_checkpoint_text_never_enters_sqlite_or_wal(env):
    paused = pause(env)
    env.controller.save_checkpoint(payload(), paused['revision'])
    marker = b'checkpoint-marker-private'
    for path in env.path.parent.glob('work_sessions.db*'):
        assert marker not in path.read_bytes()
    dump = '\n'.join(env.store._conn.iterdump())
    assert marker.decode() not in dump


def test_save_finalization_race_requires_current_revision(env):
    token = env.controller.begin_action()
    requested = pause(env)
    barrier = threading.Barrier(2)
    def save():
        barrier.wait(timeout=3)
        try:
            return env.controller.save_checkpoint(payload(), requested['revision'])
        except SessionConflict:
            return None
    def finish():
        barrier.wait(timeout=3)
        env.controller.finish_action(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_save = pool.submit(save)
        future_finish = pool.submit(finish)
        result = future_save.result(timeout=5)
        future_finish.result(timeout=5)
    state = env.controller.snapshot()
    assert state['state'] == 'paused' and state['running_actions'] == 0
    if result is None:
        assert not state['checkpoint_content_available']
        env.controller.save_checkpoint(payload(), state['revision'])
    assert env.controller.get_checkpoint()['available']
