"""L2b call lifecycle on real adapter/control/runtime; process-free transport.

Pre-commit refusal and post-commit drain are separate outcomes. No fake
admission grants, SDK subprocess, external network or real profiles are used.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from external_mcp.limits import ExternalError
from external_mcp.persistent_resources import ResourceDispatchAdmission
from external_mcp_persistent_admission_helpers import until, wait
from external_mcp_persistent_fakes import Gate, Session
from external_mcp_persistent_manual_lifecycle_helpers import LifecycleRig
from tasks.models import TaskStatus


INVALIDATING = ('pause', 'end', 'disable', 'delete', 'connection', 'mode')
METADATA = ('alias', 'description')


@pytest.fixture
def rig(tmp_path):
    instance = LifecycleRig(tmp_path / 'call-lifecycle')
    try:
        yield instance
    finally:
        instance.controller._autopilot = None
        instance.close()


def change_profile(rig, change):
    proposal = {'alias': 'fake', 'transport': 'stdio', 'enabled': True,
                'command': 'never-executed', 'stdio_mode': 'persistent'}
    if change == 'alias':
        proposal['alias'] = 'renamed'
    elif change == 'description':
        proposal['description'] = 'new local description'
    elif change == 'connection':
        proposal['command'] = 'different-never-executed'
    elif change == 'mode':
        proposal['stdio_mode'] = 'one_shot'
    rig.registry.update(rig.profile['id'], proposal, rig.registry.list()['revision'])


def change_control(rig, change):
    if change == 'pause':
        rig.controller.request_pause(rig.controller.snapshot()['revision'])
    elif change == 'end':
        state = rig.controller.snapshot()
        rig.manager.end_session(state['session_id'], state['revision'])
    elif change == 'disable':
        rig.registry.set_enabled(rig.profile['id'], False, rig.registry.list()['revision'])
    elif change == 'delete':
        rig.registry.remove(rig.profile['id'], rig.registry.list()['revision'])
    elif change == 'panel':
        rig.tools.panel_available = lambda: False
    elif change == 'autopilot':
        # Control-state double only, not real delegation acceptance.
        with rig.controller._lock:
            rig.controller._autopilot = SimpleNamespace(engaged=True)
    else:
        change_profile(rig, change)


def allow_call(rig, *, timeout=60, alias='fake'):
    response = rig.tools.call_tool(alias, 'lookup', {'q': 'local'}, timeout=timeout,
        expected_runtime_generation=rig.generation)
    assert response['success'], response
    tid = response['data']['task_id']
    assert rig.manager.get(tid).status == TaskStatus.AWAITING_APPROVAL
    rig.manager.resolve_approval(tid, 'allow')
    return tid


def settle(rig, tid):
    task = rig.manager.wait(tid, 5)
    assert task.is_terminal, task.status
    return task


@pytest.mark.parametrize('change', INVALIDATING + METADATA + ('panel', 'autopilot'))
def test_change_during_validation_refuses_without_business_dispatch(rig, change):
    rig.discover()
    backend = rig.hub.backends[0]
    backend.validation = Gate(blocked=True)
    tid = allow_call(rig)
    try:
        wait(backend.validation.entered, 'pre-commit validation')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(change_control, rig, change).result(timeout=2)
            pool.submit(rig.controller.snapshot).result(timeout=2)
            pool.submit(rig.registry.list).result(timeout=2)
        backend.validation.release.set()
        task = settle(rig, tid)
        assert task.status == TaskStatus.FAILED, task.error
        assert 'uncertain' not in task.error
        assert backend.calls == []
        if change in INVALIDATING:
            until(backend.closed.is_set, 'pre-commit cleanup')
            assert backend.cleanup_attempts == 1
        else:
            assert not backend.closed.is_set()
        assert rig.legacy.calls == []
    finally:
        backend.validation.release.set()
        settle(rig, tid)


@pytest.mark.parametrize('change', INVALIDATING + METADATA)
def test_admitted_call_drains_and_keeps_confirmed_status(rig, change):
    rig.discover()
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    tid = allow_call(rig)
    try:
        wait(backend.call.entered, 'admitted call I/O')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(change_control, rig, change).result(timeout=2)
            pool.submit(rig.controller.snapshot).result(timeout=2)
            pool.submit(rig.registry.list).result(timeout=2)
        if change in INVALIDATING:
            until(lambda: rig.supervisor.snapshot(owner=rig.session.id,
                profile_id=rig.profile['id'])['state'] == 'closing', 'call invalidation')
        assert rig.manager.get(tid).status == TaskStatus.RUNNING
        assert not backend.closed.is_set() and backend.cleanup_attempts == 0
        backend.call.release.set()
        task = settle(rig, tid)
        assert task.status == TaskStatus.DONE, task.error
        assert len(backend.calls) == 1
        if change in INVALIDATING:
            until(backend.closed.is_set, 'post-drain cleanup')
            assert backend.cleanup_attempts == 1
        else:
            assert not backend.closed.is_set()
            assert rig.supervisor.snapshot(owner=rig.session.id,
                profile_id=rig.profile['id'])['runtime_generation'] == rig.generation
        if change == 'end':
            until(lambda: rig.controller.snapshot()['session_id'] is None, 'session end')
        result = rig.manager.get_external_result(tid)
        if change in ('pause', 'description'):
            assert result['state'] == 'available', result
            assert result['data']['runtime_generation'] == rig.generation
            assert json.loads(result['data']['content'])['result']['isError'] is False
        else:
            assert result['state'] != 'available', result
        assert len(rig.hub.backends) == 1 and rig.legacy.calls == []
    finally:
        backend.call.release.set()
        settle(rig, tid)


@pytest.mark.parametrize('change', ['pause', 'end', 'disable'])
def test_change_in_commit_to_send_gap_still_drains_once(rig, monkeypatch, change):
    rig.discover()
    backend = rig.hub.backends[0]
    gate = Gate(blocked=True)
    original = Session.call_tool
    async def call(session, *args, **kwargs):
        await gate.run()
        return await original(session, *args, **kwargs)
    monkeypatch.setattr(Session, 'call_tool', call)
    tid = allow_call(rig)
    try:
        wait(gate.entered, 'after commit before fake call entry')
        assert backend.calls == []
        change_control(rig, change)
        until(lambda: rig.supervisor.snapshot(owner=rig.session.id,
            profile_id=rig.profile['id'])['state'] == 'closing', 'gap invalidation')
        gate.release.set()
        assert settle(rig, tid).status == TaskStatus.DONE
        until(backend.closed.is_set, 'gap drain cleanup')
        assert len(backend.calls) == backend.cleanup_attempts == 1
    finally:
        gate.release.set()
        settle(rig, tid)


@pytest.mark.parametrize('failure', ['protocol', 'timeout'])
def test_failure_after_commit_and_pause_is_uncertain_without_retry(rig, failure):
    rig.discover()
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    tid = allow_call(rig, timeout=1 if failure == 'timeout' else 60)
    try:
        wait(backend.call.entered, 'call before failure')
        change_control(rig, 'pause')
        if failure == 'protocol':
            backend.call_error = RuntimeError('PRIVATE_UPSTREAM_TEXT')
            backend.call.release.set()
        else:
            rig.clock.advance(1.1)
        task = settle(rig, tid)
        assert task.status == TaskStatus.FAILED, task.error
        assert 'uncertain' in task.error
        assert 'PRIVATE_UPSTREAM_TEXT' not in task.error
        until(backend.closed.is_set, 'uncertain cleanup')
        assert len(backend.calls) == backend.cleanup_attempts == len(rig.hub.backends) == 1
    finally:
        backend.call.release.set()
        settle(rig, tid)


def test_confirmed_result_survives_later_cleanup_failure(rig):
    rig.discover()
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    backend.cleanup_error = ExternalError('stdio_cleanup_failed')
    tid = allow_call(rig)
    try:
        wait(backend.call.entered, 'call before cleanup failure')
        change_control(rig, 'pause')
        backend.call.release.set()
        assert settle(rig, tid).status == TaskStatus.DONE
        until(lambda: rig.supervisor.snapshot(owner=rig.session.id,
            profile_id=rig.profile['id'])['state'] == 'broken', 'broken cleanup')
        result = rig.manager.get_external_result(tid)
        assert result['state'] == 'available', result
        assert json.loads(result['data']['content'])['result']['isError'] is False
        assert rig.manager.get(tid).status == TaskStatus.DONE
        assert len(backend.calls) == backend.cleanup_attempts == 1
        assert not backend.closed.is_set()
    finally:
        backend.call.release.set()
        settle(rig, tid)


@pytest.mark.parametrize('change', METADATA)
def test_metadata_edit_keeps_runtime_but_requires_fresh_catalog(rig, change):
    rig.discover()
    backend = rig.hub.backends[0]
    change_profile(rig, change)
    alias = 'renamed' if change == 'alias' else 'fake'
    response = rig.tools.call_tool(alias, 'lookup', {'q': 'local'},
        expected_runtime_generation=rig.generation)
    assert response['success'] is False
    assert backend.calls == []
    task = rig.settle(rig.tools.list_tools(alias))
    assert task.status == TaskStatus.DONE, task.error
    result = rig.manager.get_external_result(task.id)
    assert result['data']['runtime_generation'] == rig.generation
    tid = allow_call(rig, alias=alias)
    assert settle(rig, tid).status == TaskStatus.DONE
    assert len(rig.hub.backends) == len(backend.calls) == 1
    assert not backend.closed.is_set()


def test_pause_refuses_slot_waiter_while_admitted_call_drains(rig, monkeypatch):
    rig.discover()
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    first = allow_call(rig)
    wait(backend.call.entered, 'first admitted call')
    entered = threading.Event()
    original = rig.supervisor._acquire
    async def acquire(*args):
        entered.set()
        return await original(*args)
    monkeypatch.setattr(rig.supervisor, '_acquire', acquire)
    second = allow_call(rig)
    try:
        wait(entered, 'second executor waiting for slot')
        change_control(rig, 'pause')
        task = settle(rig, second)
        assert task.status == TaskStatus.FAILED and 'uncertain' not in task.error
        assert rig.manager.get(first).status == TaskStatus.RUNNING
        backend.call.release.set()
        assert settle(rig, first).status == TaskStatus.DONE
        until(backend.closed.is_set, 'drain with slot waiter cleanup')
        assert len(backend.calls) == backend.cleanup_attempts == len(rig.hub.backends) == 1
    finally:
        backend.call.release.set()
        settle(rig, first)
        settle(rig, second)


def test_panel_is_rechecked_at_commit_after_initial_resource_checks(rig, monkeypatch):
    rig.discover()
    backend = rig.hub.backends[0]
    original = ResourceDispatchAdmission._before_commit
    def before_commit(admission, *args):
        rig.tools.panel_available = lambda: False
        return original(admission, *args)
    monkeypatch.setattr(ResourceDispatchAdmission, '_before_commit', before_commit)
    tid = allow_call(rig)
    task = settle(rig, tid)
    assert task.status == TaskStatus.FAILED and 'uncertain' not in task.error
    assert backend.calls == []
    assert not backend.closed.is_set()


@pytest.mark.parametrize('change', ['pause', 'connection'])
def test_commit_wins_against_control_contending_for_retained_locks(rig, monkeypatch, change):
    rig.discover()
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    entered, release, contending = threading.Event(), threading.Event(), threading.Event()
    original = ResourceDispatchAdmission._before_commit
    def before_commit(admission, *args):
        entered.set()
        wait(release, 'local commit release')
        return original(admission, *args)
    monkeypatch.setattr(ResourceDispatchAdmission, '_before_commit', before_commit)
    tid = allow_call(rig)
    try:
        wait(entered, 'local commit with retained locks')
        with ThreadPoolExecutor(max_workers=1) as pool:
            def control():
                contending.set()
                change_control(rig, change)
            pending = pool.submit(control)
            try:
                wait(contending, 'control lock contender')
                assert not pending.done()
            finally:
                release.set()
            pending.result(timeout=2)
        wait(backend.call.entered, 'admitted call after control contention')
        assert rig.manager.get(tid).status == TaskStatus.RUNNING
        backend.call.release.set()
        assert settle(rig, tid).status == TaskStatus.DONE
        until(backend.closed.is_set, 'contended commit cleanup')
        assert len(backend.calls) == backend.cleanup_attempts == 1
    finally:
        release.set()
        backend.call.release.set()
        settle(rig, tid)


@pytest.mark.parametrize('admitted', [False, True])
def test_cancel_preserves_uncertainty_only_after_persistent_dispatch(rig, admitted):
    rig.discover()
    backend = rig.hub.backends[0]
    gate = Gate(blocked=True)
    if admitted:
        backend.call = gate
    else:
        backend.validation = gate
    tid = allow_call(rig)
    try:
        wait(gate.entered, 'cancel barrier')
        rig.manager.cancel(tid)
        task = settle(rig, tid)
        assert task.status == TaskStatus.CANCELLED
        assert ('call_cancelled_uncertain' in task.error) is admitted
        assert len(backend.calls) == int(admitted)
    finally:
        gate.release.set()
        settle(rig, tid)
