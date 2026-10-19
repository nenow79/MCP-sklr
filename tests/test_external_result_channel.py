"""M3: external_result channel in the task queue. Fake executors only: no MCP, no network."""
import json
import logging
import threading

import pytest

from server.task_tools import get_task_impl
from tasks.approval import ApprovalService
from tasks.manager import (
    EXTERNAL_ERROR, EXTERNAL_KIND, MAX_SAFE_ERROR_CHARS, SECRET_ERROR, TaskManager, TaskStateError,
)
from tasks.models import TaskStatus
from tasks.store import TaskStore
from tasks.volatile_results import MAX_CONTENT_CHARS, VolatileResult, VolatileResultStore

PRIVATE = 'PRIVATE_RESULT_MARKER'
UNAVAILABLE = {'state': 'unavailable'}
EXTERNAL_FIELDS = dict(kind=EXTERNAL_KIND, title='Zewnetrzne MCP: demo / echo', sensitive=True,
                       offer_trust=False, timeout=10, payload={'alias': 'demo', 'tool': 'echo'})
SECRET_FIELDS = dict(kind='secret_reveal', title='Ujawnienie', sensitive=True,
                     offer_trust=False, timeout=10)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def make(tmp_path):
    created = []

    def factory(answer='allow', clock=None):
        db = tmp_path / f'tasks{len(created)}.db'
        store = TaskStore(str(db))

        def notifier(task):
            if answer is not None:
                approval.resolve(task.id, answer)

        approval = ApprovalService(notifier=notifier)
        volatile = VolatileResultStore(clock=clock) if clock is not None else None
        manager = TaskManager(store, approval, max_parallel=2, max_queued=50, max_wait_s=8,
                              volatile_store=volatile)
        created.append((manager, store))
        return manager, store, db

    yield factory
    for manager, store in created:
        manager.shutdown()
        store.close()


def typed_content(text=PRIVATE):
    return json.dumps({'content': [{'type': 'text', 'text': text}], 'isError': False})


def result_executor(content=None, check=None, calls=None):
    def run(event):
        if calls is not None:
            calls.append(1)
        return VolatileResult({'content': content if content is not None else typed_content(),
                               'tool': 'echo'}, check)
    return run


class Safe(Exception):
    """Mimics the adapter's exception: only `safe_error` may reach the task."""
    def __init__(self, safe=None):
        super().__init__(PRIVATE)
        if safe is not None:
            self.safe_error = safe


def run_to_end(manager, executor, answer_needed=True, verify=None, fields=None):
    task = manager.submit(fields or EXTERNAL_FIELDS, executor, answer_needed, verify=verify)
    return manager.wait(task.id, 8)


# --- the channel ---

def test_approved_call_result_only_in_external_channel(make):
    manager, _, _ = make()
    done = run_to_end(manager, result_executor())
    assert done.status == TaskStatus.DONE and done.decision == 'allow'
    for _ in range(2):  # reading does not consume the result
        got = manager.get_external_result(done.id)
        assert got['state'] == 'available' and PRIVATE in got['data']['content']
    assert manager.get_volatile_result(done.id) == UNAVAILABLE  # secret channel never serves it


def test_authorized_call_runs_without_approval_after_verify(make):
    manager, _, _ = make(answer=None)
    order = []

    def verify():
        order.append('verify')

    def executor(event):
        order.append('run')
        return VolatileResult({'content': PRIVATE})

    done = run_to_end(manager, executor, answer_needed=False, verify=verify)
    assert done.status == TaskStatus.DONE and done.decision == ''
    assert order == ['verify', 'run']
    assert manager.get_external_result(done.id)['state'] == 'available'


def test_rejected_by_user_never_runs(make):
    manager, _, _ = make(answer='deny')
    calls = []
    done = run_to_end(manager, result_executor(calls=calls))
    assert done.status == TaskStatus.REJECTED and calls == []
    assert manager.get_external_result(done.id) == UNAVAILABLE


# --- fail closed ---

def test_failed_verify_blocks_execution_with_safe_message(make):
    manager, _, _ = make(answer=None)
    calls = []
    message = 'Zewnetrzne MCP: zaufanie cofniete.'
    done = run_to_end(manager, result_executor(calls=calls), False, verify=lambda: message)
    assert done.status == TaskStatus.FAILED and done.error == message and calls == []
    assert manager.get_external_result(done.id) == UNAVAILABLE


@pytest.mark.parametrize('message', ['x' * (MAX_SAFE_ERROR_CHARS + 1), 'line\nbreak'])
def test_unsafe_verify_message_is_replaced(make, message):
    manager, _, _ = make(answer=None)
    done = run_to_end(manager, result_executor(), False, verify=lambda: message)
    assert done.status == TaskStatus.FAILED and done.error == EXTERNAL_ERROR


def test_executor_error_exposes_only_safe_error_attribute(make, caplog):
    caplog.set_level(logging.DEBUG)
    manager, _, _ = make()

    def safe(event):
        raise Safe('Zewnetrzne MCP: timeout.')

    def unsafe(event):
        raise Safe()

    first = run_to_end(manager, safe)
    second = run_to_end(manager, unsafe)
    assert first.status == TaskStatus.FAILED and first.error == 'Zewnetrzne MCP: timeout.'
    assert second.status == TaskStatus.FAILED and second.error == EXTERNAL_ERROR
    assert PRIVATE not in caplog.text
    assert PRIVATE not in json.dumps([first.to_dict(), second.to_dict()])
    assert manager.get_external_result(first.id) == UNAVAILABLE


def test_safe_error_attribute_is_ignored_for_secret_reveal(make):
    manager, _, _ = make()

    def boom(event):
        raise Safe('Zewnetrzne MCP: should not be shown')

    done = run_to_end(manager, boom, fields=SECRET_FIELDS)
    assert done.status == TaskStatus.FAILED and done.error == SECRET_ERROR


def test_plain_dict_result_from_external_task_is_not_stored(make):
    manager, _, _ = make()
    done = run_to_end(manager, lambda event: {'exit_code': 0, 'stdout': PRIVATE, 'started': True})
    assert done.status == TaskStatus.FAILED and done.error == EXTERNAL_ERROR
    assert PRIVATE not in json.dumps(done.to_dict())
    assert manager.get_external_result(done.id) == UNAVAILABLE


def test_result_over_limit_is_rejected_not_truncated(make):
    manager, _, _ = make()
    over = run_to_end(manager, result_executor('x' * (MAX_CONTENT_CHARS + 1)))
    assert over.status == TaskStatus.FAILED and over.error == EXTERNAL_ERROR
    assert manager.get_external_result(over.id) == UNAVAILABLE
    exact = run_to_end(manager, result_executor('x' * MAX_CONTENT_CHARS))
    assert exact.status == TaskStatus.DONE
    assert len(manager.get_external_result(exact.id)['data']['content']) == MAX_CONTENT_CHARS


def test_cancel_during_run_discards_result(make):
    manager, _, _ = make()
    started = threading.Event()

    def run(event):
        started.set()
        event.wait(5)
        return VolatileResult({'content': PRIVATE})

    task = manager.submit(EXTERNAL_FIELDS, run, True)
    assert started.wait(5)
    assert manager.cancel(task.id).status == TaskStatus.CANCELLED
    assert manager.get_external_result(task.id) == UNAVAILABLE


# --- access, TTL, restart, shutdown ---

def test_access_check_denial_removes_result(make):
    manager, _, _ = make()
    state = {'deny': None}
    done = run_to_end(manager, result_executor(check=lambda: state['deny']))
    assert manager.get_external_result(done.id)['state'] == 'available'
    state['deny'] = 'profile_disabled'
    assert manager.get_external_result(done.id) == {'state': 'denied'}
    state['deny'] = None
    assert manager.get_external_result(done.id) == UNAVAILABLE


def test_result_expires_after_five_minutes(make):
    clock = Clock()
    manager, _, _ = make(clock=clock)
    done = run_to_end(manager, result_executor())
    clock.now += 299
    assert manager.get_external_result(done.id)['state'] == 'available'
    clock.now += 2
    assert manager.get_external_result(done.id) == UNAVAILABLE


def test_after_restart_task_is_done_but_result_is_gone(make):
    manager, store, db = make()
    done = run_to_end(manager, result_executor())
    manager.shutdown()
    store.close()
    store2 = TaskStore(str(db))
    manager2 = TaskManager(store2, ApprovalService(notifier=lambda task: None))
    try:
        assert manager2.get(done.id).status == TaskStatus.DONE
        assert manager2.get_external_result(done.id) == UNAVAILABLE
    finally:
        manager2.shutdown()
        store2.close()


def test_result_unavailable_after_shutdown(make):
    manager, _, _ = make()
    done = run_to_end(manager, result_executor())
    manager.shutdown()
    assert manager.get_external_result(done.id) == UNAVAILABLE


def test_nothing_private_in_history_logs_or_database_files(make, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    manager, store, db = make()
    done = run_to_end(manager, result_executor())
    assert PRIVATE not in json.dumps(manager.get(done.id).to_dict(), ensure_ascii=False)
    assert PRIVATE not in caplog.text
    manager.shutdown()
    store.close()
    blob = b''.join(path.read_bytes() for path in tmp_path.glob(db.name + '*'))
    assert PRIVATE.encode() not in blob


# --- submit contract and isolation from secret_reveal ---

@pytest.mark.parametrize('patch', [{'sensitive': False}, {'offer_trust': True}])
def test_external_requires_sensitive_and_no_trust_offer(make, patch):
    manager, _, _ = make()
    with pytest.raises(TaskStateError):
        manager.submit({**EXTERNAL_FIELDS, **patch}, result_executor(), True)


def test_external_rejects_on_trust_callback(make):
    manager, _, _ = make()
    with pytest.raises(TaskStateError):
        manager.submit(EXTERNAL_FIELDS, result_executor(), True, on_trust=lambda: None)


def test_secret_reveal_still_requires_approval(make):
    manager, _, _ = make()
    with pytest.raises(TaskStateError):
        manager.submit(SECRET_FIELDS, result_executor(), False)


def test_channels_are_isolated_between_kinds(make):
    manager, _, _ = make()
    secret = run_to_end(manager, result_executor(), fields=SECRET_FIELDS)
    external = run_to_end(manager, result_executor())
    assert manager.get_volatile_result(secret.id)['state'] == 'available'
    assert manager.get_external_result(secret.id) == UNAVAILABLE
    assert manager.get_external_result(external.id)['state'] == 'available'
    assert manager.get_volatile_result(external.id) == UNAVAILABLE


def test_rate_fallback_same_task_requires_user_before_dispatch(make):
    from tasks.manager import ExternalApprovalRequired
    manager, store, _ = make(answer=None)
    requested, attempts, dispatched = threading.Event(), [], []
    manager._approval.set_notifier(lambda task: requested.set())
    def executor(event):
        attempts.append(True)
        if len(attempts) == 1:
            raise ExternalApprovalRequired()
        dispatched.append(True)
        return VolatileResult({'content': PRIVATE})
    task = manager.submit(EXTERNAL_FIELDS, executor, False, verify=lambda: None,
                          allow_external_reapproval=True)
    assert requested.wait(5)
    waiting = manager.get(task.id)
    assert waiting.status == TaskStatus.AWAITING_APPROVAL
    assert waiting.started_at is None and not waiting.offer_trust
    assert dispatched == [] and len(store.list()) == 1
    manager.resolve_approval(task.id, 'allow')
    assert manager.wait(task.id, 8).status == TaskStatus.DONE
    assert attempts == [True, True] and dispatched == [True]


@pytest.mark.parametrize('answer', ['deny', 'cancel'])
def test_rate_fallback_denial_or_cancel_never_dispatches(make, answer):
    from tasks.manager import ExternalApprovalRequired
    manager, _, _ = make(answer=None)
    requested, attempts = threading.Event(), []
    manager._approval.set_notifier(lambda task: requested.set())
    def executor(event):
        attempts.append(True)
        raise ExternalApprovalRequired()
    task = manager.submit(EXTERNAL_FIELDS, executor, False, allow_external_reapproval=True)
    assert requested.wait(5)
    result = manager.cancel(task.id) if answer == 'cancel' else manager.resolve_approval(task.id, 'deny')
    assert result.status == (TaskStatus.CANCELLED if answer == 'cancel' else TaskStatus.REJECTED)
    assert attempts == [True] and manager.get_external_result(task.id) == UNAVAILABLE


def test_reapproval_signal_without_optin_fails_closed(make):
    from tasks.manager import ExternalApprovalRequired
    manager, _, _ = make(answer=None)
    def executor(event):
        raise ExternalApprovalRequired()
    assert run_to_end(manager, executor, False).status == TaskStatus.FAILED


def test_reapproval_cannot_loop_after_user_approval(make):
    from tasks.manager import ExternalApprovalRequired
    manager, _, _ = make(answer='allow')
    attempts = []
    def executor(event):
        attempts.append(True)
        raise ExternalApprovalRequired()
    task = manager.submit(EXTERNAL_FIELDS, executor, False, allow_external_reapproval=True)
    assert manager.wait(task.id, 8).status == TaskStatus.FAILED
    assert attempts == [True, True]


@pytest.mark.parametrize('fields, needs_approval', [(SECRET_FIELDS, True), (EXTERNAL_FIELDS, True)])
def test_reapproval_optin_rejects_secret_or_approval_submission(make, fields, needs_approval):
    manager, _, _ = make(answer=None)
    with pytest.raises(TaskStateError):
        manager.submit(fields, result_executor(), needs_approval, allow_external_reapproval=True)


def test_external_consent_query_matches_actual_user_decision(make):
    manager, _, _ = make(answer='allow')
    observed = []
    def executor(event):
        observed.append(manager.external_approval_granted(event))
        assert not manager.external_approval_granted(threading.Event())
        return VolatileResult({'content': PRIVATE})
    assert run_to_end(manager, executor, True).status == TaskStatus.DONE
    assert run_to_end(manager, executor, False).status == TaskStatus.DONE
    assert observed == [True, False]


def test_approval_preview_stays_in_ram_and_disappears_on_decision(make):
    manager, _, _ = make(answer=None)
    preview = {'text': PRIVATE, 'truncated': False, 'warning': 'heuristic'}
    task = manager.submit(EXTERNAL_FIELDS, result_executor(), True, approval_preview=preview)
    preview['text'] = 'changed by caller'
    assert manager.get_approval_preview(task.id)['text'] == PRIVATE
    detached = manager.get_approval_preview(task.id)
    detached['text'] = 'changed by reader'
    assert manager.get_approval_preview(task.id)['text'] == PRIVATE
    assert PRIVATE not in json.dumps(manager.get(task.id).to_dict())
    assert PRIVATE not in json.dumps(get_task_impl(manager, task.id))
    assert PRIVATE not in json.dumps([item.to_summary() for item in manager.list()])
    manager.resolve_approval(task.id, 'deny')
    assert manager.get_approval_preview(task.id) is None


def test_preview_rejected_for_secret_tasks(make):
    manager, _, _ = make(answer=None)
    with pytest.raises(TaskStateError):
        manager.submit(SECRET_FIELDS, result_executor(), True,
                       approval_preview={'text': PRIVATE, 'truncated': False, 'warning': ''})


def test_get_task_adds_the_matching_result_field_only(make):
    manager, _, _ = make()
    secret = run_to_end(manager, result_executor(), fields=SECRET_FIELDS)
    external = run_to_end(manager, result_executor())
    other = run_to_end(manager, lambda event: {'exit_code': 0, 'stdout': 'ok', 'started': True},
                       False, fields=dict(kind='file_edit', title='x'))
    ext = get_task_impl(manager, external.id)['data']
    assert ext['external_result']['state'] == 'available' and 'secret_result' not in ext
    sec = get_task_impl(manager, secret.id)['data']
    assert sec['secret_result']['state'] == 'available' and 'external_result' not in sec
    oth = get_task_impl(manager, other.id)['data']
    assert 'secret_result' not in oth and 'external_result' not in oth
