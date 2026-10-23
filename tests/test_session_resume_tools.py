"""Detached resume tool factories: no FastMCP server or panel preparation API."""
import inspect
import queue
from types import SimpleNamespace

import pytest

from server.session_bootstrap import build_work_session
from server.session_registration import (
    make_get_resume_context, make_confirm_resume_plan, make_session_registrars)
from server.session_resume import ResumeRejected
from server.session_tools import session_control
from tasks.session_controller import ResumeOperationError
from tasks.session_models import SessionConflict

ROOTS = SimpleNamespace(list_status=lambda: [{'alias': 'sklr', 'valid': True}])


@pytest.fixture
def runtime(tmp_path):
    item = build_work_session(tmp_path / 'sessions.db', ROOTS, enabled=True)
    yield item
    item.close()


def prepare(runtime):
    active = runtime.admin.start(['sklr'])
    paused = runtime.admin.pause(active['session_id'], active['revision'])
    runtime.controller.prepare_resume_internal('private-supplement-marker',
                                               runtime.controller.snapshot()['revision'])
    return runtime.controller.get_resume_context()


def test_signatures_and_no_model_session_id(runtime):
    assert not inspect.signature(make_get_resume_context(runtime.controller)).parameters
    assert list(inspect.signature(make_confirm_resume_plan(runtime.controller)).parameters) == [
        'plan', 'expected_revision', 'preparation_token']


def test_no_preparation_cannot_be_started_by_model(runtime):
    read = make_get_resume_context(runtime.controller)()
    assert read['success'] and read['data'] == {'available': False}
    result = make_confirm_resume_plan(runtime.controller)('Plan', 1, 'forged')
    assert not result['success'] and result['error'] == 'resume_conflict'
    assert runtime.controller.snapshot()['execution_blocked']


def test_context_confirmation_and_pending_instruction(runtime):
    context = prepare(runtime)
    control = session_control(runtime.controller)
    assert control['code'] == 'session_resume_pending' and control['reason'] == 'resume_plan_required'
    assert 'get_resume_context' in control['instruction'] and 'confirm_resume_plan' in control['instruction']
    assert 'save_session_checkpoint' not in control['instruction']
    read = make_get_resume_context(runtime.controller)()
    assert not {'checkpoint', 'checkpoint_missing'} & read['data'].keys()
    assert read['data']['supplement'] == 'private-supplement-marker'
    result = make_confirm_resume_plan(runtime.controller)(
        'private-plan-marker', context['revision'], context['preparation_token'])
    assert result['success'] and result['data']['confirmed']
    assert 'private-plan-marker' not in repr(result)
    assert result['session_control']['code'] == 'session_active'
    assert make_get_resume_context(runtime.controller)()['data'] == {'available': False}


def test_wrong_token_and_secret_plan_keep_gate_closed(runtime):
    context = prepare(runtime)
    tool = make_confirm_resume_plan(runtime.controller)
    assert tool('Plan', context['revision'], 'wrong')['error'] == 'resume_conflict'
    rejected = tool('API_TOKEN=private', context['revision'], context['preparation_token'])
    assert rejected['error'] == 'secret_detected' and 'API_TOKEN' not in repr(rejected)
    assert runtime.controller.snapshot()['execution_blocked']


@pytest.mark.parametrize('error,expected', [
    (ResumeRejected('secret_detected'), 'secret_detected'),
    (ResumeRejected('PRIVATE-ERROR'), 'resume_rejected'),
    (ResumeOperationError('resume_ram_failed'), 'resume_ram_failed'),
    (ResumeOperationError('PRIVATE-ERROR'), 'resume_failed'),
    (SessionConflict('PRIVATE-ERROR'), 'resume_conflict'),
    (ValueError('PRIVATE-ERROR'), 'invalid_resume_arguments'),
    (RuntimeError('PRIVATE-ERROR'), 'resume_failed'),
])
def test_fixed_error_allowlist(runtime, monkeypatch, error, expected):
    def fail(*args):
        raise error
    monkeypatch.setattr(runtime.controller, 'get_resume_context', fail)
    monkeypatch.setattr(runtime.controller, 'confirm_resume_plan', fail)
    for result in (make_get_resume_context(runtime.controller)(),
                   make_confirm_resume_plan(runtime.controller)('Plan', 1, 'token')):
        assert not result['success'] and result['error'] == expected
        assert 'PRIVATE-ERROR' not in repr(result)


def test_both_tools_forced_to_confidential_telemetry(runtime, monkeypatch):
    import telemetry
    records = []
    def forbidden(*args, **kwargs):
        raise AssertionError('Confidential serialization forbidden')
    monkeypatch.setattr(telemetry, '_json_size_bytes', forbidden)
    monkeypatch.setattr(telemetry, '_summarize_error', forbidden)
    class CaptureQueue(queue.Queue):
        def put_nowait(self, record):
            records.append(record)  # never enqueue private records for persistent logging
    monkeypatch.setattr(telemetry, '_RECORD_QUEUE', CaptureQueue())
    def ordinary(fn):
        pytest.fail('Resume factories must use confidential registrar')
    def confidential(fn):
        return telemetry.track_mcp_tool(fn, lambda: 'sklr', confidential=True)
    register, _ = make_session_registrars(ordinary, confidential, runtime.controller)
    read = register(make_get_resume_context(runtime.controller))
    confirm = register(make_confirm_resume_plan(runtime.controller))
    prepare(runtime)
    context = read()['data']
    result = confirm('private-plan-marker', context['revision'], context['preparation_token'])
    assert result['success'] and len(records) == 2
    assert all(record['input_bytes'] == record['output_bytes'] == '' for record in records)
    for marker in ('private-plan-marker',
                   'private-supplement-marker', context['preparation_token']):
        assert marker not in repr(records)


def test_close_clears_resume_ram(tmp_path):
    runtime = build_work_session(tmp_path / 'close.db', ROOTS, enabled=True)
    try:
        prepare(runtime)
        resumes = runtime.controller._resumes
        assert resumes.get() is not None
    finally:
        runtime.close()
    assert resumes.get() is None
