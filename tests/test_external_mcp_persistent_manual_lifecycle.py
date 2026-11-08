"""M1-L: 6 lifecycle cases, no processes.

Real adapter/registry/TaskManager/WorkSession/supervisor, fake transport/session.
No helper calls invalidate. Passing these tests alone does not accept spawn,
commit races, full lifecycle, SDK, main wiring or persistent activation.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from external_mcp_persistent_admission_helpers import until, wait
from tasks.models import TaskStatus

from external_mcp_persistent_manual_lifecycle_helpers import LifecycleRig


@pytest.fixture
def rig(tmp_path):
    instance = LifecycleRig(tmp_path / 'lifecycle')
    try:
        yield instance
    finally:
        instance.close()


def test_pause_automatically_closes_idle_runtime(rig):
    rig.discover()
    state = rig.controller.snapshot()
    rig.controller.request_pause(state['revision'])
    rig.assert_closed()
    response = rig.call()
    assert response['success'] is False, response
    assert len(rig.hub.backends) == 1
    assert rig.legacy.calls == []


def test_blocked_cleanup_does_not_hold_session_control_locks(rig):
    rig.discover()
    backend = rig.hub.backends[0]
    backend.cleanup.release.clear()
    state = rig.controller.snapshot()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pause = pool.submit(rig.controller.request_pause, state['revision'])
            paused = pause.result(timeout=5)
            wait(backend.cleanup.entered, 'automatic cleanup entry')
            assert not backend.closed.is_set()
            snapshot = pool.submit(rig.controller.snapshot).result(timeout=5)
            assert snapshot['execution_blocked'] and paused['execution_blocked']
    finally:
        backend.cleanup.release.set()
    rig.assert_closed()


def test_generation_pinned_invalidation_cannot_close_replacement(rig):
    rig.discover()
    current = rig.generation
    # Proposed no-I/O leaf API must compare and invalidate under one supervisor
    # lock. A snapshot comparison followed by plain invalidate is insufficient.
    changed = rig.supervisor.invalidate_generation(owner=rig.session.id,
        profile_id=rig.profile['id'], expected_runtime_generation='obsolete-generation',
        reason='profile_changed')
    assert changed is False
    state = rig.supervisor.snapshot(owner=rig.session.id, profile_id=rig.profile['id'])
    assert state['runtime_generation'] == current and state['state'] == 'ready'
    assert not rig.hub.backends[0].closed.is_set()


def test_lifecycle_worker_close_is_idempotent_and_leaves_no_thread(rig):
    rig.discover()
    # Proposed adapter lifecycle shutdown, distinct from supervisor shutdown.
    assert rig.tools.close_lifecycle() is True
    assert rig.tools.close_lifecycle() is True
    workers = [t for t in threading.enumerate()
               if t.name == 'external-mcp-lifecycle-' + str(id(rig.tools))]
    assert workers == []


@pytest.mark.parametrize('change', ['disable', 'delete'])
def test_profile_change_automatically_closes_idle_runtime(rig, change):
    rig.discover()
    revision = rig.registry.list()['revision']
    if change == 'disable':
        rig.registry.set_enabled(rig.profile['id'], False, revision)
    else:
        rig.registry.remove(rig.profile['id'], revision)
    rig.assert_closed()
    response = rig.call()
    assert response['success'] is False, response
    assert len(rig.hub.backends) == 1
    assert rig.legacy.calls == []


@pytest.mark.parametrize('change', ['pause', 'disable'])
def test_event_before_generation_tracking_still_cleans_runtime(rig, change):
    original = rig.supervisor.fetch_catalog
    def fetch(*args, **kwargs):
        response = original(*args, **kwargs)
        if change == 'pause':
            rig.controller.request_pause(rig.controller.snapshot()['revision'])
        else:
            rig.registry.set_enabled(rig.profile['id'], False,
                                     rig.registry.list()['revision'])
        return response
    rig.supervisor.fetch_catalog = fetch
    task = rig.settle(rig.tools.list_tools('fake'))
    assert task.status != TaskStatus.DONE
    rig.assert_closed()
    assert rig.tools.catalogs == {}


def test_closed_lifecycle_blocks_discovery_and_call_without_new_task(rig):
    rig.discover()
    assert rig.tools.close_lifecycle()
    rig.assert_closed()
    assert rig.tools.list_tools('fake')['success'] is False
    assert rig.call()['success'] is False
    assert len(rig.hub.backends) == 1


def test_supervisor_cannot_change_after_first_discovery(rig):
    rig.discover()
    with pytest.raises(RuntimeError):
        rig.tools.persistent_supervisor = object()


def test_supervisor_cannot_change_during_first_discovery(rig):
    original = rig.supervisor.fetch_catalog
    entered, release = threading.Event(), threading.Event()
    def fetch(*args, **kwargs):
        entered.set()
        wait(release, 'first discovery release')
        return original(*args, **kwargs)
    rig.supervisor.fetch_catalog = fetch
    response = rig.tools.list_tools('fake')
    try:
        wait(entered, 'first discovery entry')
        with pytest.raises(RuntimeError):
            rig.tools.persistent_supervisor = object()
    finally:
        if rig.tools.persistent_supervisor is not rig.supervisor:
            rig.tools.persistent_supervisor = rig.supervisor
        release.set()
        assert rig.settle(response).status == TaskStatus.DONE


def test_worker_failure_after_validation_started_refuses_dispatch(rig):
    rig.discover()
    backend = rig.hub.backends[0]
    backend.validation.release.clear()
    original = rig.supervisor.invalidate_generation
    def fail(**kwargs):
        raise RuntimeError('injected lifecycle failure')
    try:
        response = rig.call()
        tid = response['data']['task_id']
        rig.manager.resolve_approval(tid, 'allow')
        wait(backend.validation.entered, 'call validation')
        rig.supervisor.invalidate_generation = fail
        rig.tools._lifecycle.session_changed(rig.session.id, 'pause')
        until(lambda: rig.tools._lifecycle.blocked, 'worker failure latch')
        # Failed callback did not touch supervisor state. The independent
        # admission gate must refuse even with a still-ready generation.
        state = rig.supervisor.snapshot(owner=rig.session.id, profile_id=rig.profile['id'])
        assert state['runtime_generation'] == rig.generation
        backend.validation.release.set()
        done = rig.manager.wait(tid, 5)
        assert done.status == TaskStatus.FAILED, done.error
        assert 'uncertain' not in (done.error or '')
        assert backend.calls == []
        assert rig.call()['success'] is False
        assert rig.tools.list_tools('fake')['success'] is False
    finally:
        backend.validation.release.set()
        rig.supervisor.invalidate_generation = original
