"""S2a: ten failure-path cases; no subprocess/network or test execution implied.

Existing supervisor API is exercised: absence/setup errors are not useful RED.
Virtual contract time and real bounded joins distinguish unresolved executors
from safe refusals. Tests do not claim SDK/OS containment acceptance.
"""
import asyncio
import concurrent.futures
import threading
from contextlib import contextmanager

import anyio
import pytest

from external_mcp.limits import ExternalError
from external_mcp_persistent_fakes import Gate, rig, wait
from external_mcp_persistent_fakes import Session


@contextmanager
def released_on_exit(*gates):
    # Release failing scenarios before rig teardown even if an assertion fails.
    try:
        yield
    finally:
        for gate in gates:
            gate.release.set()


def safe_refusal(task, *, uncertain=False):
    # Concurrent Future timeout is NOT accepted as an ExternalError/refusal.
    with pytest.raises(ExternalError) as raised:
        task.result()
    assert ('uncertain' in raised.value.code) is uncertain
    assert 'PRIVATE' not in str(raised.value)
    return raised.value.code


@pytest.mark.parametrize('cause', ['timeout', 'cancel'])
def test_sdk_error_before_watchdog_poll_uses_current_local_deadline_or_cancel(rig, monkeypatch, cause):
    generation = rig.discover()['runtime_generation']
    cancel = threading.Event()
    original = Session.call_tool
    async def call(session, *args, **kwargs):
        await original(session, *args, **kwargs)
        # SDK can raise before our polling watchdog gets its next checkpoint.
        if cause == 'timeout':
            rig.clock.advance(2.1)
        else:
            cancel.set()
        raise RuntimeError('PRIVATE_SDK_ERROR')
    monkeypatch.setattr(Session, 'call_tool', call)
    task = rig.launch(lambda: rig.call(generation, timeout=2, cancel_event=cancel))
    assert safe_refusal(task, uncertain=True) == (
        'call_timeout_uncertain' if cause == 'timeout' else 'call_cancelled_uncertain')
    assert len(rig.hub.backends[0].calls) == 1


@pytest.mark.parametrize('phase', ['call', 'catalog', 'validation'])
def test_eof_resolves_active_executor_without_replay(rig, phase):
    generation = rig.discover()['runtime_generation']
    backend = rig.hub.backends[0]
    gate = Gate(blocked=True)
    setattr(backend, phase, gate)
    with released_on_exit(gate):
        task = rig.launch(lambda: rig.call(generation, timeout=60))
        wait(gate.entered, phase)
        backend.eof.set()
        safe_refusal(task, uncertain=phase == 'call')
        rig.eventually(lambda: rig.snapshot()['runtime_generation'] is None)
        assert len(backend.calls) == (1 if phase == 'call' else 0)
        with pytest.raises(ExternalError):
            rig.call(generation)
        assert len(rig.hub.backends) == 1


@pytest.mark.parametrize('how', ['timeout', 'cancel'])
def test_transport_partial_entry_closes_without_dispatch(rig, how):
    backend = rig.hub.prepare(transport_start=Gate(blocked=True))
    cancel = threading.Event()
    with released_on_exit(backend.transport_start):
        task = rig.launch(lambda: rig.discover(timeout=2, cancel_event=cancel))
        wait(backend.transport_start.entered, 'transport partial entry')
        if how == 'timeout':
            rig.tick(2.2)
        else:
            cancel.set()
        safe_refusal(task)
        rig.eventually(backend.closed.is_set, 'partial transport cleanup')
        assert backend.initializes == 0 and backend.calls == []
        assert backend.cleanup_attempts == 1
        events = dict(backend.trace)
        assert events['transport_enter'] == events['transport_exit']
        assert rig.snapshot()['state'] == 'absent'


@pytest.mark.parametrize('how', ['timeout', 'cancel'])
def test_nested_session_partial_entry_and_initialize_cleanup(rig, how):
    # One case blocks inside session __aenter__ after its task group is entered;
    # the other blocks initialize with both transport/session fully entered.
    backend = rig.hub.prepare()
    gate = Gate(blocked=True)
    if how == 'timeout':
        backend.session_start = gate
    else:
        backend.start = gate
    cancel = threading.Event()
    with released_on_exit(gate):
        task = rig.launch(lambda: rig.discover(timeout=2, cancel_event=cancel))
        wait(gate.entered, 'nested session start')
        if how == 'timeout':
            rig.tick(2.2)
        else:
            cancel.set()
        safe_refusal(task)
        rig.eventually(backend.closed.is_set, 'nested context cleanup')
        assert backend.calls == [] and backend.cleanup_attempts == 1
        names = [name for name, _ in backend.trace]
        assert names.count('session_exit') == 1
        events = dict(backend.trace)
        assert events['session_enter'] == events['session_exit']
        assert rig.snapshot()['state'] == 'absent'


def test_cancelled_start_cleanup_gets_one_fresh_shared_deadline(rig):
    backend = rig.hub.prepare(start=Gate(blocked=True), cleanup_delays=(2, 2, 2))
    cancel = threading.Event()
    with released_on_exit(backend.start):
        task = rig.launch(lambda: rig.discover(timeout=60, cancel_event=cancel))
        wait(backend.start.entered, 'initialize')
        cancel.set()
        safe_refusal(task)
        wait(backend.cleanup.entered, 'cleanup after cancelled start')
        origin = rig.clock.monotonic()
        rig.tick(4.5)
        assert rig.snapshot()['state'] == 'closing'
        assert not backend.closed.is_set()
        rig.tick(0.8)
        rig.eventually(lambda: rig.snapshot()['state'] == 'broken')
        assert rig.clock.monotonic() - origin < 5.4
        assert backend.cleanup_attempts == 1
        assert len(backend.cleanup_budgets) >= 2
        assert backend.calls == []


def test_executor_safety_timeout_cancels_pending_acquire(monkeypatch, rig):
    # Internal failure injection at the executor bridge, not a production API.
    # Keep _acquire suspended before admission. A bridge timeout must cancel it;
    # invalidating a nonexistent runtime alone cannot prevent a later spawn.
    supervisor = rig.supervisor
    supervisor._start_thread()
    original_acquire = supervisor._acquire
    original_schedule = asyncio.run_coroutine_threadsafe
    entered = threading.Event()
    cancelled = threading.Event()
    gate = Gate(blocked=True)
    scheduled = []

    async def held_acquire(owner, request):
        entered.set()
        try:
            await gate.run()
            return await original_acquire(owner, request)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    class SafetyTimeout:
        def __init__(self, future):
            self.future = future
        def result(self, timeout=None):
            wait(entered, 'pending acquire')
            raise concurrent.futures.TimeoutError()
        def cancel(self):
            return self.future.cancel()
        def __getattr__(self, name):
            return getattr(self.future, name)

    def schedule_once(coroutine, loop):
        future = original_schedule(coroutine, loop)
        if not scheduled:
            scheduled.append(future)
            return SafetyTimeout(future)
        return future

    # Scoped patch also restores before fixture shutdown, including failure.
    with released_on_exit(gate), monkeypatch.context() as patch:
        patch.setattr(supervisor, '_acquire', held_acquire)
        patch.setattr(asyncio, 'run_coroutine_threadsafe', schedule_once)
        task = rig.launch(rig.discover)
        safe_refusal(task)
        rig.eventually(cancelled.is_set, 'executor cancels pending acquisition')
        gate.release.set()
        rig.tick(0.2)
        assert scheduled[0].cancelled()
        assert rig.hub.backends == []
    rig.tick(0.2)
    assert rig.hub.backends == []


def test_shutdown_starting_resolves_executor_and_joins_owner(rig):
    backend = rig.hub.prepare(start=Gate(blocked=True))
    with released_on_exit(backend.start):
        discovery = rig.launch(lambda: rig.discover(timeout=60))
        wait(backend.start.entered, 'initialize')
        shutdown = rig.launch(rig.supervisor.shutdown)
        # Starting is not an admitted business call: shutdown must interrupt
        # preparation rather than wait its 60-second outer operation budget.
        safe_refusal(discovery)
        shutdown.result()
        assert backend.closed.is_set() and backend.calls == []
        assert not rig.supervisor.thread.is_alive()
        assert backend.cleanup_attempts == 1
