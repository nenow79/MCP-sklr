"""L2a discovery/spawn admission on real controls with process-free transport."""
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from external_mcp_persistent_admission_helpers import until, wait
from external_mcp_persistent_fakes import Gate
from external_mcp_persistent_manual_lifecycle_helpers import LifecycleRig
from tasks.models import TaskStatus


@pytest.fixture
def rig(tmp_path):
    instance = LifecycleRig(tmp_path / 'discovery')
    try:
        yield instance
    finally:
        instance.controller._autopilot = None
        instance.close()


def change_control(rig, change):
    if change == 'pause':
        rig.controller.request_pause(rig.controller.snapshot()['revision'])
    elif change == 'disable':
        rig.registry.set_enabled(rig.profile['id'], False, rig.registry.list()['revision'])
    elif change == 'delete':
        rig.registry.remove(rig.profile['id'], rig.registry.list()['revision'])
    elif change == 'autopilot':
        # Control-state double, not acceptance of real delegation/accounting.
        with rig.controller._lock:
            rig.controller._autopilot = SimpleNamespace(engaged=True)
    elif change == 'panel':
        rig.tools.panel_available = lambda: False
    elif change == 'end':
        state = rig.controller.snapshot()
        rig.manager.end_session(state['session_id'], state['revision'])
    elif change == 'owner':
        foreign = rig.sessions.create(['sklr'])
        # Adversarial loss of the pinned selection; production bind disallows
        # switching. Do not grant discovery merely from another ACTIVE owner.
        with rig.controller._lock:
            rig.controller._selected = foreign.id
    elif change in ('connection', 'mode'):
        proposal = {'alias': 'fake', 'transport': 'stdio', 'enabled': True,
                    'command': 'never-executed', 'stdio_mode': 'persistent'}
        if change == 'connection':
            proposal['command'] = 'different-never-executed'
        else:
            proposal['stdio_mode'] = 'one_shot'
        rig.registry.update(rig.profile['id'], proposal, rig.registry.list()['revision'])


@pytest.mark.parametrize('change', ['pause', 'disable', 'delete', 'autopilot', 'panel',
                                   'owner', 'end', 'connection', 'mode'])
def test_changed_authority_after_submit_refuses_before_transport(rig, change):
    gate = Gate(blocked=True)
    original = rig.supervisor._owner
    async def owner(runtime):
        await gate.run()
        await original(runtime)
    rig.supervisor._owner = owner
    response = rig.tools.list_tools('fake')
    try:
        wait(gate.entered, 'owner before spawn')
        change_control(rig, change)
        gate.release.set()
        task = rig.settle(response)
        assert task.status != TaskStatus.DONE
        assert rig.hub.backends == []
        assert rig.legacy.calls == []
    finally:
        gate.release.set()
        rig.settle(response)
        if change == 'owner':
            with rig.controller._lock:
                rig.controller._selected = rig.session.id


@pytest.mark.parametrize('change', ['pause', 'disable'])
def test_authority_rechecked_after_runtime_preparation(rig, monkeypatch, change):
    import external_mcp.persistent_supervisor as module
    entered, release = threading.Event(), threading.Event()
    original = module.prepare_runtime
    def prepare(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        wait(release, 'runtime preparation release')
        return result
    monkeypatch.setattr(module, 'prepare_runtime', prepare)
    response = rig.tools.list_tools('fake')
    try:
        wait(entered, 'runtime preparation')
        change_control(rig, change)
        release.set()
        assert rig.settle(response).status != TaskStatus.DONE
        assert rig.hub.backends == []
    finally:
        release.set()
        rig.settle(response)


@pytest.mark.parametrize('change', ['pause', 'disable'])
def test_start_io_holds_no_control_locks_and_is_invalidated_before_ready(rig, change):
    backend = rig.hub.prepare(transport_start=Gate(blocked=True))
    response = rig.tools.list_tools('fake')
    try:
        wait(backend.transport_start.entered, 'transport partial entry')
        state = rig.supervisor.snapshot(owner=rig.session.id, profile_id=rig.profile['id'])
        assert state['state'] == 'starting' and state['runtime_generation'] is None
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(change_control, rig, change).result(timeout=2)
            snapshot = pool.submit(rig.controller.snapshot).result(timeout=2)
            assert snapshot['session_id'] == rig.session.id
            assert pool.submit(rig.registry.list).result(timeout=2)['configuration_valid']
        # Do not release the fake start barrier: lifecycle must cancel partial
        # entry and clean it, rather than wait for initialize to finish.
        until(backend.closed.is_set, 'start cancellation cleanup')
        assert rig.settle(response).status != TaskStatus.DONE
        assert backend.initializes == 0 and backend.lists == 0
        assert backend.cleanup_attempts == 1
    finally:
        backend.transport_start.release.set()
        rig.settle(response)


def test_reused_discovery_rechecks_manual_mode_before_catalog_io(rig):
    rig.discover()
    backend = rig.hub.backends[0]
    lists = backend.lists
    gate = Gate(blocked=True)
    original = rig.supervisor._perform
    async def perform(*args):
        await gate.run()
        return await original(*args)
    rig.supervisor._perform = perform
    response = rig.tools.list_tools('fake')
    try:
        wait(gate.entered, 'reused discovery before catalog')
        change_control(rig, 'autopilot')
        gate.release.set()
        assert rig.settle(response).status != TaskStatus.DONE
        assert backend.lists == lists
        assert len(rig.hub.backends) == 1
    finally:
        gate.release.set()
        rig.settle(response)


def test_control_admission_never_runs_under_supervisor_lock(rig, monkeypatch):
    original = rig.manager.admit_external_dispatch
    checks = []
    def admit(event):
        assert not rig.supervisor._lock._is_owned(), 'reverse supervisor -> control lock order'
        checks.append(threading.get_ident())
        return original(event)
    monkeypatch.setattr(rig.manager, 'admit_external_dispatch', admit)
    rig.discover()
    rig.discover()
    assert rig.supervisor.thread.ident in checks
    assert len(rig.hub.backends) == 1


@pytest.mark.parametrize('foreign', ['owner', 'event'])
def test_discovery_admission_is_bound_to_exact_executor(rig, foreign):
    original = rig.supervisor.fetch_catalog
    def fetch(*args, **kwargs):
        assert kwargs.get('discovery_admission') is not None
        if foreign == 'owner':
            kwargs['owner'] = 'foreign-session'
        else:
            kwargs['cancel_event'] = threading.Event()
        return original(*args, **kwargs)
    rig.supervisor.fetch_catalog = fetch
    task = rig.settle(rig.tools.list_tools('fake'))
    assert task.status == TaskStatus.FAILED
    assert rig.supervisor.thread is None
    assert rig.hub.backends == []
