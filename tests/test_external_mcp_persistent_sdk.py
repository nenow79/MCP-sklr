"""S4: six real local persistent SDK/process cases. Explicit approval to run required.

Real supervisor, bounded stdio, ClientSession, schema worker and OS containment.
No API/TaskManager/WorkSession, FlowAgent, network or configuration mutations.
This is not full nested-job/POSIX hard-crash or Power Automate acceptance.
"""
import json

import pytest

from external_mcp.limits import ExternalError
from external_mcp_persistent_sdk_helpers import alive, eventually, sdk_rig, text_of


def test_sdk_persistent_preview_apply_same_process_and_generation(sdk_rig):
    rig = sdk_rig
    generation = rig.discover()['runtime_generation']
    pid = rig.number('server.pid')
    preview = rig.call(generation, 'preview')
    token = text_of(preview)
    assert token
    applied = rig.call(generation, 'apply', {'token': token})
    assert text_of(applied) == 'applied'
    assert preview['runtime_generation'] == applied['runtime_generation'] == generation
    assert rig.snapshot()['runtime_generation'] == generation
    assert rig.number('server.pid') == pid and alive(pid)
    assert rig.transport_entries == 1
    assert rig.number('initialize.count') == 1
    assert rig.number('call.count') == 2


def test_sdk_persistent_discovery_reuses_process_and_generation(sdk_rig):
    rig = sdk_rig
    first = rig.discover()
    pid = rig.number('server.pid')
    second = rig.discover()
    assert first['runtime_generation'] == second['runtime_generation']
    assert first['tools'] == second['tools']
    assert rig.transport_entries == 1
    assert rig.number('initialize.count') == 1
    assert rig.number('server.pid') == pid and alive(pid)


def test_sdk_persistent_idle_eof_refuses_stale_call_without_spawn(sdk_rig):
    rig = sdk_rig
    generation = rig.discover()['runtime_generation']
    pid = rig.number('server.pid')
    eventually(lambda: rig.snapshot()['state'] == 'ready', label='runtime ready before EOF')
    (rig.folder / 'exit.signal').write_text('1', encoding='ascii')
    eventually(lambda: rig.snapshot()['runtime_generation'] is None,
               label='idle EOF did not invalidate generation')
    with pytest.raises(ExternalError) as raised:
        rig.call(generation, 'status')
    assert raised.value.code == 'runtime_generation'
    rig.assert_gone([pid])
    eventually(lambda: rig.snapshot()['state'] == 'absent',
               label='EOF cleanup did not release runtime')
    assert rig.transport_entries == 1
    assert not (rig.folder / 'call.count').exists()


def test_sdk_persistent_dispatched_timeout_uncertain_no_retry(sdk_rig):
    rig = sdk_rig
    generation = rig.discover()['runtime_generation']
    pid = rig.number('server.pid')
    invocation = rig.launch(lambda: rig.call(generation, 'slow', timeout=10))
    eventually(lambda: (rig.folder / 'dispatch.signal').exists(),
               label='slow call did not reach actual server dispatch')
    with pytest.raises(ExternalError) as raised:
        invocation.result()
    assert raised.value.code == 'call_timeout_uncertain'
    rig.assert_gone([pid])
    eventually(lambda: rig.snapshot()['state'] == 'absent', label='timeout cleanup failed')
    with pytest.raises(ExternalError) as stale:
        rig.call(generation, 'status')
    assert stale.value.code == 'runtime_generation'
    assert rig.transport_entries == 1
    assert rig.number('call.count') == 1


def test_sdk_persistent_success_then_invalidation_kills_descendant(sdk_rig):
    rig = sdk_rig
    generation = rig.discover()['runtime_generation']
    response = rig.call(generation, 'spawn_child')
    assert text_of(response) == 'spawned'
    pids = rig.pids()
    assert len(pids) == 3 and all(alive(pid) for pid in pids)
    rig.invalidate()
    rig.assert_gone(pids)
    eventually(lambda: rig.snapshot()['state'] == 'absent', label='invalidation cleanup failed')
    # Successful business result is not erased by later runtime invalidation.
    assert response['runtime_generation'] == generation
    assert text_of(response) == 'spawned'
    assert rig.transport_entries == 1


def test_sdk_persistent_shutdown_kills_descendant_and_joins_thread(sdk_rig):
    rig = sdk_rig
    generation = rig.discover()['runtime_generation']
    assert text_of(rig.call(generation, 'spawn_child')) == 'spawned'
    status = json.loads(text_of(rig.call(generation, 'status')))
    assert status['pid'] == rig.number('server.pid') and status['initialize'] == 1
    pids = rig.pids()
    assert len(pids) == 3 and all(alive(pid) for pid in pids)
    rig.launch(rig.supervisor.shutdown).result(seconds=15)
    rig.assert_gone(pids)
    assert not rig.supervisor.thread.is_alive()
    assert rig.snapshot()['state'] == 'absent'
    assert rig.transport_entries == 1
