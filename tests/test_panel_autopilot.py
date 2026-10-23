"""Authenticated A2b4 HTTP routes, without production main or live servers."""
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from autopilot_fixtures import environment
from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from server.autopilot_admin import AutopilotAdmin
from tasks.approval import ApprovalService

ORIGIN = 'http://127.0.0.1:8102'


@pytest.fixture
def env(tmp_path):
    with environment(tmp_path) as e:
        e.pause()
        admin = AutopilotAdmin(e.runtime)
        auth = PanelAuth()
        app = create_panel_app(e.manager, ApprovalService(), auth, autopilot=admin)
        cookie = auth.exchange_token(auth.token)
        with TestClient(app, base_url=ORIGIN) as client:
            yield SimpleNamespace(e=e, admin=admin, client=client,
                headers={'Origin': ORIGIN, 'Cookie': f'{COOKIE_NAME}={cookie}'})


def body(env):
    state = env.admin.status()
    return {key: state[key] for key in ('session_id', 'revision', 'decision_revision')}


@pytest.mark.parametrize('action', ['preview', 'approve', 'reconfirm', 'revoke', 'manual', 'resolve', 'acknowledge-recovery'])
def test_mutations_require_auth_host_origin(env, action):
    path = '/api/autopilot/' + action
    assert env.client.post(path, json={}, headers={'Origin': ORIGIN}).status_code == 401
    assert env.client.post(path, json={}, headers={**env.headers, 'Host': 'evil.test'}).status_code == 403
    assert env.client.post(path, json={}, headers={**env.headers, 'Origin': 'http://evil.test'}).status_code == 403
    assert env.client.post(path, json={}, headers={'Cookie': env.headers['Cookie']}).status_code == 403


def test_reads_require_auth_and_do_not_expose_profile_settings(env):
    for path in ('/api/autopilot', '/api/autopilot/resources'):
        assert env.client.get(path).status_code == 401
        response = env.client.get(path, headers=env.headers)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert 'connection_digest' not in response.text


def test_preview_approve_conflict_and_closed_fields(env):
    request = dict(body(env), goal='Create files.', plan='Create in app only.', limits={},
                   policy={'version': 1, 'rules': [dict(id='x', kind='files', tools=['create_file'],
                        root='root:app', scope={'kind': 'tree', 'path': '.'}, overwrite=False)]})
    preview = env.client.post('/api/autopilot/preview', json=request, headers=env.headers)
    assert preview.status_code == 200
    assert 'preview_resources;dur=' in preview.headers['server-timing']
    decision = dict(body(env), confirmed=True, preview_id=preview.json()['data']['preview_id'])
    assert env.client.post('/api/autopilot/approve', json=decision, headers=env.headers).status_code == 200
    assert env.client.post('/api/autopilot/approve', json=decision, headers=env.headers).status_code == 409
    assert env.e.sessions.snapshot()['execution_blocked']
    assert env.client.post('/api/autopilot/revoke', json=dict(body(env), confirmed=True, extra=True),
                           headers=env.headers).status_code == 400


def test_size_limit_and_safe_error(env, monkeypatch):
    response = env.client.post('/api/autopilot/preview', content='x' * 80001, headers=env.headers)
    assert response.status_code == 413
    def fail():
        raise RuntimeError('PRIVATE-DATABASE-PATH')
    monkeypatch.setattr(env.admin, 'status', fail)
    response = env.client.get('/api/autopilot', headers=env.headers)
    assert response.status_code == 503 and 'PRIVATE' not in response.text


def test_disabled_routes_are_absent(env):
    auth = PanelAuth()
    app = create_panel_app(env.e.manager, None, auth)
    headers = {'Origin': ORIGIN, 'Cookie': f'{COOKIE_NAME}={auth.exchange_token(auth.token)}'}
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get('/api/autopilot', headers=headers).status_code == 404


def test_resource_phases_and_status_have_numeric_server_timing(env):
    status = env.client.get('/api/autopilot', headers=env.headers)
    assert status.status_code == 200
    assert 'app;dur=' in status.headers['server-timing']
    assert 'catalog_scan' not in status.headers['server-timing']
    resources = env.client.get('/api/autopilot/resources', headers=env.headers)
    assert resources.status_code == 200
    header = resources.headers['server-timing']
    assert 'catalog_scan;dur=' in header and 'catalog_skills;dur=' in header
    for entry in header.split(', '):
        name, duration = entry.split(';dur=')
        assert name.replace('_', '').isalpha() and float(duration) >= 0
    assert 'private' not in header.lower()


@pytest.mark.parametrize('error_name,status', [('PreviewInProgress', 409), ('PreviewTimedOut', 504)])
def test_preview_busy_and_timeout_explain_the_block_without_private_details(env, monkeypatch, error_name, status):
    from server import autopilot_admin
    def fail(*args):
        raise getattr(autopilot_admin, error_name)('PRIVATE-PATH')
    monkeypatch.setattr(env.admin, 'preview', fail)
    request = dict(body(env), goal='Example', plan='Example', limits={}, policy={})
    response = env.client.post('/api/autopilot/preview', json=request, headers=env.headers)
    assert response.status_code == status
    assert 'zgoda nie została udzielona' in response.json()['error'].lower()
    assert 'PRIVATE' not in response.text
