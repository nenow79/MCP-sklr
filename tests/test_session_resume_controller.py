"""Detached P3b1 handshake tests. No panel/model endpoints or TaskManager wiring."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from server.session_resume import ResumeRejected
from tasks.resume_store import ResumeStore
from tasks.session_controller import ResumeOperationError, SessionBlocked, SessionController
from tasks.session_models import SessionConflict
from tasks.session_store import SessionStore


@pytest.fixture
def env(tmp_path):
    path = tmp_path / 'sessions.db'
    store = SessionStore(path)
    resumes = ResumeStore()
    controller = SessionController(store, resumes=resumes)
    item = store.create(['sklr'])
    controller.bind(item.id, item.revision)
    yield SimpleNamespace(store=store, resumes=resumes,
                          controller=controller, sid=item.id, path=path)
    store.close()


def paused(env):
    return env.controller.request_pause(env.controller.snapshot()['revision'])


def prepared(env):
    state = paused(env)
    env.controller.prepare_resume_internal('supplement-marker', state['revision'])
    return env.controller.get_resume_context()


def test_prepare_stays_paused_and_gate_closed_until_confirmation(env):
    context = prepared(env)
    state = env.controller.snapshot()
    assert state['state'] == 'paused' and state['execution_blocked'] and state['resume_pending']
    with pytest.raises(SessionBlocked):
        env.controller.begin_action()
    env.controller.bind(env.sid, state['revision'])  # cannot bypass handshake
    assert env.controller.snapshot()['execution_blocked']
    assert context['available'] and context['supplement'] == 'supplement-marker'
    assert not {'checkpoint', 'checkpoint_missing'} & context.keys()
    result = env.controller.confirm_resume_plan('plan-marker', context['revision'],
                                                context['preparation_token'])
    assert result['confirmed'] and result['revision'] == context['revision'] + 1
    assert 'plan-marker' not in repr(result)
    state = env.controller.snapshot()
    assert state['state'] == 'active' and not state['execution_blocked'] and not state['resume_pending']
    assert env.controller.get_resume_context() == {'available': False}
    token = env.controller.begin_action()
    env.controller.finish_action(token)


def test_model_confirmation_cannot_initiate_resume(env):
    with pytest.raises(SessionConflict):
        env.controller.confirm_resume_plan('Plan', 1, 'forged')
    assert env.controller.get_resume_context() == {'available': False}


def test_prepare_requires_fully_paused_session(env):
    with pytest.raises(SessionConflict):
        env.controller.prepare_resume_internal('', 1)
    token = env.controller.begin_action()
    requested = env.controller.request_pause(1)
    with pytest.raises(SessionConflict):
        env.controller.prepare_resume_internal('', requested['revision'])
    env.controller.finish_action(token)


def test_resume_requires_no_checkpoint(env):
    state = paused(env)
    env.controller.prepare_resume_internal('', state['revision'])
    context = env.controller.get_resume_context()
    assert context['available'] and context['supplement'] == ''
    assert not {'checkpoint', 'checkpoint_missing'} & context.keys()
    assert env.controller.snapshot()['execution_blocked']


def test_invalid_or_secret_text_does_not_prepare(env):
    state = paused(env)
    with pytest.raises(ResumeRejected, match='^secret_detected$'):
        env.controller.prepare_resume_internal('API_TOKEN=private', state['revision'])
    assert not env.controller.snapshot()['resume_pending']
    assert env.store.get(env.sid).revision == state['revision']


@pytest.mark.parametrize('revision_delta,token', [(1, None), (0, 'wrong-token')])
def test_old_revision_or_wrong_token_does_not_open_gate(env, revision_delta, token):
    context = prepared(env)
    with pytest.raises(SessionConflict):
        env.controller.confirm_resume_plan('Plan', context['revision'] + revision_delta,
                                            token or context['preparation_token'])
    assert env.controller.snapshot()['execution_blocked']
    assert env.controller.get_resume_context()['available']


def test_rejected_plan_keeps_gate_closed(env):
    context = prepared(env)
    with pytest.raises(ResumeRejected, match='^secret_detected$'):
        env.controller.confirm_resume_plan('API_TOKEN=private', context['revision'],
                                            context['preparation_token'])
    assert env.controller.snapshot()['execution_blocked']


def test_pause_invalidates_handshake_even_when_pause_request_has_stale_revision(env):
    context = prepared(env)
    # Durable PAUSED is idempotent, but RAM handshake must still be invalidated.
    env.controller.request_pause(context['revision'])
    assert env.controller.get_resume_context() == {'available': False}
    with pytest.raises(SessionConflict):
        env.controller.confirm_resume_plan('Plan', context['revision'], context['preparation_token'])
    assert env.controller.snapshot()['execution_blocked']


def test_restart_does_not_restore_pending_context(env):
    context = prepared(env)
    env.store.recover_after_restart()
    restarted = SessionController(env.store, resumes=ResumeStore())
    item = env.store.get(env.sid)
    restarted.bind(item.id, item.revision)
    assert restarted.get_resume_context() == {'available': False}
    assert restarted.snapshot()['execution_blocked']
    with pytest.raises(SessionConflict):
        env.controller.confirm_resume_plan('Plan', context['revision'], context['preparation_token'])


@pytest.mark.parametrize('target', ['store', 'ram'])
def test_prepare_failure_never_opens_gate(env, monkeypatch, target):
    state = paused(env)
    def fail(*args):
        raise RuntimeError('private-error-path')
    if target == 'store':
        monkeypatch.setattr(env.store, 'prepare_resume', fail)
    else:
        monkeypatch.setattr(env.resumes, 'prepare', fail)
    with pytest.raises(ResumeOperationError, match='^resume_prepare_failed$'):
        env.controller.prepare_resume_internal('Supplement', state['revision'])
    assert env.controller.snapshot()['execution_blocked']
    assert env.controller.get_resume_context() == {'available': False}


def test_ram_confirmation_failure_keeps_gate_closed(env, monkeypatch):
    context = prepared(env)
    def fail(*args):
        raise RuntimeError('private-error-path')
    monkeypatch.setattr(env.resumes, 'confirm', fail)
    with pytest.raises(ResumeOperationError, match='^resume_ram_failed$'):
        env.controller.confirm_resume_plan('Plan', context['revision'], context['preparation_token'])
    assert env.controller.snapshot()['execution_blocked']


@pytest.mark.parametrize('commit_first', [False, True])
def test_durable_confirmation_failure_never_opens_gate(env, monkeypatch, commit_first):
    context = prepared(env)
    original = env.store.confirm_resume
    def fail(*args):
        if commit_first:
            original(*args)
        raise RuntimeError('private-commit-details')
    monkeypatch.setattr(env.store, 'confirm_resume', fail)
    with pytest.raises(ResumeOperationError, match='^resume_confirm_failed$'):
        env.controller.confirm_resume_plan('Plan', context['revision'], context['preparation_token'])
    assert env.controller.snapshot()['execution_blocked']
    assert env.controller.get_resume_context() == {'available': False}
    with pytest.raises(SessionBlocked):
        env.controller.begin_action()


def test_concurrent_confirmation_opens_once(env):
    context = prepared(env)
    def confirm(n):
        try:
            env.controller.confirm_resume_plan('Plan %d' % n, context['revision'],
                                                context['preparation_token'])
            return True
        except SessionConflict:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(confirm, range(8)))
    assert sum(results) == 1 and not env.controller.snapshot()['execution_blocked']


def test_sqlite_and_wal_never_contain_supplement_plan_or_token(env):
    context = prepared(env)
    env.controller.confirm_resume_plan('plan-marker', context['revision'], context['preparation_token'])
    markers = ['supplement-marker', 'plan-marker', context['preparation_token']]
    dump = '\n'.join(env.store._conn.iterdump())
    for marker in markers:
        assert marker not in dump
        for path in env.path.parent.glob('sessions.db*'):
            assert marker.encode() not in path.read_bytes()
