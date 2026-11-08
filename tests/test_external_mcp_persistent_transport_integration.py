"""S3: ten integration cases, actual stdio/ClientSession, fake process/containment.

No server API, WorkSession wiring, SDK subprocess, tenant or external network.
Decorators do not expand the test count; init timeout/cancel share one case.
"""
import threading
import time

import pytest

from external_mcp.limits import ExternalError
from external_mcp_persistent_fakes import Gate, wait
from external_mcp_persistent_wire_fakes import CLEANUP, wire


def refusal(task, uncertain=False):
    with pytest.raises(ExternalError) as raised:
        task.result()
    assert ('uncertain' in raised.value.code) is uncertain
    return raised.value.code


def test_wire_preview_apply_share_sdk_session_and_generation(wire):
    generation = wire.discover()['runtime_generation']
    assert wire.call(generation, 'preview')['result']['content'][0]['text'] == 'preview-token'
    assert wire.call(generation, 'apply')['result']['content'][0]['text'] == 'applied'
    assert len(wire.processes) == 1
    assert sum(row.get('method') == 'initialize' for row in wire.processes[0].requests) == 1
    assert wire.snapshot()['runtime_generation'] == generation


def test_wire_contexts_enter_and_exit_in_same_owner_task(wire):
    wire.discover()
    wire.invalidate()
    wire.eventually(lambda: wire.snapshot()['state'] == 'absent')
    trace = dict(wire.trace)
    assert trace['session_enter'] == trace['session_exit']
    assert trace['transport_enter'] == trace['transport_exit']
    assert trace['session_enter'] == trace['transport_enter']


def test_wire_idle_eof_invalidates_without_next_call(wire):
    generation = wire.discover()['runtime_generation']
    wire.eof()
    wire.eventually(lambda: wire.snapshot()['runtime_generation'] is None)
    with pytest.raises(ExternalError):
        wire.call(generation)
    assert len(wire.processes) == 1


def test_wire_eof_after_dispatch_resolves_uncertain_without_retry(wire):
    generation = wire.discover()['runtime_generation']
    process = wire.processes[0]
    process.call = Gate(blocked=True)
    try:
        task = wire.launch(lambda: wire.call(generation))
        wait(process.request_seen, 'wire tools/call')
        wire.eof()
        refusal(task, uncertain=True)
        assert sum(row.get('method') == 'tools/call' for row in process.requests) == 1
        assert len(wire.processes) == 1
    finally:
        process.release()


def test_wire_initialize_timeout_and_cancel_cleanup_partial_resources(wire):
    for mode in ('timeout', 'cancel'):
        wire.init_blocked = True
        cancel = threading.Event()
        task = wire.launch(lambda: wire.discover(timeout=1, cancel_event=cancel))
        wire.eventually(lambda: len(wire.processes) > (0 if mode == 'timeout' else 1))
        process = wire.processes[-1]
        try:
            wait(process.initialize.entered, 'wire initialize')
            if mode == 'cancel':
                cancel.set()
            refusal(task)
            wire.eventually(lambda: wire.snapshot()['state'] == 'absent')
            assert wire.containments[-1].events.count('close') == 1
            assert not any(row.get('method') == 'tools/call' for row in process.requests)
        finally:
            process.release()


def test_wire_invalidation_during_validation_prevents_dispatch(wire):
    generation = wire.discover()['runtime_generation']
    wire.validation = Gate(blocked=True)
    try:
        task = wire.launch(lambda: wire.call(generation))
        wait(wire.validation.entered, 'wire validation')
        wire.invalidate()
        wire.validation.release.set()
        refusal(task)
        assert not any(row.get('method') == 'tools/call' for row in wire.processes[0].requests)
    finally:
        wire.validation.release.set()


def test_wire_admitted_call_drains_before_transport_cleanup(wire):
    generation = wire.discover()['runtime_generation']
    process = wire.processes[0]
    process.call = Gate(blocked=True)
    try:
        task = wire.launch(lambda: wire.call(generation))
        wait(process.request_seen, 'wire tools/call')
        wire.invalidate()
        assert 'close' not in wire.containments[0].events
        process.call.release.set()
        assert task.result()['result']['content'][0]['text'] == 'ok'
        wire.eventually(lambda: 'close' in wire.containments[0].events)
    finally:
        process.release()


def test_wire_session_and_transport_share_one_cleanup_deadline(wire):
    wire.discover()
    process = wire.processes[0]
    wire.session_delay = CLEANUP * 0.65
    process.close_gate = Gate(blocked=True)
    start = time.monotonic()
    try:
        wire.invalidate()
        wire.eventually(lambda: 'close' in wire.containments[0].events)
        elapsed = time.monotonic() - start
        assert elapsed <= CLEANUP + 0.12, 'transport restarted cleanup budget after session exit'
        assert wire.snapshot()['state'] == 'broken'
    finally:
        process.release()


def test_wire_hung_cleanup_stays_broken_and_refuses_replacement(wire):
    generation = wire.discover()['runtime_generation']
    process = wire.processes[0]
    process.close_gate = Gate(blocked=True)
    try:
        wire.invalidate()
        wire.eventually(lambda: wire.snapshot()['state'] == 'broken')
        with pytest.raises(ExternalError):
            wire.discover()
        with pytest.raises(ExternalError):
            wire.call(generation)
        assert len(wire.processes) == 1
    finally:
        process.release()


def test_wire_confirmed_result_survives_later_cleanup_failure(wire):
    generation = wire.discover()['runtime_generation']
    result = wire.call(generation)
    wire.containments[0].fail = True
    wire.invalidate()
    wire.eventually(lambda: wire.snapshot()['state'] == 'broken')
    assert result['result']['isError'] is False
    assert result['result']['content'][0]['text'] == 'ok'
    assert result['runtime_generation'] == generation
    assert 'PRIVATE' not in repr(wire.snapshot())
