"""Budget lifecycle and race invariants with a controlled clock; no executors."""
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from autopilot.budget import BudgetError, BudgetLedger
from autopilot.models import BudgetLimits, ContractBinding


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


@pytest.fixture
def env():
    binding = ContractBinding('a' * 32, 'b' * 32, 1, 1, 'c' * 64)
    clock = Clock()
    ledger = BudgetLedger(binding, BudgetLimits(duration_s=10, max_operations=3),
                          approved_at=clock.value, clock=clock)
    return binding, clock, ledger


def expect_code(code, function, *args):
    with pytest.raises(BudgetError) as exc:
        function(*args)
    assert str(exc.value) == code


def test_prepared_does_not_admit_work(env):
    binding, _, ledger = env
    assert ledger.snapshot()['state'] == 'prepared'
    expect_code('prepared', ledger.reserve, binding)
    assert ledger.snapshot()['used'] == 0
    ledger.activate(binding)
    assert ledger.snapshot()['execution_blocked'] is False


def test_reservation_release_and_single_charge(env):
    binding, _, ledger = env
    ledger.activate(binding)
    tokens = [ledger.reserve(binding) for _ in range(3)]
    expect_code('budget_reserved', ledger.reserve, binding)
    assert ledger.snapshot()['state'] == 'active'
    ledger.release(binding, tokens.pop())
    expect_code('invalid_token', ledger.start, binding, object())
    token = ledger.reserve(binding)
    ledger.start(binding, token)
    assert ledger.snapshot()['used'] == 1
    assert ledger.snapshot()['reserved'] == 2
    assert ledger.snapshot()['running'] == 1
    expect_code('invalid_token', ledger.start, binding, token)
    expect_code('invalid_token', ledger.release, binding, token)
    ledger.finish(binding, token)
    expect_code('invalid_token', ledger.finish, binding, token)
    assert ledger.snapshot()['used'] == 1
    assert ledger.snapshot()['running'] == 0


def test_busy_keeps_reservation_and_does_not_consume_operation(env):
    binding, _, ledger = env
    ledger.activate(binding)
    first, second = ledger.reserve(binding), ledger.reserve(binding)
    ledger.start(binding, first)
    expect_code('busy', ledger.start, binding, second)
    assert ledger.snapshot()['reserved'] == 1
    assert ledger.snapshot()['used'] == 1
    ledger.finish(binding, first)
    ledger.start(binding, second)
    ledger.finish(binding, second)
    assert ledger.snapshot()['used'] == 2


def test_last_operation_blocks_admission_but_can_finish(env):
    binding, clock, _ = env
    ledger = BudgetLedger(binding, BudgetLimits(max_operations=1), approved_at=100, clock=clock)
    ledger.activate(binding)
    token = ledger.reserve(binding)
    ledger.start(binding, token)
    assert ledger.snapshot()['state'] == 'exhausted'
    assert ledger.snapshot()['execution_blocked']
    expect_code('exhausted', ledger.reserve, binding)
    expect_code('exhausted', ledger.activate, binding)
    ledger.finish(binding, token)
    assert ledger.snapshot()['used'] == 1 and ledger.snapshot()['running'] == 0


def test_suspend_and_reactivate_keep_usage_reservations_and_deadline(env):
    binding, clock, ledger = env
    ledger.activate(binding)
    first, pending = ledger.reserve(binding), ledger.reserve(binding)
    ledger.start(binding, first)
    ledger.suspend(binding)
    ledger.suspend(binding)
    expect_code('suspended', ledger.start, binding, pending)
    ledger.finish(binding, first)
    clock.value = 104
    assert ledger.snapshot()['remaining_s'] == 6
    ledger.activate(binding)
    ledger.activate(binding)
    assert ledger.snapshot()['used'] == 1
    assert ledger.snapshot()['remaining_s'] == 6
    ledger.start(binding, pending)
    ledger.finish(binding, pending)
    assert ledger.snapshot()['used'] == 2


@pytest.mark.parametrize('transition,code', [('revoke', 'revoked'), ('expiry', 'expired')])
def test_running_can_finish_after_admission_closes(env, transition, code):
    binding, clock, ledger = env
    ledger.activate(binding)
    running, pending = ledger.reserve(binding), ledger.reserve(binding)
    ledger.start(binding, running)
    if transition == 'revoke':
        ledger.revoke(binding)
        ledger.revoke(binding)
    else:
        clock.value = 110
    expect_code(code, ledger.start, binding, pending)
    expect_code(code, ledger.activate, binding)
    ledger.release(binding, pending)
    ledger.finish(binding, running)
    assert ledger.snapshot()['state'] == code
    assert ledger.snapshot()['used'] == 1
    assert ledger.snapshot()['reserved'] == ledger.snapshot()['running'] == 0


def test_expiry_applies_while_prepared_and_suspended(env):
    binding, clock, ledger = env
    clock.value = 109.9
    ledger.activate(binding)
    ledger.suspend(binding)
    clock.value = 110
    expect_code('expired', ledger.activate, binding)
    prepared = BudgetLedger(binding, BudgetLimits(duration_s=10), approved_at=100, clock=clock)
    expect_code('expired', prepared.activate, binding)


@pytest.mark.parametrize('field,value', [
    ('contract_id', 'd' * 32), ('session_id', 'd' * 32),
    ('contract_revision', 2), ('run_revision', 2), ('rules_digest', 'd' * 64),
])
def test_every_binding_component_is_required(env, field, value):
    binding, _, ledger = env
    ledger.activate(binding)
    token = ledger.reserve(binding)
    before = ledger.snapshot()
    foreign = replace(binding, **{field: value})
    for method, args in [
        (ledger.activate, ()), (ledger.suspend, ()), (ledger.revoke, ()),
        (ledger.reserve, ()), (ledger.start, (token,)),
        (ledger.finish, (token,)), (ledger.release, (token,)),
    ]:
        expect_code('binding_mismatch', method, foreign, *args)
    assert ledger.snapshot() == before


@pytest.mark.parametrize('token', [None, {}, [], 'PRIVATE', 1, True, object()])
def test_invalid_tokens_do_not_change_accounting(env, token):
    binding, _, ledger = env
    ledger.activate(binding)
    before = ledger.snapshot()
    for method in (ledger.start, ledger.finish, ledger.release):
        expect_code('invalid_token', method, binding, token)
    assert ledger.snapshot() == before


def test_tokens_cannot_cross_instances_or_be_reused_after_release(env):
    binding, clock, ledger = env
    other = BudgetLedger(binding, BudgetLimits(), approved_at=100, clock=clock)
    ledger.activate(binding)
    other.activate(binding)
    token = ledger.reserve(binding)
    expect_code('invalid_token', other.start, binding, token)
    ledger.release(binding, token)
    expect_code('invalid_token', ledger.start, binding, token)
    expect_code('invalid_token', ledger.release, binding, token)
    assert other.snapshot()['used'] == 0


@pytest.mark.parametrize('bad', [99, float('nan'), float('inf'), -1, True, 'PRIVATE', None])
def test_bad_clock_latches_closed_but_running_can_drain(env, bad):
    binding, clock, ledger = env
    ledger.activate(binding)
    token = ledger.reserve(binding)
    ledger.start(binding, token)
    clock.value = bad
    assert ledger.snapshot()['state'] == 'recovery_required'
    clock.value = 100
    expect_code('recovery_required', ledger.reserve, binding)
    expect_code('recovery_required', ledger.activate, binding)
    ledger.finish(binding, token)
    assert ledger.snapshot()['running'] == 0
    assert ledger.snapshot()['state'] == 'recovery_required'


def test_clock_exception_is_not_exposed(env):
    binding, _, ledger = env
    ledger.activate(binding)
    def fail():
        raise RuntimeError('PRIVATE-CLOCK-DATA')
    ledger._clock = fail
    assert ledger.snapshot()['state'] == 'recovery_required'
    expect_code('recovery_required', ledger.reserve, binding)
    assert 'PRIVATE' not in repr(ledger.snapshot())


@pytest.mark.parametrize('approved_at', [True, -1, float('nan'), float('inf'), 101, None])
def test_invalid_approval_time_cannot_create_a_ledger(env, approved_at):
    binding, clock, _ = env
    with pytest.raises(BudgetError):
        BudgetLedger(binding, BudgetLimits(), approved_at=approved_at, clock=clock)


def test_approval_time_is_not_reset_when_ledger_is_created_later(env):
    binding, clock, _ = env
    clock.value = 105
    ledger = BudgetLedger(binding, BudgetLimits(duration_s=10), approved_at=100, clock=clock)
    ledger.activate(binding)
    assert ledger.snapshot()['remaining_s'] == 5


def test_clock_cannot_move_backwards_after_forward_progress(env):
    binding, clock, ledger = env
    ledger.activate(binding)
    clock.value = 105
    assert ledger.snapshot()['remaining_s'] == 5
    clock.value = 104
    expect_code('recovery_required', ledger.reserve, binding)
    clock.value = 106
    expect_code('recovery_required', ledger.activate, binding)


def test_invalid_initial_clock_and_models_fail_without_foreign_errors(env):
    binding, clock, _ = env
    with pytest.raises(BudgetError, match='^invalid_model$'):
        BudgetLedger(binding, {}, approved_at=100, clock=clock)
    with pytest.raises(BudgetError, match='^invalid_clock$'):
        BudgetLedger(binding, BudgetLimits(), approved_at=100, clock=None)
    def fail():
        raise RuntimeError('PRIVATE-CLOCK-DATA')
    with pytest.raises(BudgetError, match='^clock_unavailable$'):
        BudgetLedger(binding, BudgetLimits(), approved_at=100, clock=fail)


def test_inconsistent_counter_latches_closed(env):
    binding, _, ledger = env
    ledger.activate(binding)
    ledger._used = -1
    expect_code('recovery_required', ledger.reserve, binding)
    ledger._used = 0
    expect_code('recovery_required', ledger.activate, binding)
    assert ledger.snapshot()['execution_blocked']


@pytest.mark.parametrize('field,value', [('_reserved', None), ('_running', []), ('_state', {})])
@pytest.mark.parametrize('method_name', ['start', 'finish', 'release'])
def test_corrupt_accounting_fails_with_fixed_code(env, field, value, method_name):
    binding, _, ledger = env
    ledger.activate(binding)
    token = ledger.reserve(binding)
    setattr(ledger, field, value)
    expect_code('recovery_required', getattr(ledger, method_name), binding, token)


def test_snapshot_is_detached_and_contains_no_tokens(env):
    binding, _, ledger = env
    ledger.activate(binding)
    ledger.reserve(binding)
    state = ledger.snapshot()
    assert set(state) == {'binding', 'state', 'execution_blocked', 'used', 'reserved',
                          'running', 'remaining_operations', 'remaining_s'}
    state['binding']['contract_id'] = 'changed'
    state['used'] = 999
    assert ledger.snapshot()['binding']['contract_id'] == binding.contract_id
    assert ledger.snapshot()['used'] == 0


def test_concurrent_reservations_cannot_overbook_last_unit(env):
    binding, clock, _ = env
    ledger = BudgetLedger(binding, BudgetLimits(max_operations=1), approved_at=100, clock=clock)
    ledger.activate(binding)
    barrier = threading.Barrier(2)
    def reserve():
        barrier.wait(timeout=5)
        try:
            return ledger.reserve(binding)
        except BudgetError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert sum(type(result) is object for result in results) == 1
    assert results.count('budget_reserved') == 1
    assert ledger.snapshot()['reserved'] == 1


def test_concurrent_starts_do_not_exceed_single_running_slot(env):
    binding, _, ledger = env
    ledger.activate(binding)
    tokens = [ledger.reserve(binding) for _ in range(2)]
    barrier = threading.Barrier(2)
    def start(token):
        barrier.wait(timeout=5)
        try:
            ledger.start(binding, token)
            return 'started'
        except BudgetError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(start, token) for token in tokens]
        results = [future.result(timeout=5) for future in futures]
    assert sorted(results) == ['busy', 'started']
    assert ledger.snapshot()['used'] == ledger.snapshot()['running'] == 1
    for token, result in zip(tokens, results):
        if result == 'started':
            ledger.finish(binding, token)
        else:
            ledger.release(binding, token)


def test_start_revoke_race_has_one_admission_order(env):
    binding, _, ledger = env
    ledger.activate(binding)
    token = ledger.reserve(binding)
    barrier = threading.Barrier(2)
    def start():
        barrier.wait(timeout=5)
        try:
            ledger.start(binding, token)
            return True
        except BudgetError as exc:
            assert str(exc) == 'revoked'
            return False
    def revoke():
        barrier.wait(timeout=5)
        ledger.revoke(binding)
    with ThreadPoolExecutor(max_workers=2) as pool:
        started = pool.submit(start)
        revoked = pool.submit(revoke)
        admitted = started.result(timeout=5)
        revoked.result(timeout=5)
    state = ledger.snapshot()
    assert state['state'] == 'revoked'
    assert state['used'] == state['running'] == int(admitted)
    if admitted:
        ledger.finish(binding, token)
    else:
        ledger.release(binding, token)
    expect_code('revoked', ledger.reserve, binding)
