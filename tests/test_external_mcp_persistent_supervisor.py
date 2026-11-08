"""MCP-P2-S1: 17 supervisor tests; process-free fakes, no pytest run implied.

Criteria: partial P04/P07/P09. Uses the test API documented in the fake module.
No isolation claim for independent clients within the same WorkSession.
"""
import threading

import pytest

from external_mcp.limits import ExternalError
from external_mcp_persistent_fakes import Gate, rig, wait


def test_construction_and_snapshot_are_lazy(rig):
    assert rig.snapshot()['state'] == 'absent'
    assert rig.snapshot()['runtime_generation'] is None
    assert rig.hub.backends == []
    # Public safe state must expose the actual supervisor thread state for this
    # internal test seam; construction must not eagerly start its event loop.
    assert rig.supervisor.thread is None


def test_authorized_discovery_starts_and_initializes(rig):
    checked = []
    result = rig.discover(before_start=lambda: checked.append('authorized'))
    assert checked and rig.hub.backends[0].initializes == 1
    assert result['tools'][0]['name'] == 'lookup'
    assert result['runtime_generation'] == rig.snapshot()['runtime_generation']
    assert rig.snapshot()['state'] == 'ready'


def test_context_entry_and_exit_have_the_same_owner_task(rig):
    rig.discover()
    backend = rig.hub.backends[0]
    rig.invalidate()
    rig.eventually(backend.closed.is_set)
    contexts = dict(backend.trace)
    assert contexts['transport_enter'] == contexts['transport_exit']
    assert contexts['session_enter'] == contexts['session_exit']
    assert contexts['transport_enter'] == contexts['session_enter']


def test_executor_threads_use_one_dedicated_loop(rig):
    first = rig.launch(rig.discover).result()
    second = rig.launch(lambda: rig.call(first['runtime_generation'])).result()
    assert second['runtime_generation'] == first['runtime_generation']
    identities = [identity for _, identity in rig.hub.backends[0].trace]
    assert len({item[0] for item in identities}) == 1
    assert len({item[1] for item in identities}) == 1
    assert identities[0][0] != threading.get_ident()
    assert all(identities[0][0] != invocation.thread.ident
               for invocation in rig.invocations)


def test_same_owner_profile_keeps_generation_session_and_ram_token(rig):
    first = rig.discover()
    generation = first['runtime_generation']
    rig.call(generation, arguments={'q': 'preview'})
    assert rig.discover()['runtime_generation'] == generation
    applied = rig.call(generation, arguments={'q': 'apply'})
    assert applied['result']['structuredContent']['value'] == 'applied'
    assert len(rig.hub.backends) == 1
    assert rig.hub.backends[0].initializes == 1


def test_work_sessions_have_distinct_runtime_and_generation(rig):
    first = rig.discover()
    second = rig.discover(owner='work-session-B')
    assert first['runtime_generation'] != second['runtime_generation']
    assert len(rig.hub.backends) == 2
    assert rig.snapshot(owner='work-session-B')['runtime_generation'] == second['runtime_generation']


def test_foreign_owner_and_stale_generation_refuse_without_call(rig):
    generation = rig.discover()['runtime_generation']
    for owner, expected in [('work-session-B', generation), (rig.owner, 'obsolete')]:
        with pytest.raises(ExternalError):
            rig.call(expected, owner=owner)
    assert len(rig.hub.backends) == 1
    assert rig.hub.backends[0].calls == []


def test_only_new_discovery_can_replace_lost_generation(rig):
    generation = rig.discover()['runtime_generation']
    rig.invalidate()
    rig.eventually(rig.hub.backends[0].closed.is_set)
    with pytest.raises(ExternalError):
        rig.call(generation)
    assert len(rig.hub.backends) == 1
    fresh = rig.discover()['runtime_generation']
    assert fresh and fresh != generation
    assert len(rig.hub.backends) == 2
    assert rig.hub.backends[1].calls == []


def test_parallel_discovery_spawns_once(rig):
    backend = rig.hub.prepare(start=Gate(blocked=True))
    first = rig.launch(rig.discover)
    wait(backend.start.entered, 'initialize')
    second = rig.launch(rig.discover)
    backend.start.release.set()
    assert first.result()['runtime_generation'] == second.result()['runtime_generation']
    assert len(rig.hub.backends) == backend.initializes == 1


def test_slot_covers_catalog_validation_and_call(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    backend.validation = Gate(blocked=True)
    first = rig.launch(lambda: rig.call(generation))
    wait(backend.validation.entered, 'validation')
    lists = backend.lists
    second = rig.launch(lambda: rig.call(generation))
    rig.tick(0.2)
    assert backend.lists == lists and backend.calls == []
    backend.validation.release.set()
    first.result()
    second.result()
    assert len(backend.calls) == 2 and backend.maximum_active == 1
    assert len(rig.hub.backends) == 1


def test_discovery_waits_for_call_without_second_spawn(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    first = rig.launch(lambda: rig.call(generation))
    wait(backend.call.entered, 'call')
    lists = backend.lists
    second = rig.launch(rig.discover)
    rig.tick(0.2)
    assert backend.lists == lists and len(rig.hub.backends) == 1
    backend.call.release.set()
    first.result()
    assert second.result()['runtime_generation'] == generation
    assert backend.maximum_active == 1


def test_slot_timeout_does_not_dispatch_or_destroy_healthy_runtime(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    first = rig.launch(lambda: rig.call(generation, timeout=60))
    wait(backend.call.entered, 'call')
    second = rig.launch(lambda: rig.call(generation, timeout=60))
    rig.tick(8.2)
    with pytest.raises(ExternalError) as raised:
        second.result()
    assert 'uncertain' not in raised.value.code
    assert len(backend.calls) == 1 and not backend.closed.is_set()
    backend.call.release.set()
    first.result()
    assert rig.snapshot()['runtime_generation'] == generation


def test_slot_cancel_does_not_dispatch_or_destroy_healthy_runtime(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    first = rig.launch(lambda: rig.call(generation))
    wait(backend.call.entered, 'call')
    cancel = threading.Event()
    second = rig.launch(lambda: rig.call(generation, cancel_event=cancel))
    cancel.set()
    rig.tick(0.2)
    with pytest.raises(ExternalError) as raised:
        second.result()
    assert 'uncertain' not in raised.value.code
    assert len(backend.calls) == 1 and not backend.closed.is_set()
    backend.call.release.set()
    first.result()
    assert rig.snapshot()['runtime_generation'] == generation


def test_fifth_runtime_refused_without_eviction(rig):
    generations = [rig.discover(owner=f'owner-{index}')['runtime_generation']
                   for index in range(4)]
    with pytest.raises(ExternalError):
        rig.discover(owner='owner-4')
    assert len(rig.hub.backends) == 4
    assert all(not backend.closed.is_set() for backend in rig.hub.backends)
    assert [rig.snapshot(owner=f'owner-{index}')['runtime_generation']
            for index in range(4)] == generations


def test_starting_closing_and_broken_all_consume_capacity(rig):
    closing = rig.hub.prepare(cleanup=Gate(blocked=True))
    rig.discover()
    rig.invalidate()
    wait(closing.cleanup.entered, 'cleanup')
    broken = rig.hub.prepare(cleanup_error=ExternalError('stdio_cleanup_failed'))
    rig.discover(owner='broken-owner')
    rig.supervisor.invalidate(owner='broken-owner', profile_id=rig.profile['id'], reason='end')
    rig.eventually(lambda: rig.snapshot(owner='broken-owner')['state'] == 'broken')
    starting = rig.hub.prepare(start=Gate(blocked=True))
    start = rig.launch(lambda: rig.discover(owner='starting-owner'))
    wait(starting.start.entered, 'initialize')
    rig.discover(owner='ready-owner')
    with pytest.raises(ExternalError):
        rig.discover(owner='fifth-owner')
    assert len(rig.hub.backends) == 4
    starting.start.release.set()
    start.result()
    closing.cleanup.release.set()


def test_start_deadline_is_minimum_of_fifteen_and_operation_budget(rig):
    for owner, budget, deadline in [('long', 60, 15), ('short', 3, 3)]:
        backend = rig.hub.prepare(start=Gate(blocked=True))
        task = rig.launch(lambda owner=owner, budget=budget:
                          rig.discover(owner=owner, timeout=budget))
        wait(backend.start.entered, 'initialize')
        rig.tick(deadline - 0.2)
        assert not task.future.done()
        rig.tick(0.4)
        with pytest.raises(ExternalError) as raised:
            task.result()
        assert 'uncertain' not in raised.value.code
        assert backend.calls == []
        backend.start.release.set()
        rig.eventually(backend.closed.is_set)


def test_slot_and_subsequent_phases_share_one_operation_budget(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    first = rig.launch(lambda: rig.call(generation, timeout=60))
    wait(backend.call.entered, 'call')
    second = rig.launch(lambda: rig.call(generation, timeout=2))
    rig.tick(2.2)
    with pytest.raises(ExternalError):
        second.result()
    assert len(backend.calls) == 1
    backend.call.release.set()
    first.result()
    backend.catalog = Gate(blocked=True)
    backend.call = Gate(blocked=True)
    third = rig.launch(lambda: rig.call(generation, timeout=3))
    wait(backend.catalog.entered, 'catalog')
    rig.tick(2)
    backend.catalog.release.set()
    wait(backend.call.entered, 'call')
    rig.tick(1.2)
    with pytest.raises(ExternalError) as raised:
        third.result()
    assert raised.value.code == 'call_timeout_uncertain'
