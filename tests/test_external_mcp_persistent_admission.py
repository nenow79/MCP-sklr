"""P3c-A RED: exact executor authority and pause/end dispatch linearization.

Criteria: persistent contract P04/P05/P06/P07. No production wiring, network,
subprocess, SDK or real delegation. Missing API is reported at fixture setup,
not hidden by skip or a collection-time import. RED has not yet been executed.
"""
import importlib
import threading
from dataclasses import FrozenInstanceError

import pytest

from autopilot.context import CURRENT
from external_mcp.limits import ExternalError
from external_mcp_persistent_admission_helpers import (
    SCHEMA_SHA, TOOL, Scope, rig_at, wait,
)
from tasks.manager import TaskStateError
from tasks.models import TaskStatus
from tasks.session_controller import SessionBlocked


@pytest.fixture
def admission_type():
    # Deliberately missing until a separately approved minimal implementation.
    return importlib.import_module('external_mcp.persistent_admission').DispatchAdmission


@pytest.fixture
def env(tmp_path):
    with rig_at(tmp_path / 'admission') as rig:
        yield rig


def make_admission(admission_type, env, attempt, scope=None):
    scope = Scope() if scope is None else scope
    # Capture CURRENT on the executor thread; never inherit it at discovery.
    def capture(event):
        marker = CURRENT.set(scope)
        try:
            return admission_type(env.manager, event, CURRENT.get())
        finally:
            CURRENT.reset(marker)
    return attempt.run(capture), scope


def refused(admission):
    with pytest.raises(ExternalError) as exc:
        admission.guard(TOOL, SCHEMA_SHA)
    assert exc.value.code == 'dispatch_guard'
    assert not admission.admitted


def test_ticket_pins_exact_event_owner_is_immutable_and_adds_no_token(env, admission_type, monkeypatch):
    attempt = env.start()
    link = env.sessions.get_task_link(attempt.task.id)
    assert link.session_id == env.session.id
    original = env.controller.admit_dispatch
    visited = []
    def ordered(task_id):
        # Condition ownership is a local test seam, not a public tool input.
        assert env.manager._cond._is_owned(), 'Controller entered before manager'
        visited.append(task_id)
        return original(task_id)
    monkeypatch.setattr(env.controller, 'admit_dispatch', ordered)
    before = env.controller.snapshot()['running_actions']
    ticket = attempt.run(lambda event: env.manager.admit_external_dispatch(event))
    assert ticket.task_id == attempt.task.id
    assert ticket.session_id == link.session_id
    assert ticket.consent == 'approved'
    with pytest.raises((FrozenInstanceError, AttributeError)):
        ticket.session_id = 'not-the-owner'
    assert visited == [attempt.task.id]
    assert env.controller.snapshot()['running_actions'] == before == 1
    with pytest.raises((TaskStateError, SessionBlocked)):
        env.manager.admit_external_dispatch(threading.Event())
    attempt.finish()
    env.drained()
    with pytest.raises((TaskStateError, SessionBlocked)):
        env.manager.admit_external_dispatch(attempt.event)
    assert env.controller.snapshot()['running_actions'] == 0


@pytest.mark.parametrize('lifecycle', ['pause', 'end'])
def test_lifecycle_after_executor_start_before_dispatch_refuses_call(env, admission_type, lifecycle):
    attempt = env.start()
    admission, scope = make_admission(admission_type, env, attempt)
    assert not admission.admitted and not scope.dispatched
    state = getattr(env, lifecycle)()
    assert state['execution_blocked'] and state['running_actions'] == 1
    assert state['state'] == ('pause_requested' if lifecycle == 'pause' else 'ending')
    attempt.run(lambda event: refused(admission))
    admission.mark_uncertain_if_admitted()
    assert attempt.calls == [] and not scope.dispatched and not scope.uncertain
    assert not attempt.event.is_set()
    attempt.finish()
    env.drained()
    state = env.controller.snapshot()
    assert state['execution_blocked']
    if lifecycle == 'pause':
        assert state['state'] == 'paused'
    else:
        assert state['session_id'] is None
        assert env.sessions.get(env.session.id).lifecycle == 'ended'


@pytest.mark.parametrize('lifecycle', ['pause', 'end'])
def test_admitted_call_drains_and_new_admission_is_blocked(env, admission_type, lifecycle):
    first, second = env.start(), env.start()
    admitted, scope = make_admission(admission_type, env, first)
    blocked, blocked_scope = make_admission(admission_type, env, second)
    entered, release = threading.Event(), first.barrier()
    def business(event):
        assert admitted.guard(TOOL, SCHEMA_SHA) is None
        first.calls.append('sent-once')
        entered.set()
        wait(release, 'admitted fake call release')
        assert not event.is_set()
        return 'confirmed-result'
    result = first.command(business)
    wait(entered, 'admitted fake call')
    state = getattr(env, lifecycle)()
    assert state['execution_blocked'] and state['running_actions'] == 2
    second.run(lambda event: refused(blocked))
    assert not blocked_scope.dispatched and not blocked_scope.uncertain
    second.finish()
    # The first real executor's original action token remains during drain.
    assert env.controller.snapshot()['running_actions'] >= 1
    release.set()
    assert result.result(timeout=5) == 'confirmed-result'
    assert scope.dispatched and not scope.uncertain and admitted.admitted
    assert first.calls == ['sent-once']
    first.finish()
    env.drained()
    assert env.controller.snapshot()['execution_blocked']


def test_manual_consent_is_for_exact_running_task_not_another_pending_task(env, admission_type):
    approved = env.start()
    authorized = env.start(approved=False)
    pending = env.manager.submit(
        {'kind': 'external_mcp', 'sensitive': True, 'offer_trust': False},
        lambda event: pytest.fail('Unapproved executor ran'), True)
    assert env.manager.get(pending.id).status == TaskStatus.AWAITING_APPROVAL
    # Private event read is a negative capability test; no user consent is set.
    pending_event = env.manager._ctx[pending.id].cancel_event
    with pytest.raises((TaskStateError, SessionBlocked)):
        env.manager.admit_external_dispatch(pending_event)
    assert env.manager.external_approval_granted(approved.event)
    assert not env.manager.external_approval_granted(authorized.event)
    good, scope = make_admission(admission_type, env, approved)
    assert approved.run(lambda event: good.guard(TOOL, SCHEMA_SHA)) is None
    assert scope.dispatched
    not_approved, other_scope = make_admission(admission_type, env, authorized)
    authorized.run(lambda event: refused(not_approved))
    assert not other_scope.dispatched
    assert env.manager.get(pending.id).status == TaskStatus.AWAITING_APPROVAL
    assert env.manager.cancel(pending.id).status == TaskStatus.CANCELLED
    approved.finish()
    authorized.finish()
    env.drained()


@pytest.mark.parametrize('trust', [None, False, True])
def test_authorized_executor_requires_explicit_current_trust_check(env, admission_type, trust):
    attempt = env.start(approved=False)
    admission, scope = make_admission(admission_type, env, attempt)
    ticket = attempt.run(lambda event: env.manager.admit_external_dispatch(event))
    assert ticket.consent == 'authorized'
    checks = []
    def trust_check(name, schema_sha):
        checks.append((name, schema_sha))
        return trust
    def guard(event):
        return admission.guard(TOOL, SCHEMA_SHA,
                               trust_check=None if trust is None else trust_check)
    if trust is True:
        assert attempt.run(guard) is None
        assert admission.admitted and scope.dispatched
    else:
        with pytest.raises(ExternalError) as exc:
            attempt.run(guard)
        assert exc.value.code == 'dispatch_guard'
        assert not admission.admitted and not scope.dispatched
    assert checks == ([] if trust is None else [(TOOL, SCHEMA_SHA)])
    assert not scope.uncertain
    attempt.finish()
    env.drained()


def test_scope_is_explicit_per_operation_on_empty_context_thread(env, admission_type):
    first, second = env.start(), env.start()
    a, first_scope = make_admission(admission_type, env, first)
    b, second_scope = make_admission(admission_type, env, second)
    ambient = Scope(refuse=True)
    def supervisor_like():
        assert CURRENT.get() is None
        marker = CURRENT.set(ambient)
        try:
            assert a.guard(TOOL, SCHEMA_SHA) is None
            assert CURRENT.get() is ambient
            assert b.guard(TOOL, SCHEMA_SHA) is None
            assert CURRENT.get() is ambient
        finally:
            CURRENT.reset(marker)
        assert CURRENT.get() is None
        return threading.get_ident()
    thread, future = env.thread(supervisor_like)
    identity = env.join(thread, future)
    assert first_scope.checks == [identity] and second_scope.checks == [identity]
    assert first_scope.dispatched and second_scope.dispatched
    assert ambient.checks == [] and not ambient.dispatched and not ambient.uncertain
    assert not first_scope.uncertain and not second_scope.uncertain
    first.finish()
    second.finish()
    env.drained()


def test_recheck_refusal_is_sanitized_not_dispatched_or_uncertain_and_restores_context(env, admission_type):
    attempt = env.start()
    scope = Scope(refuse=True)
    admission, scope = make_admission(admission_type, env, attempt, scope)
    def empty_context():
        assert CURRENT.get() is None
        refused(admission)
        admission.mark_uncertain_if_admitted()
        assert CURRENT.get() is None
    thread, future = env.thread(empty_context)
    env.join(thread, future)
    assert len(scope.checks) == 1
    assert not scope.dispatched and not scope.uncertain and attempt.calls == []
    attempt.finish()
    env.drained()


@pytest.mark.parametrize('admit', [False, True])
def test_business_failure_is_uncertain_only_after_admission_no_retry(env, admission_type, admit):
    attempt = env.start()
    admission, scope = make_admission(admission_type, env, attempt)
    if not admit:
        env.pause()
    def operation(event):
        try:
            admission.guard(TOOL, SCHEMA_SHA)
            attempt.calls.append('one-call')
            raise RuntimeError('fake business failure')
        except Exception:
            admission.mark_uncertain_if_admitted()
        return admission.admitted
    thread, future = env.thread(lambda: operation(attempt.event))
    assert env.join(thread, future) is admit
    assert scope.dispatched is admit and scope.uncertain is admit
    assert attempt.calls == (['one-call'] if admit else [])
    assert CURRENT.get() is None
    attempt.finish()
    env.drained()


def test_pause_dispatch_race_has_only_admission_or_refusal_without_deadlock(tmp_path, admission_type):
    # Repeated simultaneous entry, not a claim that scheduling covers all races.
    for iteration in range(12):
        with rig_at(tmp_path / ('race_' + str(iteration))) as env:
            attempt = env.start()
            admission, scope = make_admission(admission_type, env, attempt)
            barrier = threading.Barrier(3, timeout=5)
            def dispatch():
                barrier.wait()
                try:
                    assert admission.guard(TOOL, SCHEMA_SHA) is None
                except ExternalError as exc:
                    assert exc.code == 'dispatch_guard'
                    return False
                attempt.calls.append('one-call')
                return True
            def pause():
                barrier.wait()
                return env.pause()
            dispatch_thread, dispatch_result = env.thread(dispatch)
            pause_thread, pause_result = env.thread(pause)
            barrier.wait()
            sent = env.join(dispatch_thread, dispatch_result)
            paused = env.join(pause_thread, pause_result)
            assert paused['execution_blocked'] and paused['running_actions'] == 1
            assert admission.admitted is sent and scope.dispatched is sent
            assert attempt.calls == (['one-call'] if sent else [])
            assert not scope.uncertain and not attempt.event.is_set()
            attempt.finish()
            env.drained()
            assert env.controller.snapshot()['state'] == 'paused'


def test_manager_status_and_pause_do_not_wait_for_admitted_business_io(env, admission_type):
    attempt = env.start()
    admission, scope = make_admission(admission_type, env, attempt)
    entered, release = threading.Event(), attempt.barrier()
    def business(event):
        assert admission.guard(TOOL, SCHEMA_SHA) is None
        attempt.calls.append('one-call')
        entered.set()
        wait(release, 'blocked fake I/O release')
        assert not event.is_set()
        return 'ok'
    result = attempt.command(business)
    wait(entered, 'blocked fake I/O')
    # Controls must finish while I/O is still blocked. No speed benchmark or
    # lock-held sleep: one safety timeout only detects unintended waiting.
    def controls():
        task = env.manager.get(attempt.task.id)
        state = env.controller.snapshot()
        paused = env.pause()
        assert not release.is_set()
        return task, state, paused
    thread, future = env.thread(controls)
    task, state, paused = env.join(thread, future)
    assert task.status == TaskStatus.RUNNING
    assert state['running_actions'] == 1
    assert paused['execution_blocked'] and paused['running_actions'] == 1
    assert not result.done()
    release.set()
    assert result.result(timeout=5) == 'ok'
    assert scope.dispatched and not scope.uncertain and attempt.calls == ['one-call']
    attempt.finish()
    env.drained()
    assert env.controller.snapshot()['state'] == 'paused'
