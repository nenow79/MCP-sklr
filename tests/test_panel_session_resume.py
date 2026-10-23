"""Detached panel resume API: real stores, TestClient, fake executors."""
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from server.session_admin import SessionAdmin
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.models import TaskStatus
from tasks.resume_store import ResumeStore
from tasks.session_controller import ResumeOperationError, SessionBlocked, SessionController
from tasks.session_models import SessionConflict
from tasks.session_store import SessionStore
from tasks.store import TaskStore

ORIGIN = 'http://127.0.0.1:8102'
PATH = '/api/work-session'


@pytest.fixture
def env(tmp_path):
    sessions = SessionStore(tmp_path / 'sessions.db')
    tasks = TaskStore(str(tmp_path / 'tasks.db'))
    controller = SessionController(sessions, resumes=ResumeStore())
    entries = [{'alias': 'sklr', 'valid': True}]
    roots = SimpleNamespace(list_status=lambda: entries)
    admin = SessionAdmin(sessions, controller, roots)
    approval = ApprovalService()
    manager = TaskManager(tasks, approval, max_queued=100, session_controller=controller)
    auth = PanelAuth(port=8102)
    cookie = auth.exchange_token(auth.token)
    headers = {'Origin': ORIGIN, 'Cookie': f'{COOKIE_NAME}={cookie}'}
    app = create_panel_app(manager, approval, auth, static_dir=tmp_path / 'absent',
                           work_sessions=admin)
    with TestClient(app, base_url=ORIGIN) as client:
        yield SimpleNamespace(client=client, headers=headers, sessions=sessions,
            tasks=tasks, controller=controller, admin=admin, manager=manager,
            entries=entries, approval=approval, auth=auth, tmp_path=tmp_path)
    manager.shutdown()
    for worker in manager._workers:
        worker.join(timeout=6)
    assert not any(worker.is_alive() for worker in manager._workers)
    tasks.close()
    sessions.close()


def get(env, suffix, **kwargs):
    return env.client.get(PATH + suffix, headers=env.headers, **kwargs)


def post(env, suffix, body):
    return env.client.post(PATH + suffix, headers=env.headers, json=body)


def paused(env, count=0):
    response = post(env, '/start', {'roots': ['sklr']})
    assert response.status_code == 201
    active = response.json()['data']
    rows = [env.manager.submit({}, lambda event: pytest.fail('old executor ran'), True)
            for _ in range(count)]
    response = post(env, '/pause', {'session_id': active['session_id'], 'revision': active['revision']})
    assert response.status_code == 200
    state = response.json()['data']
    return env.admin.status(), rows


def preview(env):
    state = env.admin.status()
    return get(env, '/resume-preview', params={
        'session_id': state['session_id'], 'revision': state['revision']})


def body_from(view):
    return {'session_id': view['session_id'], 'revision': view['revision'],
            'supplement': 'supplement-marker', 'cancel_count': view['cancel_count'],
            'confirmed': True}


def confirm(env):
    context = env.controller.get_resume_context()
    result = env.controller.confirm_resume_plan('plan-marker', context['revision'],
                                                context['preparation_token'])
    return context, result


def test_complete_api_flow_counts_all_links_and_plan_has_no_token(env):
    state, rows = paused(env, count=25)
    env.manager.resolve_approval(rows[0].id, 'allow')
    before = env.admin.status()
    response = preview(env)
    assert response.status_code == 200
    view = response.json()['data']
    assert view['cancel_count'] == 25 and 'checkpoint_missing' not in view
    assert env.admin.status() == before
    assert all(env.manager.get(row.id).status != TaskStatus.CANCELLED for row in rows)
    assert get(env, '/resume-plan').json()['data'] == {'available': False}
    response = post(env, '/prepare-resume', body_from(view))
    assert response.status_code == 200 and response.json()['data']['cancelled_count'] == 25
    assert env.admin.status()['resume_pending'] and env.admin.status()['execution_blocked']
    assert 'supplement-marker' not in response.text
    assert all(env.manager.get(row.id).status == TaskStatus.CANCELLED for row in rows)
    assert get(env, '/resume-plan').json()['data'] == {'available': False}
    context, result = confirm(env)
    response = get(env, '/resume-plan')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['data'] == {'available': True, 'session_id': state['session_id'],
                                      'revision': result['revision'], 'plan': 'plan-marker'}
    assert context['preparation_token'] not in response.text
    assert 'supplement-marker' not in response.text
    status = get(env, '').text
    assert 'plan-marker' not in status and 'supplement-marker' not in status
    env.admin.pause(state['session_id'], result['revision'])
    assert get(env, '/resume-plan').json()['data'] == {'available': False}


@pytest.mark.parametrize('field,value', [('session_id', '0' * 32), ('revision', 999),
                                        ('cancel_count', 2)])
def test_stale_identity_revision_or_count_has_no_effects(env, field, value):
    state, rows = paused(env, count=1)
    body = body_from(preview(env).json()['data'])
    body[field] = value
    assert post(env, '/prepare-resume', body).status_code == 409
    assert env.admin.status() == state
    assert env.manager.get(rows[0].id).status == TaskStatus.AWAITING_APPROVAL
    assert env.controller.get_resume_context() == {'available': False}


def test_count_change_after_preview_requires_new_decision(env):
    state, rows = paused(env, count=2)
    body = body_from(preview(env).json()['data'])
    env.manager.cancel(rows[0].id)
    assert post(env, '/prepare-resume', body).status_code == 409
    assert env.manager.get(rows[1].id).status == TaskStatus.AWAITING_APPROVAL
    assert env.admin.status() == state
    assert post(env, '/prepare-resume', body_from(preview(env).json()['data'])).status_code == 200


@pytest.mark.parametrize('field,value', [('confirmed', False), ('confirmed', 1),
    ('cancel_count', True), ('cancel_count', -1), ('supplement', None),
    ('supplement', 'API_TOKEN=private'), ('supplement', 'x' * 3001),
    ('allow_missing_checkpoint', 1), ('revision', True), ('session_id', 'bad')])
def test_invalid_prepare_rejected_before_cancellation(env, field, value):
    state, rows = paused(env, count=1)
    body = body_from(preview(env).json()['data'])
    body[field] = value
    response = post(env, '/prepare-resume', body)
    assert response.status_code == 400
    assert 'API_TOKEN=private' not in response.text
    assert env.admin.status() == state
    assert env.manager.get(rows[0].id).status == TaskStatus.AWAITING_APPROVAL


def test_resume_without_checkpoint_and_old_client_rejected_before_effects(env):
    state, rows = paused(env, count=1)
    view = preview(env).json()['data']
    assert 'checkpoint_missing' not in view
    body = body_from(view)
    assert post(env, '/prepare-resume', dict(body, allow_missing_checkpoint=True)).status_code == 400
    assert env.admin.status() == state
    assert env.manager.get(rows[0].id).status == TaskStatus.AWAITING_APPROVAL
    assert post(env, '/prepare-resume', body).status_code == 200
    assert env.controller.get_resume_context()['available']
    assert env.admin.status()['execution_blocked']


@pytest.mark.parametrize('suffix', ['/resume-preview', '/resume-plan', '/saved',
                                    '/prepare-resume', '/recover'])
def test_new_endpoints_require_auth_host_origin_and_no_store(env, suffix):
    is_post = suffix in ('/prepare-resume', '/recover')
    method = env.client.post if is_post else env.client.get
    kwargs = {'json': {}} if is_post else {}
    assert method(PATH + suffix, headers={'Origin': ORIGIN}, **kwargs).status_code == 401
    for key, value in [('Host', 'evil.example'), ('Origin', 'http://evil.example')]:
        response = method(PATH + suffix, headers={**env.headers, key: value}, **kwargs)
        assert response.status_code == 403 and response.headers['cache-control'] == 'no-store'
    if is_post:
        headers = {key: value for key, value in env.headers.items() if key != 'Origin'}
        assert method(PATH + suffix, headers=headers, **kwargs).status_code == 403


@pytest.mark.parametrize('suffix', ['/prepare-resume', '/recover'])
@pytest.mark.parametrize('body', [{}, [], {'unexpected': True}])
def test_invalid_action_fields(env, suffix, body):
    assert post(env, suffix, body).status_code == 400
    assert env.admin.status()['session_id'] is None


@pytest.mark.parametrize('query', ['', '?session_id=bad&revision=1',
    '?session_id=' + '0' * 32 + '&revision=x',
    '?session_id=' + '0' * 32 + '&revision=1&revision=2'])
def test_invalid_preview_query(env, query):
    assert get(env, '/resume-preview' + query).status_code == 400


@pytest.mark.parametrize('commit_first', [False, True])
def test_uncertain_confirmation_plan_not_advertised_and_api_can_recover(env, monkeypatch, commit_first):
    paused(env)
    assert post(env, '/prepare-resume', body_from(preview(env).json()['data'])).status_code == 200
    original = env.sessions.confirm_resume
    def fail(*args):
        if commit_first:
            original(*args)
        raise OSError('PRIVATE-SQL')
    with monkeypatch.context() as patch:
        patch.setattr(env.sessions, 'confirm_resume', fail)
        with pytest.raises(ResumeOperationError):
            confirm(env)
    assert get(env, '/resume-plan').json()['data'] == {'available': False}
    view = preview(env).json()['data']
    assert view['resume_recovery_required']
    body = body_from(view)
    assert post(env, '/prepare-resume', body).status_code == 200
    assert env.admin.status()['execution_blocked']
    confirm(env)
    assert get(env, '/resume-plan').json()['data']['available']


def test_partial_cancellation_reports_safe_error_and_fresh_preview(env, monkeypatch):
    paused(env, count=3)
    body = body_from(preview(env).json()['data'])
    original = env.tasks.update
    calls = []
    def fail(task_id, **fields):
        result = original(task_id, **fields)
        if fields.get('status') == TaskStatus.CANCELLED:
            calls.append(task_id)
            if len(calls) == 2:
                raise OSError('PRIVATE-SQL')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(env.tasks, 'update', fail)
        response = post(env, '/prepare-resume', body)
    assert response.status_code == 500 and 'PRIVATE-SQL' not in response.text
    assert env.admin.status()['execution_blocked']
    view = preview(env).json()['data']
    assert view['cancel_count'] == 1
    assert post(env, '/prepare-resume', body_from(view)).status_code == 200


def test_explicit_restart_recovery_pins_blocked_and_forbids_switching(env):
    first = env.sessions.create(['sklr'])
    second = env.sessions.create(['sklr'])
    env.sessions.recover_after_restart()
    response = get(env, '/saved')
    assert response.status_code == 200 and not response.json()['data']['truncated']
    items = response.json()['data']['sessions']
    assert all(item['recoverable'] and item['ram_lost'] for item in items)
    assert env.admin.status()['session_id'] is None
    item = next(item for item in items if item['session_id'] == first.id)
    body = {'session_id': first.id, 'revision': item['revision'], 'confirmed': True}
    assert post(env, '/recover', {**body, 'confirmed': False}).status_code == 400
    assert post(env, '/recover', {**body, 'revision': first.revision}).status_code == 409
    response = post(env, '/recover', body)
    assert response.status_code == 200
    state = response.json()['data']
    assert state['session_id'] == first.id and state['ram_lost'] and state['execution_blocked']
    assert not any(key.startswith('checkpoint') for key in state)
    with pytest.raises(SessionBlocked):
        env.controller.begin_action()
    other = env.sessions.get(second.id)
    assert post(env, '/recover', {'session_id': other.id, 'revision': other.revision,
                                 'confirmed': True}).status_code == 409
    assert post(env, '/recover', body).status_code == 409
    assert post(env, '/start', {'roots': ['sklr']}).status_code == 409
    assert all(not row['recoverable'] for row in get(env, '/saved').json()['data']['sessions'])
    prepare_body = body_from(preview(env).json()['data'])
    assert post(env, '/prepare-resume', prepare_body).status_code == 200
    assert env.admin.status()['execution_blocked']


def test_unavailable_roots_and_active_session_are_not_recovery_candidates(env):
    item = env.sessions.create(['sklr'])
    assert not get(env, '/saved').json()['data']['sessions'][0]['recoverable']
    assert post(env, '/recover', {'session_id': item.id, 'revision': item.revision,
                                 'confirmed': True}).status_code == 409
    env.sessions.recover_after_restart()
    env.entries[0]['valid'] = False
    item = env.sessions.get(item.id)
    assert not get(env, '/saved').json()['data']['sessions'][0]['recoverable']
    assert post(env, '/recover', {'session_id': item.id, 'revision': item.revision,
                                 'confirmed': True}).status_code == 400
    assert env.admin.status()['session_id'] is None


def test_manager_mismatch_is_rejected(env):
    with pytest.raises(ValueError, match=r'^Work-session manager mismatch\.$'):
        env.admin.attach_manager(SimpleNamespace(_sessions=SessionController(env.sessions)))
    with pytest.raises(ValueError):
        env.admin.attach_manager(SimpleNamespace(_sessions=env.controller))
    env.admin.attach_manager(env.manager)


def test_store_listing_is_bounded_with_explicit_truncation(env):
    env.sessions.create(['sklr'])
    env.sessions.create(['sklr'])
    items, truncated = env.sessions.list_saved_sessions(limit=1)
    assert len(items) == 1 and truncated
    for value in (True, 0, 201):
        with pytest.raises(ValueError):
            env.sessions.list_saved_sessions(value)


@pytest.mark.parametrize('operation,suffix', [('resume_plan', '/resume-plan'),
                                            ('saved_sessions', '/saved')])
def test_read_errors_do_not_leak_storage_details(env, monkeypatch, operation, suffix):
    def fail():
        raise OSError('PRIVATE-STORAGE')
    monkeypatch.setattr(env.admin, operation, fail)
    response = get(env, suffix)
    assert response.status_code == 500 and 'PRIVATE-STORAGE' not in response.text


def test_resume_text_not_persisted_after_panel_flow(env):
    paused(env)
    assert post(env, '/prepare-resume', body_from(preview(env).json()['data'])).status_code == 200
    context, _ = confirm(env)
    for store in (env.sessions, env.tasks):
        dump = '\n'.join(store._conn.iterdump())
        for marker in ('supplement-marker', 'plan-marker', context['preparation_token']):
            assert marker not in dump


def test_new_routes_absent_without_adapter(env):
    app = create_panel_app(env.manager, env.approval, env.auth,
                           static_dir=env.tmp_path / 'absent')
    with TestClient(app, base_url=ORIGIN) as client:
        for suffix in ('/resume-preview', '/resume-plan', '/saved'):
            assert client.get(PATH + suffix, headers=env.headers).status_code == 404
        for suffix in ('/prepare-resume', '/recover'):
            assert client.post(PATH + suffix, json={}, headers=env.headers).status_code == 404
