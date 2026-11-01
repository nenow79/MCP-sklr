"""Profile API security and conflict mapping, no server or local configuration."""
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from panel.project_profiles import project_profile_routes
from server.project_profiles import ProfileConflict
from tasks.session_models import SessionConflict

ORIGIN = 'http://127.0.0.1:8102'


@pytest.fixture
def api():
    calls = []
    service = SimpleNamespace(list=lambda: {'profiles': [], 'selected': None, 'revision': 'v1'},
                              change=lambda action, body: calls.append((action, body)) or {'profiles': []})
    auth = PanelAuth()
    app = create_panel_app(object(), object(), auth)
    # Same SecurityMiddleware as production; compose routes independently of main.
    app.router.routes.extend(project_profile_routes(service))
    headers = {'Origin': ORIGIN, 'Cookie': f'{COOKIE_NAME}={auth.exchange_token(auth.token)}'}
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, headers, service, calls


def test_start_api_requires_revision_and_uses_profile_coordinator():
    calls = []
    auth = PanelAuth()
    sessions = SimpleNamespace(attach_manager=lambda manager: None,
                               start=lambda roots: pytest.fail('unguarded Start called'))
    def start(roots, revision):
        calls.append((roots, revision))
        if revision != 'current':
            raise ProfileConflict('stale')
        return {'session_id': 'fresh', 'execution_blocked': False}
    service = SimpleNamespace(start=start)
    app = create_panel_app(object(), object(), auth, work_sessions=sessions, project_profiles=service)
    headers = {'Origin': ORIGIN, 'Cookie': f'{COOKIE_NAME}={auth.exchange_token(auth.token)}'}
    with TestClient(app, base_url=ORIGIN) as client:
        url = '/api/work-session/start'
        assert client.post(url, json={'roots': ['src']}, headers=headers).status_code == 400
        assert calls == []
        assert client.post(url, json={'roots': ['src'], 'project_revision': 'stale'}, headers=headers).status_code == 409
        response = client.post(url, json={'roots': ['src'], 'project_revision': 'current'}, headers=headers)
        assert response.status_code == 201 and response.json()['data']['session_id'] == 'fresh'
        assert calls == [(['src'], 'stale'), (['src'], 'current')]


def test_list_and_change(api):
    client, headers, service, calls = api
    response = client.get('/api/project-profiles', headers=headers)
    assert response.status_code == 200 and response.json()['data']['revision'] == 'v1'
    body = {'revision': 'v1', 'confirmed': True, 'name': 'QAP'}
    assert client.post('/api/project-profiles/create', json=body, headers=headers).status_code == 200
    assert calls == [('create', body)]


def test_security_and_invalid_json(api):
    client, headers, service, calls = api
    url = '/api/project-profiles/create'
    assert client.post(url, json={}, headers={'Origin': ORIGIN}).status_code == 401
    assert client.post(url, json={}, headers={'Cookie': headers['Cookie']}).status_code == 403
    for override in ({'Host': 'evil.example'}, {'Origin': 'http://evil.example'}):
        assert client.post(url, json={}, headers={**headers, **override}).status_code == 403
    assert client.get('/api/project-profiles', headers={'Cookie': headers['Cookie']}).status_code == 200
    assert client.post(url, content='bad json', headers=headers).status_code == 400
    assert calls == []


@pytest.mark.parametrize('exception,code', [(ProfileConflict('stale'), 409),
    (SessionConflict('active'), 409), (ValueError('invalid'), 400), (OSError('PRIVATE'), 500)])
def test_safe_error_mapping(api, exception, code):
    client, headers, service, calls = api
    def fail(*args):
        raise exception
    service.change = fail
    response = client.post('/api/project-profiles/select', json={}, headers=headers)
    assert response.status_code == code
    assert 'PRIVATE' not in response.text
