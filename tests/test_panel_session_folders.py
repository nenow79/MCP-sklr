"""Shared folder/session API: paused edits, stale decisions and delegated scope."""
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from autopilot_fixtures import environment
from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from server.autopilot_admin import AutopilotAdmin
from server.session_admin import SessionAdmin

ORIGIN = 'http://127.0.0.1:8102'


@pytest.fixture
def env(tmp_path):
    with environment(tmp_path) as e:
        sessions = SessionAdmin(e.session_store, e.sessions, e.roots)
        autopilot = AutopilotAdmin(e.runtime)
        auth = PanelAuth()
        app = create_panel_app(e.manager, e.manager._approval, auth,
            roots=e.roots, work_sessions=sessions, autopilot=autopilot)
        cookie = auth.exchange_token(auth.token)
        with TestClient(app, base_url=ORIGIN) as client:
            yield SimpleNamespace(e=e, sessions=sessions, autopilot=autopilot, client=client,
                headers={'Origin': ORIGIN, 'Cookie': f'{COOKIE_NAME}={cookie}'})


def identity(env):
    state = env.sessions.status()
    return {k: state[k] for k in ('session_id', 'revision')}


@pytest.mark.parametrize('suffix,extra', [('app/update', {'description': 'Changed'}),
    ('other/delete', {}), ('other/default', {})])
def test_active_edits_are_rejected_before_registry_write(env, suffix, extra):
    before = env.e.roots.list_status()
    response = env.client.post('/api/roots/' + suffix,
        json=dict(identity(env), **extra), headers=env.headers)
    assert response.status_code == 409
    assert env.e.roots.list_status() == before


def test_paused_add_joins_session_and_rejects_stale_tab(env):
    env.e.pause()
    previous = identity(env)
    folder = env.e.app.parent / 'new-folder'
    folder.mkdir()
    response = env.client.post('/api/roots', headers=env.headers,
        json=dict(previous, path=str(folder), alias='new', mode='rw'))
    assert response.status_code == 201
    state = env.sessions.status()
    assert state['roots'] == ['app', 'other', 'new']
    assert state['revision'] == previous['revision'] + 1
    assert state['state'] == 'paused' and state['execution_blocked']
    assert env.client.post('/api/roots/new/delete', json=previous,
        headers=env.headers).status_code == 409
    assert env.e.roots.get('new') is not None


def test_edit_revokes_delegation_and_resume_scope(env):
    env.e.approve([env.e.file_rule('read_file')])
    env.e.pause()
    current = identity(env)
    assert env.e.autopilot.snapshot()['state'] == 'suspended'
    response = env.client.post('/api/roots/app/update', headers=env.headers,
        json=dict(current, description='Changed project'))
    assert response.status_code == 200
    assert env.autopilot.status()['state'] == 'disabled'
    assert env.autopilot.status()['policy'] is None
    assert env.e.autopilot.resume_scope is None
    assert env.sessions.status()['execution_blocked']


def test_pending_handshake_prevents_folder_edit(env):
    env.e.pause()
    env.e.manager.prepare_session_resume('', identity(env)['revision'])
    response = env.client.post('/api/roots/other/delete', headers=env.headers, json=identity(env))
    assert response.status_code == 409
    assert env.e.roots.get('other') is not None
