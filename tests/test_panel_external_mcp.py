"""M4a: panel API for external MCP profiles with a fake client. No MCP, no network, no process."""
import hashlib
import json
import threading
import time

import pytest
from starlette.testclient import TestClient

from config_store import ConfigStore
from external_mcp.limits import ExternalError
from external_mcp.registry import ExternalMCPRegistry
from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from server.external_mcp_admin import INSPECTION_TTL_S, ExternalMCPAdmin
from state import load_config
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.models import TaskStatus
from tasks.store import TaskStore

ORIGIN = 'http://127.0.0.1:8102'
SECRET = 'SENTINEL_SECRET_VALUE'


def tool(name):
    return {'name': name, 'description': 'untrusted <b>text</b>', 'inputSchema': {'type': 'object'},
            'schemaSha256': hashlib.sha256(name.encode()).hexdigest()}


class FakeClient:
    def __init__(self):
        self.calls, self.error, self.hang = [], None, False
        self.tools = [tool('read_page'), tool('open_url')]

    def fetch_catalog(self, profile, *, timeout, cancel_event, environ, blocked_ports):
        self.calls.append({'alias': profile['alias'], 'timeout': timeout,
                           'blocked_ports': blocked_ports})
        if self.hang:
            cancel_event.wait(10)
            raise ExternalError('cancelled')
        if self.error is not None:
            raise self.error
        return {'tools': [dict(item) for item in self.tools]}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Env:
    pass


@pytest.fixture
def env(tmp_path):
    e = Env()
    e.path = tmp_path / 'config.json'
    e.store = ConfigStore(load_config(e.path), e.path)
    e.registry = ExternalMCPRegistry(e.store)
    e.fake, e.clock, e.environ = FakeClient(), Clock(), {}
    task_store = TaskStore(str(tmp_path / 'tasks.db'))
    e.approval = ApprovalService()
    e.manager = TaskManager(task_store, e.approval)
    e.admin = ExternalMCPAdmin(e.registry, e.manager, client=e.fake, environ=e.environ,
                               clock=e.clock)
    auth = PanelAuth(port=8102)
    e.app = create_panel_app(e.manager, e.approval, auth, external_mcp=e.admin)
    e.client = TestClient(e.app, base_url=ORIGIN)
    session = auth.exchange_token(auth.token)
    e.read_headers = {'Cookie': f'{COOKIE_NAME}={session}'}
    e.headers = {**e.read_headers, 'Origin': ORIGIN}

    e.get = lambda path: e.client.get(path, headers=e.read_headers)
    e.post = lambda path, body: e.client.post(path, json=body, headers=e.headers)
    e.listing = lambda: e.get('/api/external-mcp').json()['data']

    def add(alias='browser', **values):
        profile = {'alias': alias, 'transport': 'http', 'url': 'https://example.test/mcp'}
        profile.update(values)
        return e.post('/api/external-mcp', {'profile': profile, 'revision': e.listing()['revision']})
    e.add = add

    def first_id():
        return e.listing()['profiles'][0]['id']
    e.first_id = first_id

    def inspect(profile_id=None, **body):
        return e.post(f'/api/external-mcp/{profile_id or first_id()}/inspect', body)
    e.inspect = inspect

    def settle(token, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            data = e.get(f'/api/external-mcp/inspections/{token}').json()['data']
            if data['state'] != 'pending':
                return data
            time.sleep(0.05)
        raise AssertionError('inspection did not finish')
    e.settle = settle
    yield e
    e.manager.shutdown()
    task_store.close()


# --- access control ---

def test_requires_session_and_origin(env):
    assert env.client.get('/api/external-mcp').status_code == 401
    body = {'profile': {}, 'revision': 'x'}
    assert env.client.post('/api/external-mcp', json=body,
                           headers=env.read_headers).status_code == 403


def test_routes_absent_without_admin(tmp_path):
    store = TaskStore(str(tmp_path / 't.db'))
    approval = ApprovalService()
    manager = TaskManager(store, approval)
    auth = PanelAuth(port=8102)
    try:
        client = TestClient(create_panel_app(manager, approval, auth), base_url=ORIGIN)
        session = auth.exchange_token(auth.token)
        response = client.get('/api/external-mcp', headers={'Cookie': f'{COOKIE_NAME}={session}'})
        assert response.status_code == 404
    finally:
        manager.shutdown()
        store.close()


# --- profiles ---

def test_empty_list_is_valid(env):
    data = env.listing()
    assert data['configuration_valid'] and data['count'] == 0 and data['revision']


def test_add_update_enable_remove_with_revisions(env):
    added = env.add().json()['data']
    profile = added['profiles'][0]
    assert profile['enabled'] is False and profile['trusted'] is False
    assert profile['last_test'] is None
    changed = env.post(f"/api/external-mcp/{profile['id']}/update", {
        'profile': {'alias': 'renamed', 'transport': 'http', 'url': 'https://example.test/mcp'},
        'revision': added['revision']}).json()['data']
    assert changed['profiles'][0]['alias'] == 'renamed' and changed['profiles'][0]['id'] == profile['id']
    enabled = env.post(f"/api/external-mcp/{profile['id']}/enabled",
                       {'enabled': True, 'revision': changed['revision']}).json()['data']
    assert enabled['profiles'][0]['enabled'] is True
    removed = env.post(f"/api/external-mcp/{profile['id']}/delete",
                       {'revision': enabled['revision']}).json()['data']
    assert removed['count'] == 0
    assert ExternalMCPRegistry(ConfigStore(load_config(env.path), env.path)).list()['count'] == 0


def test_stale_revision_conflict_unknown_profile_and_invalid_input(env):
    env.add()
    stale = 'stale-revision'
    pid = env.first_id()
    assert env.post(f'/api/external-mcp/{pid}/enabled',
                    {'enabled': True, 'revision': stale}).status_code == 409
    assert env.post('/api/external-mcp/00000000-0000-4000-8000-000000000000/delete',
                    {'revision': env.listing()['revision']}).status_code == 404
    bad = env.client.post('/api/external-mcp', headers=env.headers, json={
        'profile': {'alias': 'Bad Alias', 'transport': 'http', 'url': 'https://example.test'},
        'revision': env.listing()['revision']})
    assert bad.status_code == 400 and 'alias' in bad.json()['error']
    assert env.client.post('/api/external-mcp', headers=env.headers, content=b'not json').status_code == 400
    assert env.post(f'/api/external-mcp/{pid}/unknown', {}).status_code == 404


def test_trust_cannot_be_injected_through_profile_edit(env):
    response = env.add(trust={'tools': {'x': 'a' * 64}, 'connection_sha256': 'b' * 64,
                              'granted_at': '2026-01-01T00:00:00+00:00'})
    assert response.status_code == 400 and 'unknown_fields' in response.json()['error']
    assert env.listing()['count'] == 0


def test_secret_values_are_never_returned_only_missing_names(env):
    env.add(bearer_env='REMOTE_TOKEN')
    assert env.listing()['profiles'][0]['env_missing'] == ['REMOTE_TOKEN']
    env.environ['REMOTE_TOKEN'] = SECRET
    text = env.get('/api/external-mcp').text
    assert SECRET not in text and env.listing()['profiles'][0]['env_missing'] == []
    assert SECRET not in env.path.read_text(encoding='utf-8')


def test_profiles_survive_restart_but_test_state_does_not(env):
    env.add()
    token = env.inspect().json()['data']['token']
    assert env.settle(token)['state'] == 'ready'
    assert env.listing()['profiles'][0]['last_test']['state'] == 'ok'
    restarted = ExternalMCPAdmin(ExternalMCPRegistry(ConfigStore(load_config(env.path), env.path)),
                                 env.manager, client=env.fake)
    view = restarted.list()['profiles'][0]
    assert view['alias'] == 'browser' and view['last_test'] is None


# --- inspection ---

def test_inspection_runs_as_task_without_approval_and_even_when_disabled(env):
    env.add()
    response = env.inspect()
    assert response.status_code == 202
    data = response.json()['data']
    result = env.settle(data['token'])
    assert result['state'] == 'ready' and [t['name'] for t in result['tools']] == ['read_page', 'open_url']
    assert result['tools'][0]['description'] == 'untrusted <b>text</b>'  # data, not markup
    task = env.manager.wait(data['task_id'], 5)
    assert task.status == TaskStatus.DONE and task.kind == 'external_mcp' and task.decision == ''
    assert env.fake.calls[0]['blocked_ports'] == (8101, 8102) and env.fake.calls[0]['timeout'] == 60
    assert env.listing()['profiles'][0]['last_test'] == {
        'state': 'ok', 'code': '', 'at': env.listing()['profiles'][0]['last_test']['at'], 'tools': 2}


def test_task_record_and_result_channel_hold_only_a_summary(env):
    env.add()
    data = env.inspect().json()['data']
    env.settle(data['token'])
    task = env.manager.wait(data['task_id'], 5)
    blob = json.dumps(task.to_dict())
    assert 'untrusted' not in blob and data['token'] not in blob
    channel = env.manager.get_external_result(data['task_id'])
    assert json.loads(channel['data']['content']) == {'ok': True, 'tools': 2}


def test_stdio_inspection_requires_explicit_acknowledgement(env):
    added = env.post('/api/external-mcp', {
        'profile': {'alias': 'local', 'transport': 'stdio', 'command': 'python'},
        'revision': env.listing()['revision']})
    assert added.status_code == 200
    refused = env.inspect()
    assert refused.status_code == 400 and 'launch_not_acknowledged' in refused.json()['error']
    assert env.fake.calls == []
    assert env.inspect(acknowledge_launch=True).status_code == 202


def test_failed_inspection_reports_only_a_code(env):
    env.add()
    env.fake.error = ExternalError('timeout')
    data = env.inspect().json()['data']
    result = env.settle(data['token'])
    assert result['state'] == 'failed' and result['code'] == 'timeout' and result['tools'] == []
    task = env.manager.wait(data['task_id'], 5)
    assert task.status == TaskStatus.FAILED and task.error == 'Zewnętrzne MCP: timeout'
    assert env.listing()['profiles'][0]['last_test']['state'] == 'failed'


def test_unexpected_client_error_text_never_reaches_task_or_panel(env):
    env.add()
    env.fake.error = RuntimeError(SECRET)
    data = env.inspect().json()['data']
    result = env.settle(data['token'])
    task = env.manager.wait(data['task_id'], 5)
    assert result['code'] == 'client_failed' and SECRET not in json.dumps([result, task.to_dict()])


def test_cancelling_the_task_marks_the_inspection_failed(env):
    env.add()
    env.fake.hang = True
    data = env.inspect().json()['data']
    deadline = time.monotonic() + 5
    while not env.fake.calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert env.manager.cancel(data['task_id']).status == TaskStatus.CANCELLED
    assert env.settle(data['token'])['code'] == 'cancelled'


def test_unknown_and_expired_inspections_are_404(env):
    assert env.get('/api/external-mcp/inspections/unknown').status_code == 404
    env.add()
    token = env.inspect().json()['data']['token']
    env.settle(token)
    env.clock.now += INSPECTION_TTL_S + 1
    assert env.get(f'/api/external-mcp/inspections/{token}').status_code == 404


def test_inspection_of_missing_profile_is_404(env):
    assert env.inspect('00000000-0000-4000-8000-000000000000').status_code == 404


# --- trust ---

def ready(env, **profile):
    env.add(**profile)
    token = env.inspect().json()['data']['token']
    assert env.settle(token)['state'] == 'ready'
    return token


def test_trust_is_pinned_from_server_snapshot_for_all_or_chosen_tools(env):
    token = ready(env)
    pid = env.first_id()
    granted = env.post(f'/api/external-mcp/{pid}/trust', {
        'token': token, 'revision': env.listing()['revision']}).json()['data']
    trust = granted['profiles'][0]['trust']
    assert trust['tools'] == {t['name']: t['schemaSha256'] for t in env.fake.tools}
    assert granted['profiles'][0]['trusted'] is True
    subset = env.post(f'/api/external-mcp/{pid}/trust', {
        'token': token, 'tools': ['read_page'], 'revision': granted['revision']}).json()['data']
    assert set(subset['profiles'][0]['trust']['tools']) == {'read_page'}
    revoked = env.post(f'/api/external-mcp/{pid}/revoke-trust',
                       {'revision': subset['revision']}).json()['data']
    assert revoked['profiles'][0]['trusted'] is False


def test_browser_cannot_supply_schema_digests(env):
    token = ready(env)
    pid = env.first_id()
    response = env.post(f'/api/external-mcp/{pid}/trust', {
        'token': token, 'tools': {'read_page': 'a' * 64}, 'revision': env.listing()['revision']})
    assert response.status_code == 400 and 'trust_tools' in response.json()['error']
    assert env.listing()['profiles'][0]['trusted'] is False


@pytest.mark.parametrize('tools', [[], ['missing'], 'read_page', ['read_page', 'read_page'], [5]])
def test_bad_tool_selection_is_rejected_without_writing(env, tools):
    token = ready(env)
    before = env.listing()
    response = env.post(f'/api/external-mcp/{env.first_id()}/trust', {
        'token': token, 'tools': tools, 'revision': before['revision']})
    assert response.status_code == 400 and env.listing() == before


def test_trust_needs_a_ready_inspection_of_the_same_profile(env):
    ready(env, alias='first')
    env.add(alias='second')
    first, second = (p['id'] for p in env.listing()['profiles'])
    token_first = env.inspect(first).json()['data']['token']
    env.settle(token_first)
    wrong = env.post(f'/api/external-mcp/{second}/trust', {
        'token': token_first, 'revision': env.listing()['revision']})
    assert wrong.status_code == 400 and 'inspection_unavailable' in wrong.json()['error']
    assert env.post(f'/api/external-mcp/{first}/trust', {
        'token': 'bogus', 'revision': env.listing()['revision']}).status_code == 400
    env.fake.error = ExternalError('timeout')
    failed = env.inspect(second).json()['data']['token']
    env.settle(failed)
    assert env.post(f'/api/external-mcp/{second}/trust', {
        'token': failed, 'revision': env.listing()['revision']}).status_code == 400


def test_connection_change_after_inspection_blocks_trust_and_clears_it(env):
    token = ready(env)
    pid = env.first_id()
    env.post(f'/api/external-mcp/{pid}/trust', {'token': token, 'revision': env.listing()['revision']})
    moved = env.post(f'/api/external-mcp/{pid}/update', {
        'profile': {'alias': 'browser', 'transport': 'http', 'url': 'https://example.test/other'},
        'revision': env.listing()['revision']}).json()['data']
    assert moved['profiles'][0]['trusted'] is False
    stale = env.post(f'/api/external-mcp/{pid}/trust', {'token': token, 'revision': moved['revision']})
    assert stale.status_code == 400 and 'inspection_stale' in stale.json()['error']
    assert env.listing()['profiles'][0]['last_test'] is None


def test_trust_with_stale_revision_conflicts_and_writes_nothing(env):
    token = ready(env)
    before = env.listing()
    response = env.post(f'/api/external-mcp/{env.first_id()}/trust',
                        {'token': token, 'revision': 'stale'})
    assert response.status_code == 409 and env.listing() == before


def test_removing_a_profile_forgets_its_inspections(env):
    token = ready(env)
    pid = env.first_id()
    env.post(f'/api/external-mcp/{pid}/delete', {'revision': env.listing()['revision']})
    assert env.get(f'/api/external-mcp/inspections/{token}').status_code == 404
