"""P3a checkpoint adapters: real controller/RAM and telemetry wrapper, no main/server."""
import inspect
import queue
from types import SimpleNamespace

import pytest

from server.session_bootstrap import build_work_session
from server.session_checkpoint import CheckpointRejected
from server.session_registration import (
    make_get_session_state, make_save_session_checkpoint, make_session_registrars)
from server.session_tools import session_control
from tasks.session_controller import CheckpointOperationError
from tasks.session_models import SessionConflict

MARKER = 'checkpoint-private-marker'
ROOTS = SimpleNamespace(list_status=lambda: [{'alias': 'sklr', 'valid': True}])


@pytest.fixture
def runtime(tmp_path):
    built = build_work_session(tmp_path / 'work_sessions.db', ROOTS, enabled=True)
    yield built
    built.close()


def pause(runtime):
    active = runtime.admin.start(['sklr'])
    return runtime.admin.pause(active['session_id'], active['revision'])


def payload():
    return {'goal': MARKER, 'next_step': 'Wait for user'}


def test_signature_has_revision_but_no_model_session_id(runtime):
    tool = make_save_session_checkpoint(runtime.controller)
    assert tool.__name__ == 'save_session_checkpoint'
    assert list(inspect.signature(tool).parameters) == ['payload', 'expected_revision']
    assert list(inspect.signature(make_get_session_state(runtime.controller)).parameters) == []


def test_save_confirmation_and_state_never_contain_checkpoint_text(runtime):
    state = pause(runtime)
    get = make_get_session_state(runtime.controller)
    before = get()['session_control']
    assert before['checkpoint_available'] and not before['checkpoint_content_available']
    assert 'save_session_checkpoint' in before['instruction']
    save = make_save_session_checkpoint(runtime.controller)
    result = save(payload(), state['revision'])
    assert result['success'] and result['data']['saved']
    assert result['data']['revision'] == state['revision'] + 1
    assert MARKER not in repr(result)
    after = get()
    assert after['session_control']['checkpoint_content_available']
    assert 'save_session_checkpoint' not in after['session_control']['instruction']
    assert 'RAM' in after['session_control']['instruction']
    assert MARKER not in repr(after)
    assert runtime.controller.get_checkpoint()['checkpoint']['goal'] == MARKER
    assert runtime.controller.snapshot()['running_actions'] == 0


def test_no_selection_active_and_stale_revision_return_fixed_conflict(runtime):
    save = make_save_session_checkpoint(runtime.controller)
    assert save(payload(), 1)['error'] == 'checkpoint_conflict'
    active = runtime.admin.start(['sklr'])
    assert save(payload(), active['revision'])['error'] == 'checkpoint_conflict'
    paused = runtime.admin.pause(active['session_id'], active['revision'])
    assert save(payload(), paused['revision'] - 1)['error'] == 'checkpoint_conflict'
    assert save(payload(), True)['error'] == 'invalid_revision'
    assert runtime.controller.get_checkpoint() == {'available': False}


def test_rejected_secret_returns_no_echo_or_success(runtime):
    state = pause(runtime)
    result = make_save_session_checkpoint(runtime.controller)(
        {'goal': 'API_TOKEN=private-token-123', 'next_step': 'Wait'}, state['revision'])
    assert result['success'] is False and result['error'] == 'secret_detected'
    assert 'private-token-123' not in repr(result)
    assert not result['session_control']['checkpoint_content_available']
    assert 'save_session_checkpoint' in result['session_control']['instruction']


@pytest.mark.parametrize('error,expected', [
    (CheckpointRejected('foreign_task'), 'foreign_task'),
    (CheckpointRejected('private-exception-text'), 'checkpoint_rejected'),
    (CheckpointOperationError('checkpoint_ram_failed'), 'checkpoint_ram_failed'),
    (CheckpointOperationError('private-exception-text'), 'checkpoint_failed'),
    (SessionConflict('private-exception-text'), 'checkpoint_conflict'),
    (ValueError('private-exception-text'), 'invalid_revision'),
    (RuntimeError('private-exception-text'), 'checkpoint_failed'),
])
def test_adapter_error_allowlist_does_not_echo_exceptions(runtime, monkeypatch, error, expected):
    state = pause(runtime)
    def fail(*args):
        raise error
    monkeypatch.setattr(runtime.controller, 'save_checkpoint', fail)
    result = make_save_session_checkpoint(runtime.controller)(payload(), state['revision'])
    assert result['success'] is False and result['error'] == expected
    assert MARKER not in repr(result) and 'private-exception-text' not in repr(result)


def test_snapshot_failure_returns_stop_without_checkpoint_instructions(runtime, monkeypatch):
    def fail():
        raise RuntimeError('private-storage-path')
    monkeypatch.setattr(runtime.controller, 'snapshot', fail)
    result = make_get_session_state(runtime.controller)()
    control = result['session_control']
    assert control['code'] == 'session_state_unavailable'
    assert not control['checkpoint_available']
    assert 'save_session_checkpoint' not in control['instruction']
    assert 'private-storage-path' not in repr(result)


def test_missing_selection_does_not_instruct_checkpoint_save(runtime):
    control = session_control(runtime.controller)
    assert control['checkpoint_available']
    assert control['code'] == 'session_not_selected'
    assert 'save_session_checkpoint' not in control['instruction']


def test_regular_decorator_is_forced_to_confidential_for_checkpoint(runtime):
    ordinary, confidential = [], []
    def regular(fn):
        ordinary.append(fn)
        return fn
    def secret(fn):
        confidential.append(fn)
        return fn
    register, _ = make_session_registrars(regular, secret, runtime.controller)
    tool = register(make_save_session_checkpoint(runtime.controller))
    assert ordinary == [] and confidential == [tool]
    assert list(inspect.signature(tool).parameters) == ['payload', 'expected_revision']


def test_confidential_telemetry_does_not_serialize_input_or_output(runtime, monkeypatch):
    import telemetry
    records = []
    def forbidden(*args, **kwargs):
        raise AssertionError('Confidential serialization forbidden')
    monkeypatch.setattr(telemetry, '_json_size_bytes', forbidden)
    monkeypatch.setattr(telemetry, '_summarize_error', forbidden)
    class CaptureQueue(queue.Queue):
        def put_nowait(self, record):
            records.append(record)  # do not enqueue private test records to persistent worker
    monkeypatch.setattr(telemetry, '_RECORD_QUEUE', CaptureQueue())
    def regular(fn):
        return telemetry.track_mcp_tool(fn, lambda: 'sklr')
    def confidential(fn):
        return telemetry.track_mcp_tool(fn, lambda: 'sklr', confidential=True)
    register, _ = make_session_registrars(regular, confidential, runtime.controller)
    save = register(make_save_session_checkpoint(runtime.controller))
    state = pause(runtime)
    result = save(payload(), state['revision'])
    assert result['success']
    rejected = save({'goal': 'API_TOKEN=private-token-123', 'next_step': 'Wait'},
                    result['data']['revision'])
    assert not rejected['success']
    assert len(records) == 2
    for record in records:
        assert record['input_bytes'] == record['output_bytes'] == ''
    assert MARKER not in repr(records) and 'private-token-123' not in repr(records)


def test_close_releases_ram(tmp_path):
    runtime = build_work_session(tmp_path / 'close-test.db', ROOTS, enabled=True)
    try:
        state = pause(runtime)
        make_save_session_checkpoint(runtime.controller)(payload(), state['revision'])
        ram = runtime.controller._checkpoints
        assert len(ram) == 1
    finally:
        runtime.close()
    assert len(ram) == 0
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        runtime.store.get(state['session_id'])
