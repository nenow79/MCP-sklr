"""Session/budget/journal coordination, with real stores and no external effects."""
from dataclasses import replace
import threading

import pytest

from autopilot.audit_models import AuditEvent
from autopilot.controller import AutopilotBlocked, AutopilotBusy
from autopilot.models import BudgetLimits
from autopilot.rules import parse_policy
from tasks.session_controller import ResumeOperationError
from tasks.session_models import SessionConflict
from autopilot_fixtures import environment


@pytest.fixture
def env(tmp_path):
    now = [100.0]
    with environment(tmp_path, clock=lambda: now[0]) as e:
        e.now = now
        (e.app / 'a.txt').write_text('old', encoding='utf-8')
        yield e


def refresh(e):
    return lambda binding: e.runtime.build(binding, 'read_file', {'filepath': 'a.txt', 'root': 'app'})[:2]


def start(e, ticket):
    with e.autopilot.coordination():
        e.autopilot.start_internal(ticket)


def test_approval_requires_pause_and_confirmation_never_creates_authority(env):
    policy = parse_policy({'version': 1, 'rules': [env.file_rule('read_file')]}, hmac_key=b'x' * 32)
    with pytest.raises(SessionConflict):
        env.autopilot.panel_approve(policy, BudgetLimits(), env.sessions.snapshot()['revision'])
    with pytest.raises(SessionConflict):
        env.sessions.confirm_resume_plan('Plan', 1, 'forged')
    assert env.autopilot.snapshot()['state'] == 'disabled'


def test_reserve_start_finish_counts_once_and_records_before_effect(env):
    env.approve([env.file_rule('read_file')])
    action = env.sessions.begin_action()
    ticket = env.autopilot.reserve(refresh(env))
    assert env.autopilot.snapshot()['used'] == 0
    start(env, ticket)
    assert env.audit.unresolved()[0]['operation_id'] == ticket.operation_id
    assert env.autopilot.snapshot()['used'] == 1
    with pytest.raises(AutopilotBlocked):
        start(env, ticket)
    env.autopilot.finish(ticket)
    env.sessions.finish_action(action)
    assert env.audit.unresolved() == ()
    assert env.autopilot.snapshot()['used'] == 1


def test_busy_does_not_consume_the_second_reservation(env):
    env.approve([env.file_rule('read_file')])
    action = env.sessions.begin_action()
    first, second = env.autopilot.reserve(refresh(env)), env.autopilot.reserve(refresh(env))
    start(env, first)
    with pytest.raises(AutopilotBusy):
        start(env, second)
    assert env.autopilot.snapshot()['used'] == 1 and second.phase == 'reserved'
    env.autopilot.finish(first)
    start(env, second)
    env.autopilot.finish(second)
    env.sessions.finish_action(action)
    assert env.autopilot.snapshot()['used'] == 2


@pytest.mark.parametrize('transition', ['pause', 'revoke', 'expire'])
def test_stop_before_start_prevents_admission_and_allows_release(env, transition):
    env.approve([env.file_rule('read_file')], duration_s=10)
    ticket = env.autopilot.reserve(refresh(env))
    if transition == 'pause':
        env.pause()
    elif transition == 'revoke':
        env.autopilot.revoke()
    else:
        env.now[0] += 10
        env.autopilot.tick()
    with pytest.raises(AutopilotBlocked):
        start(env, ticket)
    env.autopilot.release(ticket)
    assert env.autopilot.snapshot()['used'] == env.autopilot.snapshot()['reserved'] == 0
    assert env.sessions.snapshot()['execution_blocked']


@pytest.mark.parametrize('transition', ['pause', 'revoke', 'expire'])
def test_started_operation_can_finish_after_stop(env, transition):
    env.approve([env.file_rule('read_file')], duration_s=10)
    action = env.sessions.begin_action()
    ticket = env.autopilot.reserve(refresh(env))
    start(env, ticket)
    if transition == 'pause':
        env.pause()
    elif transition == 'revoke':
        env.autopilot.revoke()
    else:
        env.now[0] += 10
        env.autopilot.tick()
    assert env.sessions.snapshot()['running_actions'] == 1
    env.autopilot.finish(ticket)
    env.sessions.finish_action(action)
    assert env.sessions.snapshot()['state'] == 'paused'
    assert env.autopilot.snapshot()['running'] == 0


def test_last_unit_closes_new_admission_but_preserves_started_ticket(env):
    env.approve([env.file_rule('read_file')], max_operations=1)
    action = env.sessions.begin_action()
    ticket = env.autopilot.reserve(refresh(env))
    start(env, ticket)
    assert env.autopilot.snapshot()['state'] == 'exhausted'
    assert env.sessions.snapshot()['execution_blocked']
    env.autopilot.finish(ticket)
    env.sessions.finish_action(action)


def test_pause_requires_new_panel_decision_and_keeps_usage_deadline(env):
    env.approve([env.file_rule('read_file')], duration_s=10)
    ticket = env.autopilot.reserve(refresh(env))
    env.autopilot.release(ticket)
    env.pause()
    with pytest.raises(Exception):
        env.resume()
    env.now[0] += 3
    env.autopilot.panel_reconfirm(env.sessions.snapshot()['revision'])
    env.resume()
    assert env.autopilot.snapshot()['remaining_s'] == 7
    assert env.autopilot.snapshot()['used'] == 0


def test_stale_preparation_after_revocation_never_opens_gate(env):
    env.approve([env.file_rule('read_file')])
    env.pause()
    env.autopilot.panel_reconfirm(env.sessions.snapshot()['revision'])
    env.manager.prepare_session_resume('', env.sessions.snapshot()['revision'])
    context = env.sessions.get_resume_context()
    env.autopilot.revoke()
    with pytest.raises(SessionConflict):
        env.sessions.confirm_resume_plan('Plan', context['revision'], context['preparation_token'])
    assert env.sessions.snapshot()['execution_blocked']


def test_new_contract_does_not_inherit_or_reuse_old_reservation(env):
    env.approve([env.file_rule('read_file')])
    old = env.autopilot.reserve(refresh(env))
    env.pause()
    policy = parse_policy({'version': 1, 'rules': [env.file_rule('read_file')]}, hmac_key=b'x' * 32)
    env.autopilot.panel_approve(policy, BudgetLimits(), env.sessions.snapshot()['revision'])
    env.autopilot.release(old)
    env.resume()
    with pytest.raises(AutopilotBlocked):
        start(env, old)
    assert env.autopilot.snapshot()['used'] == env.autopilot.snapshot()['reserved'] == 0


def test_unknown_finalization_does_not_decrement_running_count(env):
    env.approve([env.file_rule('read_file')])
    action = env.sessions.begin_action()
    ticket = env.autopilot.reserve(refresh(env))
    start(env, ticket)
    with pytest.raises(AutopilotBlocked):
        env.autopilot.finish(object())
    assert env.autopilot.snapshot()['running'] == 1
    env.autopilot.finish(ticket)
    env.sessions.finish_action(action)


def test_failed_start_commit_blocks_effect_and_latches_recovery(env, monkeypatch):
    env.approve([env.file_rule('read_file')])
    action = env.sessions.begin_action()
    ticket = env.autopilot.reserve(refresh(env))
    original = env.audit.append
    def fail_started(event):
        if event.event == 'started':
            raise OSError('PRIVATE_STORAGE_ERROR')
        return original(event)
    monkeypatch.setattr(env.audit, 'append', fail_started)
    with pytest.raises(AutopilotBlocked, match='^storage_error$'):
        start(env, ticket)
    assert env.autopilot.snapshot()['state'] == 'recovery_required'
    monkeypatch.setattr(env.audit, 'append', original)
    # A definitely absent start row cannot be finalized; it still closes the
    # RAM running token and requires explicit recovery acknowledgement.
    with pytest.raises(AutopilotBlocked):
        env.autopilot.finish(ticket, 'failed')
    env.sessions.finish_action(action)
    env.autopilot.panel_acknowledge_recovery(env.sessions.snapshot()['revision'])
    env.autopilot.panel_manual(env.sessions.snapshot()['revision'])
    assert env.autopilot.snapshot()['state'] == 'disabled'


def test_uncertain_finish_requires_explicit_resolution_before_manual_mode(env):
    env.approve([env.file_rule('read_file')])
    action = env.sessions.begin_action()
    ticket = env.autopilot.reserve(refresh(env))
    start(env, ticket)
    env.autopilot.finish(ticket, 'uncertain')
    env.sessions.finish_action(action)
    with pytest.raises(AutopilotBlocked):
        env.autopilot.panel_manual(env.sessions.snapshot()['revision'])
    env.autopilot.panel_resolve(ticket.operation_id, env.sessions.snapshot()['revision'])
    env.autopilot.panel_manual(env.sessions.snapshot()['revision'])
    env.resume()
    assert not env.sessions.snapshot()['execution_blocked']


def test_backward_monotonic_clock_never_extends_authority(env):
    env.approve([env.file_rule('read_file')])
    env.now[0] -= 1
    env.autopilot.tick()
    assert env.autopilot.snapshot()['state'] == 'recovery_required'
    assert env.sessions.snapshot()['execution_blocked']


def test_watchdog_expires_without_another_model_call(env, monkeypatch):
    env.approve([env.file_rule('read_file')], duration_s=10)
    stopped = threading.Event()
    original = env.sessions.request_pause
    def pause(revision):
        result = original(revision)
        stopped.set()
        return result
    monkeypatch.setattr(env.sessions, 'request_pause', pause)
    env.autopilot.start_watchdog()
    env.now[0] += 10
    assert stopped.wait(3)
    assert env.autopilot.snapshot()['state'] == 'expired'
    assert env.sessions.snapshot()['execution_blocked']
