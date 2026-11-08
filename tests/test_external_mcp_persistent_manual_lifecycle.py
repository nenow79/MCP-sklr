"""M1-L: 6 lifecycle cases, no processes.

Real adapter/registry/TaskManager/WorkSession/supervisor, fake transport/session.
No helper calls invalidate. Passing these tests alone does not accept spawn,
commit races, full lifecycle, SDK, main wiring or persistent activation.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from external_mcp_persistent_admission_helpers import wait

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
