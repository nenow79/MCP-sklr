"""MCP-P2-S1: 13 lifecycle tests, partial P06/P07/P08/P09.

Fake teardown tests a shared deadline, not Windows Job Object/POSIX behavior.
No API authorization, persistent results TTL or flow operations are exercised.
"""
import threading

import pytest

from external_mcp.limits import ExternalError
from external_mcp_persistent_fakes import Gate, rig, wait


def test_idle_origin_is_end_of_actual_operation(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    task = rig.launch(lambda: rig.call(generation, timeout=60))
    wait(backend.call.entered, 'call')
    rig.tick(20)
    backend.call.release.set()
    task.result()
    rig.clock.advance(590)
    rig.supervisor.reap_idle()
    assert not backend.closed.is_set()
    assert rig.snapshot()['runtime_generation'] == generation


def test_idle_closes_at_six_hundred_seconds(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    rig.clock.advance(599)
    rig.supervisor.reap_idle()
    assert rig.snapshot()['runtime_generation'] == generation
    rig.clock.advance(1)
    rig.supervisor.reap_idle()
    rig.eventually(backend.closed.is_set)
    assert rig.snapshot()['runtime_generation'] is None
    with pytest.raises(ExternalError):
        rig.call(generation)
    assert len(rig.hub.backends) == 1


def test_idle_does_not_close_active_operation(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    rig.clock.advance(590)
    backend.call = Gate(blocked=True)
    task = rig.launch(lambda: rig.call(generation, timeout=60))
    wait(backend.call.entered, 'call')
    rig.tick(20)
    rig.supervisor.reap_idle()
    assert not backend.closed.is_set()
    backend.call.release.set()
    task.result()
    assert rig.snapshot()['runtime_generation'] == generation


def test_safe_metadata_reads_never_refresh_idle(rig):
    rig.discover()
    backend = rig.hub.backends[0]
    for _ in range(6):
        rig.clock.advance(100)
        rig.snapshot()
    rig.supervisor.reap_idle()
    rig.eventually(backend.closed.is_set)
    assert backend.lists == 1


def test_invalidation_refuses_pre_dispatch_but_drains_admitted_call(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    backend.validation = Gate(blocked=True)
    pending = rig.launch(lambda: rig.call(generation))
    wait(backend.validation.entered, 'validation')
    rig.invalidate()
    assert rig.snapshot()['runtime_generation'] is None
    backend.validation.release.set()
    with pytest.raises(ExternalError) as raised:
        pending.result()
    assert 'uncertain' not in raised.value.code and backend.calls == []
    rig.eventually(backend.closed.is_set)
    fresh = rig.discover()['runtime_generation']
    backend = rig.hub.backends[1]
    backend.call = Gate(blocked=True)
    admitted = threading.Event()
    task = rig.launch(lambda: rig.call(fresh, before_dispatch=lambda *args: admitted.set()))
    wait(admitted, 'dispatch admission')
    wait(backend.call.entered, 'call')
    rig.invalidate(reason='end')
    assert rig.snapshot()['runtime_generation'] is None
    assert not backend.closed.is_set()
    backend.call.release.set()
    assert task.result()['result']['isError'] is False
    rig.eventually(backend.closed.is_set)


def test_idle_eof_invalidates_generation_without_next_call(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    backend.eof.set()
    rig.eventually(lambda: rig.snapshot()['runtime_generation'] is None)
    rig.eventually(backend.closed.is_set)
    with pytest.raises(ExternalError):
        rig.call(generation)
    assert backend.calls == [] and len(rig.hub.backends) == 1


def test_dispatched_timeout_is_uncertain_without_retry(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    task = rig.launch(lambda: rig.call(generation, timeout=2))
    wait(backend.call.entered, 'call')
    rig.tick(2.2)
    with pytest.raises(ExternalError) as raised:
        task.result()
    assert raised.value.code == 'call_timeout_uncertain'
    rig.eventually(backend.closed.is_set)
    assert rig.snapshot()['runtime_generation'] is None
    assert len(backend.calls) == len(rig.hub.backends) == 1


def test_dispatched_cancel_and_protocol_failure_are_uncertain_without_retry(rig):
    for failure in ('cancel', 'protocol'):
        generation = rig.discover()['runtime_generation']
        backend = rig.hub.backends[-1]
        backend.call = Gate(blocked=True)
        cancel = threading.Event()
        task = rig.launch(lambda: rig.call(generation, cancel_event=cancel))
        wait(backend.call.entered, 'call')
        if failure == 'cancel':
            cancel.set()
            rig.tick(0.2)
        else:
            backend.call_error = RuntimeError('PRIVATE_UPSTREAM_TEXT')
            backend.call.release.set()
        with pytest.raises(ExternalError) as raised:
            task.result()
        assert raised.value.code == ('call_cancelled_uncertain' if failure == 'cancel'
                                     else 'call_failed_uncertain')
        assert 'PRIVATE_UPSTREAM_TEXT' not in str(raised.value)
        assert len(backend.calls) == 1
        rig.eventually(backend.closed.is_set)
        with pytest.raises(ExternalError):
            rig.call(generation)
    assert len(rig.hub.backends) == 2


def test_business_iserror_preserves_result_and_healthy_session(rig):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    backend.business_error = True
    result = rig.call(generation)
    assert result['result']['isError'] is True
    assert result['result']['content'][0]['type'] == 'text'
    assert result['result']['structuredContent'] == {'value': 'ok'}
    assert rig.snapshot()['runtime_generation'] == generation
    assert not backend.closed.is_set()
    backend.business_error = False
    assert rig.call(generation)['result']['isError'] is False
    assert len(rig.hub.backends) == 1


def test_concurrent_invalidation_and_shutdown_cleanup_once(rig):
    backend = rig.hub.prepare(cleanup=Gate(blocked=True))
    rig.discover()
    first = rig.launch(rig.invalidate)
    wait(backend.cleanup.entered, 'cleanup')
    second = rig.launch(lambda: rig.invalidate(reason='end'))
    shutdown = rig.launch(rig.supervisor.shutdown)
    backend.cleanup.release.set()
    first.result()
    second.result()
    shutdown.result()
    assert backend.closed.is_set() and backend.cleanup_attempts == 1
    assert [name for name, _ in backend.trace].count('session_exit') == 1


def test_cleanup_stages_share_a_single_five_second_deadline(rig):
    backend = rig.hub.prepare(cleanup_delays=(2, 2, 2))
    rig.discover()
    start = rig.clock.monotonic()
    rig.invalidate()
    wait(backend.cleanup.entered, 'cleanup')
    rig.tick(4.8)
    assert not backend.closed.is_set()
    rig.tick(0.4)
    rig.eventually(lambda: rig.snapshot()['state'] == 'broken')
    assert rig.clock.monotonic() - start < 5.3
    assert not backend.closed.is_set()
    assert backend.cleanup_attempts == 1
    assert len(backend.cleanup_budgets) >= 2
    assert rig.snapshot()['runtime_generation'] is None


def test_cleanup_failure_stays_broken_and_blocks_replacement(rig):
    backend = rig.hub.prepare(cleanup_error=RuntimeError('PRIVATE_CLEANUP_TEXT'))
    generation = rig.discover()['runtime_generation']
    rig.invalidate()
    rig.eventually(lambda: rig.snapshot()['state'] == 'broken')
    state = rig.snapshot()
    assert state['runtime_generation'] is None
    assert 'PRIVATE_CLEANUP_TEXT' not in repr(state)
    with pytest.raises(ExternalError):
        rig.discover()
    with pytest.raises(ExternalError):
        rig.call(generation)
    assert len(rig.hub.backends) == 1 and not backend.closed.is_set()


def test_confirmed_call_result_is_not_erased_by_later_cleanup_failure(rig):
    backend = rig.hub.prepare(cleanup_error=ExternalError('stdio_cleanup_failed'))
    generation = rig.discover()['runtime_generation']
    result = rig.call(generation)
    rig.invalidate()
    rig.eventually(lambda: rig.snapshot()['state'] == 'broken')
    assert result['result']['isError'] is False
    assert result['result']['structuredContent'] == {'value': 'ok'}
    assert result['runtime_generation'] == generation
    assert len(backend.calls) == 1
