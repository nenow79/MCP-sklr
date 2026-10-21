"""P2a admission logic only, not actual TaskManager/harness integration."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from tasks.session_controller import SessionBlocked, SessionController
from tasks.session_models import SessionConflict
from tasks.session_store import SessionStore


@pytest.fixture
def env(tmp_path):
    store = SessionStore(tmp_path / 'sessions.db')
    item = store.create(['sklr'])
    controller = SessionController(store)
    yield store, item, controller
    store.close()


def test_no_automatic_selection(env):
    store, item, controller = env
    assert controller.snapshot()['session_id'] is None
    with pytest.raises(SessionBlocked):
        controller.begin_action()
    assert store.get(item.id) == item


def test_idle_pause_and_checkpoint_are_independent(env):
    _, item, controller = env
    controller.bind(item.id, 1)
    state = controller.request_pause(1)
    assert state['state'] == 'paused'
    assert state['execution_blocked'] is True
    assert state['running_actions'] == 0
    assert state['checkpoint_state'] == 'pending'
    with pytest.raises(SessionBlocked):
        controller.begin_action()
    assert controller.request_pause(state['revision']) == state


def test_started_actions_drain_without_cancel(env):
    _, item, controller = env
    controller.bind(item.id, 1)
    a, b = controller.begin_action(), controller.begin_action()
    state = controller.request_pause(1)
    assert state['state'] == 'pause_requested'
    assert state['running_actions'] == 2
    controller.finish_action(a)
    assert controller.snapshot()['state'] == 'pause_requested'
    with pytest.raises(SessionBlocked):
        controller.begin_action()
    controller.finish_action(b)
    assert controller.snapshot()['state'] == 'paused'


def test_invalid_release_never_decrements(env):
    _, item, controller = env
    controller.bind(item.id, 1)
    token = controller.begin_action()
    with pytest.raises(ValueError):
        controller.finish_action(object())
    assert controller.snapshot()['running_actions'] == 1
    controller.finish_action(token)
    with pytest.raises(ValueError):
        controller.finish_action(token)
    assert controller.snapshot()['running_actions'] == 0


def test_rebind_and_switch_cannot_bypass_pause(env):
    store, item, controller = env
    controller.bind(item.id, 1)
    state = controller.request_pause(1)
    assert controller.bind(item.id, state['revision'])['execution_blocked']
    other = store.create(['a'])
    with pytest.raises(SessionConflict):
        controller.bind(other.id, 1)
    with pytest.raises(SessionBlocked):
        controller.begin_action()


def test_binding_a_recovered_session_does_not_resume(env):
    store, item, controller = env
    store.recover_after_restart()
    controller.bind(item.id, 2)
    with pytest.raises(SessionBlocked):
        controller.begin_action()


def test_stale_pause_request_fails_closed(env):
    _, item, controller = env
    controller.bind(item.id, 1)
    with pytest.raises(SessionConflict):
        controller.request_pause(2)
    state = controller.snapshot()
    assert state['state'] == 'active' and state['execution_blocked']
    controller.bind(item.id, 1)
    with pytest.raises(SessionBlocked):
        controller.begin_action()
    assert controller.request_pause(1)['state'] == 'paused'


def test_pause_write_failure_fails_closed(env, monkeypatch):
    store, item, controller = env
    controller.bind(item.id, 1)
    original = store.request_pause
    def fail(*args):
        raise OSError('storage failure')
    monkeypatch.setattr(store, 'request_pause', fail)
    with pytest.raises(OSError):
        controller.request_pause(1)
    with pytest.raises(SessionBlocked):
        controller.begin_action()
    assert controller.snapshot()['state'] == 'active'
    monkeypatch.setattr(store, 'request_pause', original)
    assert controller.request_pause(1)['state'] == 'paused'


def test_finalization_failure_does_not_report_paused(env, monkeypatch):
    store, item, controller = env
    controller.bind(item.id, 1)
    token = controller.begin_action()
    controller.request_pause(1)
    original = store.mark_paused
    def fail(*args):
        raise OSError('storage failure')
    monkeypatch.setattr(store, 'mark_paused', fail)
    with pytest.raises(OSError):
        controller.finish_action(token)
    state = controller.snapshot()
    assert state['state'] == 'pause_requested'
    assert state['execution_blocked'] and state['running_actions'] == 0
    monkeypatch.setattr(store, 'mark_paused', original)
    assert controller.request_pause(state['revision'])['state'] == 'paused'


def test_read_failure_latches_admission_closed(env, monkeypatch):
    store, item, controller = env
    controller.bind(item.id, 1)
    original = store.get
    def fail(*args):
        raise OSError('foreign sensitive details')
    monkeypatch.setattr(store, 'get', fail)
    with pytest.raises(SessionBlocked) as exc:
        controller.begin_action()
    assert 'foreign sensitive details' not in str(exc.value)
    monkeypatch.setattr(store, 'get', original)
    assert controller.snapshot()['execution_blocked']
    with pytest.raises(SessionBlocked):
        controller.begin_action()


def test_start_pause_race_has_one_ordering(env):
    _, item, controller = env
    controller.bind(item.id, 1)
    barrier = threading.Barrier(2)
    def start():
        barrier.wait(timeout=5)
        try:
            return controller.begin_action()
        except SessionBlocked:
            return None
    def pause():
        barrier.wait(timeout=5)
        return controller.request_pause(1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        started = pool.submit(start)
        paused = pool.submit(pause)
        token = started.result(timeout=5)
        state = paused.result(timeout=5)
    assert state['execution_blocked']
    if token is None:
        assert state['state'] == 'paused' and state['running_actions'] == 0
    else:
        assert state['state'] == 'pause_requested' and state['running_actions'] == 1
        controller.finish_action(token)
    assert controller.snapshot()['state'] == 'paused'
    with pytest.raises(SessionBlocked):
        controller.begin_action()
