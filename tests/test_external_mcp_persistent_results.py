"""L2c result/cache ownership and TTL, real adapter/runtime with fake transport."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from external_mcp_persistent_admission_helpers import until, wait
from external_mcp_persistent_manual_lifecycle_helpers import LifecycleRig
from server.task_tools import get_task_impl
from tasks.models import TaskStatus
from tasks.volatile_results import VolatileResultStore


@pytest.fixture
def rig(tmp_path):
    instance = LifecycleRig(tmp_path / 'results')
    instance.manager._volatile.close()
    instance.manager._volatile = VolatileResultStore(clock=instance.clock.monotonic)
    instance.tools.clock = instance.clock.monotonic
    try:
        yield instance
    finally:
        instance.close()


def completed_call(rig):
    response = rig.call()
    assert response['success'], response
    tid = response['data']['task_id']
    rig.manager.resolve_approval(tid, 'allow')
    assert rig.manager.wait(tid, 5).status == TaskStatus.DONE
    until(lambda: rig.controller.snapshot()['running_actions'] == 0, 'call finalization')
    return tid


def new_owner(rig):
    state = rig.controller.snapshot()
    rig.manager.end_session(state['session_id'], state['revision'])
    until(lambda: rig.controller.snapshot()['session_id'] is None, 'old owner end')
    session = rig.sessions.create(['sklr'])
    rig.controller.bind(session.id, session.revision)
    return session.id


def test_result_reads_wait_and_discovery_do_not_extend_original_ttl(rig):
    discovery = rig.discover()
    tid = completed_call(rig)
    for advance in (100, 100, 99):
        rig.clock.advance(advance)
        assert rig.manager.wait(tid, 0).status == TaskStatus.DONE
        assert rig.manager.get_external_result(tid)['state'] == 'available'
        assert get_task_impl(rig.manager, tid)['data']['external_result']['state'] == 'available'
        assert rig.tools.list_profiles()['success']
    fresh = rig.discover()
    rig.clock.advance(1)
    assert rig.manager.get_external_result(tid)['state'] == 'unavailable'
    assert rig.manager.get_external_result(discovery.id)['state'] == 'unavailable'
    assert rig.manager.get_external_result(fresh.id)['state'] == 'available'
    assert rig.manager.get(tid).status == TaskStatus.DONE


def test_cache_reads_and_approval_do_not_extend_catalog_ttl(rig):
    rig.discover()
    rig.clock.advance(299)
    response = rig.call()
    assert response['success'], response
    rig.manager.resolve_approval(response['data']['task_id'], 'deny')
    rig.clock.advance(1)
    expired = rig.call()
    assert expired['success'] is False and expired['code'] == 'catalog_expired'
    assert rig.hub.backends[0].calls == []
    original = rig.generation
    rig.discover()
    assert rig.generation == original
    completed_call(rig)
    assert len(rig.hub.backends) == 1


def test_new_owner_cannot_read_result_or_use_old_generation_cache(rig):
    rig.discover()
    tid = completed_call(rig)
    owner = new_owner(rig)
    assert rig.manager.get_external_result(tid)['state'] != 'available'
    assert rig.call()['success'] is False
    fresh = rig.settle(rig.tools.list_tools('fake'))
    assert fresh.status == TaskStatus.DONE, fresh.error
    data = rig.manager.get_external_result(fresh.id)['data']
    assert data['runtime_generation'] != rig.generation
    assert rig.supervisor.snapshot(owner=owner,
        profile_id=rig.profile['id'])['runtime_generation'] == data['runtime_generation']
    assert rig.manager.get_external_result(tid)['state'] == 'unavailable'
    assert rig.call()['success'] is False
    assert all(not backend.calls for backend in rig.hub.backends[1:])


def test_owner_lost_during_profile_access_cannot_receive_old_result(rig, monkeypatch):
    rig.discover()
    tid = completed_call(rig)
    entered, release = threading.Event(), threading.Event()
    original = rig.registry.resolve
    reader = []
    def resolve(*args, **kwargs):
        if threading.get_ident() == reader[0]:
            entered.set()
            wait(release, 'result profile lookup release')
        return original(*args, **kwargs)
    monkeypatch.setattr(rig.registry, 'resolve', resolve)
    def get():
        reader.append(threading.get_ident())
        return rig.manager.get_external_result(tid)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(get)
        try:
            wait(entered, 'result profile lookup')
            new_owner(rig)
        finally:
            release.set()
        assert future.result(timeout=2)['state'] != 'available'


def test_result_ttl_is_rechecked_after_slow_profile_access(rig, monkeypatch):
    rig.discover()
    tid = completed_call(rig)
    original = rig.registry.resolve
    def resolve(*args, **kwargs):
        rig.clock.advance(300)
        return original(*args, **kwargs)
    monkeypatch.setattr(rig.registry, 'resolve', resolve)
    assert rig.manager.get_external_result(tid)['state'] == 'unavailable'


def test_idle_closes_runtime_but_keeps_unexpired_confirmed_result(rig):
    rig.discover()
    # Age the runtime without spending any result TTL before the call.
    rig.clock.advance(500)
    rig.discover()
    tid = completed_call(rig)
    # Idle is 600 s; result TTL is only 300 s. Move last_activity directly via
    # a separate clock domain for results to test idle without expiring it.
    result_time = rig.clock.monotonic()
    rig.manager._volatile._clock = lambda: result_time
    rig.clock.advance(600)
    rig.supervisor.reap_idle()
    until(rig.hub.backends[0].closed.is_set, 'idle runtime cleanup')
    assert rig.manager.get_external_result(tid)['state'] == 'available'
    assert rig.call()['success'] is False


def test_disable_read_denial_is_not_restored_by_reenable_and_discovery(rig):
    rig.discover()
    tid = completed_call(rig)
    rig.registry.set_enabled(rig.profile['id'], False, rig.registry.list()['revision'])
    assert rig.manager.get_external_result(tid)['state'] != 'available'
    until(rig.hub.backends[0].closed.is_set, 'disabled runtime cleanup')
    rig.registry.set_enabled(rig.profile['id'], True, rig.registry.list()['revision'])
    old = rig.generation
    rig.discover()
    assert rig.generation != old
    assert rig.manager.get_external_result(tid)['state'] == 'unavailable'
    stale = rig.tools.call_tool('fake', 'lookup', {}, expected_runtime_generation=old)
    assert stale['success'] is False
    assert rig.hub.backends[1].calls == []
