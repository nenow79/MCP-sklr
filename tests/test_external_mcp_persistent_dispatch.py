"""P3c-B: 12 process-free cases, contract partial P04/P05/P06/P07.

Missing proposed API is a setup error, never skip or successful behavior RED.
Tests do not authorize pytest, production routing or FlowAgent operations.
"""
import threading

import pytest

from autopilot.context import CURRENT
from external_mcp.limits import ExternalError
from external_mcp_persistent_admission_helpers import Scope, wait
from external_mcp_persistent_dispatch_helpers import (
    DispatchRig, Gate, production_types, refusal,
)
from tasks.models import TaskStatus


@pytest.fixture
def rig(tmp_path):
    admission_type, supervisor_type = production_types()
    instance = DispatchRig(tmp_path / 'dispatch', admission_type, supervisor_type)
    try:
        yield instance
    finally:
        instance.close()


def ready(rig):
    attempt = rig.start()
    rig.discover(attempt)
    return attempt


@pytest.mark.parametrize('lifecycle', ['pause', 'end'])
@pytest.mark.parametrize('phase', ['slot', 'validation'])
def test_lifecycle_before_commit_refuses_without_dispatch(rig, lifecycle, phase):
    first = ready(rig)
    if phase == 'slot':
        admitted, first_scope = rig.admission(first)
        rig.backend.call = Gate(blocked=True)
        running = rig.call(first, admitted)
        wait(rig.backend.call.entered, 'first call')
        pending = rig.start()
        blocked, scope = rig.admission(pending)
        rig.slot_wait.clear()
        result = rig.call(pending, blocked)
        wait(rig.slot_wait, 'second request waiting for slot')
        calls_before = 1
    else:
        pending = first
        blocked, scope = rig.admission(pending)
        rig.backend.validation = Gate(blocked=True)
        result = rig.call(pending, blocked)
        wait(rig.backend.validation.entered, 'validation')
        calls_before = 0
    state = rig.lifecycle(lifecycle)
    assert state['execution_blocked']
    assert not pending.event.is_set()
    rig.backend.validation.release.set()
    rig.backend.call.release.set()
    refusal(result, blocked, scope)
    assert len(rig.backend.calls) == calls_before
    assert pending.task.id not in rig.commits
    if phase == 'slot':
        assert running.result(timeout=5)['result']['isError'] is False
        assert admitted.admitted and not first_scope.uncertain
        rig.finish_attempt(first)
    rig.finish_attempt(pending)
    rig.drained()


@pytest.mark.parametrize('loss', ['invalidation', 'cancel', 'deadline'])
def test_loss_between_preparation_and_commit_keeps_markers_false(rig, loss):
    attempt = ready(rig)
    entered, release = threading.Event(), attempt.barrier()
    def prepare():
        entered.set()
        wait(release, 'boundary preparation release')
    admission, scope = rig.admission(attempt, before=prepare)
    result = rig.call(attempt, admission, timeout=60)
    wait(entered, 'boundary preparation')
    # The owner loop is blocked on the test barrier: mutate from a separate
    # control thread, so this does not rely on its watchdog being scheduled.
    def lose():
        if loss == 'invalidation':
            rig.invalidate('end')
        elif loss == 'cancel':
            attempt.event.set()
        else:
            rig.clock.advance(61)
    thread, future = rig.thread(lose)
    rig.join(thread, future)
    release.set()
    refusal(result, admission, scope)
    assert rig.backend.calls == [] and rig.commits == []
    rig.finish_attempt(attempt)
    rig.drained()


@pytest.mark.parametrize('lifecycle', ['pause', 'end'])
def test_committed_call_drains_and_next_operation_refuses(rig, lifecycle):
    first = ready(rig)
    second = rig.start()
    admitted, scope = rig.admission(first)
    blocked, other_scope = rig.admission(second)
    rig.backend.call = Gate(blocked=True)
    result = rig.call(first, admitted)
    wait(rig.backend.call.entered, 'business I/O')
    assert rig.commits == [first.task.id]
    assert admitted.admitted and scope.dispatched
    state = rig.lifecycle(lifecycle)
    assert state['execution_blocked'] and state['running_actions'] == 2
    assert not first.event.is_set() and not second.event.is_set()
    refusal(rig.call(second, blocked), blocked, other_scope)
    rig.backend.call.release.set()
    assert result.result(timeout=5)['result']['isError'] is False
    assert not scope.uncertain and len(rig.backend.calls) == 1
    rig.finish_attempt(second)
    rig.finish_attempt(first)
    rig.drained()


def test_business_failure_after_commit_is_uncertain_once(rig):
    attempt = ready(rig)
    admission, scope = rig.admission(attempt)
    rig.backend.call_error = RuntimeError('fake upstream failure')
    result = rig.call(attempt, admission)
    with pytest.raises(ExternalError) as exc:
        result.result(timeout=5)
    assert 'uncertain' in exc.value.code
    assert admission.admitted and scope.dispatched and scope.uncertain
    assert rig.commits == [attempt.task.id] and len(rig.backend.calls) == 1
    rig.finish_attempt(attempt)
    rig.drained()


def test_each_call_uses_explicit_scope_not_discovery_context(rig):
    first = rig.start()
    discovery_scope = Scope(refuse=True)
    rig.discover(first, scope=discovery_scope)
    second = rig.start()
    a, scope_a = rig.admission(first)
    b, scope_b = rig.admission(second)
    assert rig.call(first, a).result(timeout=5)['result']['isError'] is False
    assert rig.call(second, b).result(timeout=5)['result']['isError'] is False
    assert scope_a.checks and scope_b.checks
    assert all(identity == rig.supervisor.thread.ident for identity in scope_a.checks + scope_b.checks)
    assert discovery_scope.checks == [] and not discovery_scope.dispatched
    assert scope_a.dispatched and scope_b.dispatched
    assert not scope_a.uncertain and not scope_b.uncertain
    assert rig.commits == [first.task.id, second.task.id]
    assert CURRENT.get() is None
    rig.finish_attempt(first)
    rig.finish_attempt(second)
    rig.drained()


def test_lock_order_and_controls_finish_while_business_io_is_blocked(rig):
    attempt = ready(rig)
    admission, scope = rig.admission(attempt)
    rig.backend.call = Gate(blocked=True)
    result = rig.call(attempt, admission)
    wait(rig.backend.call.entered, 'blocked business I/O')
    # CheckedScope and ObservedAdmission assert actual lock ownership at
    # recheck, boundary entry and commit. Control must finish before release.
    def controls():
        task = rig.manager.get(attempt.task.id)
        state = rig.lifecycle('pause')
        assert not rig.backend.call.release.is_set()
        return task, state
    thread, future = rig.thread(controls)
    task, state = rig.join(thread, future)
    assert task.status == TaskStatus.RUNNING and state['execution_blocked']
    assert not result.done()
    rig.backend.call.release.set()
    assert result.result(timeout=5)['result']['isError'] is False
    assert rig.commits == [attempt.task.id] and not scope.uncertain
    rig.finish_attempt(attempt)
    rig.drained()
