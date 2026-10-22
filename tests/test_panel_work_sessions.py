"""Work-session panel API tests; no uvicorn, network MCP or production main."""
import threading
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from server.session_admin import SessionAdmin
from tasks.approval import ApprovalService
from tasks.checkpoint_store import CheckpointStore
from tasks.manager import TaskManager
from tasks.session_controller import SessionController
from tasks.session_store import SessionStore
from tasks.store import TaskStore

ORIGIN = 'http://127.0.0.1:8102'
PATH = '/api/work-session'
PRIVATE = 'PRIVATE-API-STORAGE-DETAIL'


@pytest.fixture
def env(tmp_path):
    sessions = SessionStore(tmp_path / 'sessions.db')
    tasks = TaskStore(str(tmp_path / 'tasks.db'))
    controller = SessionController(sessions)
    roots = SimpleNamespace(list_status=lambda: [{'alias': 'sklr', 'valid': True}])
    admin = SessionAdmin(sessions, controller, roots)
    approval = ApprovalService()
    manager = TaskManager(tasks, approval, session_controller=controller)
    auth = PanelAuth(port=8102)
    app = create_panel_app(manager, approval, auth, static_dir=tmp_path / 'absent',
                           work_sessions=admin)
    cookie = auth.exchange_token(auth.token)
    headers = {'Origin': ORIGIN, 'Cookie': f'{COOKIE_NAME}={cookie}'}
    with TestClient(app, base_url=ORIGIN) as client:
        yield SimpleNamespace(client=client, headers=headers, admin=admin,
                              controller=controller, sessions=sessions,
                              tasks=tasks, manager=manager, approval=approval, auth=auth,
                              tmp_path=tmp_path)
    manager.shutdown()
    for worker in manager._workers:
        worker.join(timeout=6)
    assert not any(worker.is_alive() for worker in manager._workers)
    tasks.close()
    sessions.close()


def start(env):
    response = env.client.post(PATH + '/start', json={'roots': ['sklr']}, headers=env.headers)
    assert response.status_code == 201
    return response.json()['data']


def test_status_start_and_pause(env):
    response = env.client.get(PATH, headers=env.headers)
    assert response.status_code == 200
    assert response.json()['data']['session_id'] is None
    active = start(env)
    response = env.client.post(PATH + '/pause', headers=env.headers,
                               json={'session_id': active['session_id'], 'revision': 1})
    assert response.status_code == 200
    state = response.json()['data']
    assert state['execution_blocked'] and state['state'] == 'paused'
    assert not state['checkpoint_available'] and not state['resume_available']
    assert state['roots'] == ['sklr']
    assert 'wszystkie rozmowy' in state['warning']
    assert env.client.post(PATH + '/start', json={'roots': ['sklr']},
                           headers=env.headers).status_code == 409


@pytest.mark.parametrize('suffix,body', [('', None), ('/checkpoint', None), ('/start', {'roots': ['sklr']}),
    ('/pause', {'session_id': '0' * 32, 'revision': 1}),
    ('/end', {'session_id': '0' * 32, 'revision': 1, 'confirmed': True})])
def test_endpoints_require_panel_auth(env, suffix, body):
    method = env.client.get if body is None else env.client.post
    kwargs = {} if body is None else {'json': body}
    assert method(PATH + suffix, headers={'Origin': ORIGIN}, **kwargs).status_code == 401
    assert method(PATH + suffix, headers={**env.headers, 'Host': 'evil.example'},
                  **kwargs).status_code == 403
    assert method(PATH + suffix, headers={**env.headers, 'Origin': 'http://evil.example'},
                  **kwargs).status_code == 403
    assert env.admin.status()['session_id'] is None


@pytest.mark.parametrize('suffix', ['/start', '/pause', '/end'])
def test_post_requires_origin(env, suffix):
    headers = {key: value for key, value in env.headers.items() if key != 'Origin'}
    response = env.client.post(PATH + suffix, json={}, headers=headers)
    assert response.status_code == 403


@pytest.mark.parametrize('body', [{}, [], {'roots': []}, {'roots': 'sklr'},
    {'roots': ['unknown']}, {'roots': ['sklr'], 'resume': True}])
def test_invalid_start_body(env, body):
    assert env.client.post(PATH + '/start', json=body, headers=env.headers).status_code == 400
    assert env.admin.status()['session_id'] is None


@pytest.mark.parametrize('body', [{}, [], {'session_id': 1, 'revision': 1},
    {'session_id': '0' * 32, 'revision': True}, {'session_id': '0' * 32, 'revision': 0},
    {'session_id': '0' * 32, 'revision': '1'},
    {'session_id': '0' * 32, 'revision': 1, 'force': True}])
def test_invalid_pause_body_does_not_change_latch(env, body):
    active = start(env)
    assert env.client.post(PATH + '/pause', json=body, headers=env.headers).status_code == 400
    assert env.admin.status() == active


@pytest.mark.parametrize('suffix', ['/start', '/pause', '/end'])
def test_invalid_json(env, suffix):
    response = env.client.post(PATH + suffix, content='not-json', headers=env.headers)
    assert response.status_code == 400


def test_stale_revision_reports_conflict_and_real_latch(env):
    active = start(env)
    response = env.client.post(PATH + '/pause', headers=env.headers,
                               json={'session_id': active['session_id'], 'revision': 99})
    assert response.status_code == 409
    state = env.client.get(PATH, headers=env.headers).json()['data']
    assert state['execution_blocked'] and state['state'] == 'active'


@pytest.mark.parametrize('operation,suffix,body', [
    ('status', '', None), ('checkpoint', '/checkpoint', None),
    ('start', '/start', {'roots': ['sklr']}),
    ('pause', '/pause', {'session_id': '0' * 32, 'revision': 1}),
])
def test_internal_errors_are_not_exposed(env, monkeypatch, operation, suffix, body):
    def fail(*args):
        raise OSError(PRIVATE)
    monkeypatch.setattr(env.admin, operation, fail)
    if body is None:
        response = env.client.get(PATH + suffix, headers=env.headers)
    else:
        response = env.client.post(PATH + suffix, json=body, headers=env.headers)
    assert response.status_code == 500
    assert PRIVATE not in response.text


def test_approval_during_panel_pause_does_not_run_task(env, monkeypatch):
    active = start(env)
    calls, attempted = [], threading.Event()
    task = env.manager.submit({}, lambda event: calls.append(1) or {'exit_code': 0}, True)
    original = env.controller.begin_task
    def record(task_id):
        try:
            return original(task_id)
        finally:
            attempted.set()
    monkeypatch.setattr(env.controller, 'begin_task', record)
    assert env.client.post(PATH + '/pause', headers=env.headers,
        json={'session_id': active['session_id'], 'revision': 1}).status_code == 200
    response = env.client.post(f'/api/tasks/{task.id}/decision', json={'decision': 'allow'},
                               headers=env.headers)
    assert response.status_code == 200 and attempted.wait(5)
    current = env.manager.get(task.id)
    assert current.status.value == 'queued' and current.decision == 'allow'
    assert calls == [] and current.started_at is None
    assert env.client.post(f'/api/tasks/{task.id}/cancel', headers=env.headers).status_code == 200
    assert env.manager.get(task.id).status.value == 'cancelled'


def test_checkpoint_absent_with_legacy_controller(env):
    response = env.client.get(PATH + '/checkpoint', headers=env.headers)
    assert response.status_code == 200 and response.json()['data'] == {'available': False}
    assert response.headers['cache-control'] == 'no-store'


def test_checkpoint_api_returns_ram_content_but_status_only_metadata(env):
    # Existing fixture exercises the legacy no-RAM controller; enable RAM only
    # in this test, before any session is selected.
    env.controller._checkpoints = CheckpointStore()
    active = start(env)
    assert env.client.get(PATH + '/checkpoint', headers=env.headers).json()['data'] == {'available': False}
    paused = env.client.post(PATH + '/pause', headers=env.headers, json={
        'session_id': active['session_id'], 'revision': active['revision']}).json()['data']
    marker = 'RAM-only-checkpoint-panel-marker'
    saved = env.controller.save_checkpoint({'goal': marker, 'next_step': 'Wait'}, paused['revision'])
    status = env.client.get(PATH, headers=env.headers)
    assert marker not in status.text
    assert status.json()['data']['checkpoint_available'] is True
    assert status.json()['data']['checkpoint_content_available'] is True
    response = env.client.get(PATH + '/checkpoint', headers=env.headers)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    view = response.json()['data']
    assert view['available'] and view['checkpoint']['goal'] == marker
    assert view['revision'] == saved['revision']
    assert env.client.post(PATH + '/checkpoint', json={}, headers=env.headers).status_code == 405
    env.controller._checkpoints.clear()
    status = env.client.get(PATH, headers=env.headers).json()['data']
    assert status['checkpoint_state'] == 'saved' and not status['checkpoint_content_available']
    assert env.client.get(PATH + '/checkpoint', headers=env.headers).json()['data'] == {'available': False}


def test_endpoints_absent_without_adapter(env):
    app = create_panel_app(env.manager, env.approval, env.auth,
                           static_dir=env.tmp_path / 'absent')
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get(PATH, headers=env.headers).status_code == 404
        assert client.get(PATH + '/checkpoint', headers=env.headers).status_code == 404
        assert client.post(PATH + '/start', json={'roots': ['sklr']},
                           headers=env.headers).status_code == 404
        assert client.post(PATH + '/pause', json={}, headers=env.headers).status_code == 404
        assert client.post(PATH + '/resume', json={}, headers=env.headers).status_code == 404


def test_end_api_clears_selection_and_rejects_old_identity(env):
    active = start(env)
    body = dict(session_id=active['session_id'], revision=active['revision'], confirmed=True)
    assert env.client.post(PATH + '/end', headers=env.headers, json=dict(body, confirmed=False)).status_code == 400
    assert env.admin.status() == active
    response = env.client.post(PATH + '/end', headers=env.headers, json=body)
    assert response.status_code == 200
    assert response.json()['data']['session_id'] is None
    fresh = start(env)
    assert fresh['session_id'] != active['session_id']
    assert env.client.post(PATH + '/end', headers=env.headers, json=body).status_code == 409
    assert env.admin.status() == fresh


@pytest.mark.parametrize('body', [{}, [], {'session_id': 'bad', 'revision': 1, 'confirmed': True},
    {'session_id': '0' * 32, 'revision': True, 'confirmed': True},
    {'session_id': '0' * 32, 'revision': 1, 'confirmed': 1},
    {'session_id': '0' * 32, 'revision': 1, 'confirmed': True, 'extra': 1}])
def test_invalid_end_body(env, body):
    assert env.client.post(PATH + '/end', headers=env.headers, json=body).status_code == 400
    assert env.admin.status()['session_id'] is None
