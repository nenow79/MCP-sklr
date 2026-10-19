"""M5b adapter: real registry/TaskManager; fake client, no network or subprocess."""
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from external_mcp.client import schema_digest
from external_mcp.limits import ExternalError
from external_mcp.registry import ExternalMCPRegistry
from external_mcp.trust import TrustRateLimiter
from server.external_mcp_tools import ExternalMCPTools, argument_preview
from state import load_config
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.models import TaskStatus
from tasks.store import TaskStore

SCHEMA = {'type': 'object', 'properties': {'q': {'type': 'string'}}}
TOOL = {'name': 'lookup', 'description': '<script>external</script>', 'inputSchema': SCHEMA}
PRIVATE = 'PRIVATE_PAYLOAD_SENTINEL'


class Client:
    def __init__(self):
        self.tools = [copy.deepcopy(TOOL)]
        self.calls = []
        self.catalog_calls = 0
        self.before_guard = None
        self.failure = None
        self.result = {'content': [{'type': 'text', 'text': PRIVATE}], 'isError': False}

    def fetch_catalog(self, profile, *, timeout, cancel_event, blocked_ports):
        self.catalog_calls += 1
        return {'tools': copy.deepcopy(self.tools)}

    def call_tool(self, profile, name, arguments, *, expected_schema_sha256, timeout,
                  cancel_event, blocked_ports, before_dispatch):
        tool = next(item for item in self.tools if item['name'] == name)
        digest = schema_digest(tool['inputSchema'])
        if digest != expected_schema_sha256:
            raise ExternalError('schema_changed')
        if self.before_guard:
            self.before_guard()
        before_dispatch(name, digest)
        self.calls.append(copy.deepcopy(arguments))
        if self.failure:
            raise self.failure
        return {'result': copy.deepcopy(self.result), 'schemaSha256': digest}


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / 'config.json'
    config = ConfigStore(load_config(config_path), config_path)
    registry = ExternalMCPRegistry(config)
    registry.add({'alias': 'browser', 'transport': 'http', 'url': 'https://example.test/mcp',
                  'enabled': True}, registry.list()['revision'])
    store = TaskStore(str(tmp_path / 'tasks.db'))
    approval = ApprovalService()
    manager = TaskManager(store, approval)
    client = Client()
    clock = SimpleNamespace(now=1000.0)
    tools = ExternalMCPTools(registry, manager, client=client, clock=lambda: clock.now)
    e = SimpleNamespace(registry=registry, manager=manager, store=store,
                        client=client, clock=clock, tools=tools, tmp_path=tmp_path)
    yield e
    manager.shutdown()
    store.close()


def settle(e, response):
    assert response['success'], response
    task = e.manager.wait(response['data']['task_id'], 5)
    assert task.is_terminal, task.status
    return task


def discover(e):
    task = settle(e, e.tools.list_tools('browser'))
    assert task.status == TaskStatus.DONE, task.error
    return task


def trust(e):
    listing = e.registry.list()
    profile = listing['profiles'][0]
    e.registry.set_trust(profile['id'], {'lookup': schema_digest(SCHEMA)}, listing['revision'])
    discover(e)


def test_local_list_without_connection_or_configuration_values(env):
    listing = env.tools.list_profiles()
    assert listing['success'] and listing['data']['count'] == 1
    row = listing['data']['profiles'][0]
    assert row['enabled'] and row['alias'] == 'browser'
    assert 'url' not in row and 'headers_env' not in row and 'trust' not in row
    assert env.client.catalog_calls == 0 and env.client.calls == []


def test_discovery_result_ram_only_and_call_requires_catalog(env):
    assert env.tools.call_tool('browser', 'lookup', {})['code'] == 'catalog_required'
    task = discover(env)
    assert task.decision == '' and not task.offer_trust
    content = env.manager.get_external_result(task.id)['data']['content']
    assert json.loads(content)['tools'][0]['inputSchema'] == SCHEMA
    assert '<script>' not in json.dumps(task.to_dict())


def test_ordinary_call_waits_for_user_and_payload_not_in_database(env):
    discover(env)
    response = env.tools.call_tool('browser', 'lookup', {'q': PRIVATE, 'token': PRIVATE})
    assert response['success'], response
    task_id = response['data']['task_id']
    assert env.manager.get(task_id).status == TaskStatus.AWAITING_APPROVAL
    assert env.client.calls == []
    preview = env.manager.get_approval_preview(task_id)
    assert '***' in preview['text']
    assert PRIVATE not in json.dumps(env.manager.get(task_id).to_dict())
    env.manager.resolve_approval(task_id, 'allow')
    done = env.manager.wait(task_id, 5)
    assert done.status == TaskStatus.DONE, done.error
    assert env.client.calls == [{'q': PRIVATE, 'token': PRIVATE}]
    assert PRIVATE in env.manager.get_external_result(task_id)['data']['content']
    for path in env.tmp_path.glob('tasks.db*'):
        assert PRIVATE.encode() not in path.read_bytes()
    assert env.manager.get_approval_preview(task_id) is None


def test_trusted_call_no_approval_and_rate_limit_falls_back_same_task(env):
    trust(env)
    # Exercise exactly the production default of 30 calls/min/profile.
    for _ in range(30):
        done = settle(env, env.tools.call_tool('browser', 'lookup', {}))
        assert done.status == TaskStatus.DONE and done.decision == '', done.error
    requested = threading.Event()
    env.manager._approval.set_notifier(lambda task: requested.set())
    response = env.tools.call_tool('browser', 'lookup', {})
    assert response['success'], response
    task_id = response['data']['task_id']
    assert requested.wait(5)
    assert env.manager.get(task_id).status == TaskStatus.AWAITING_APPROVAL
    assert len(env.client.calls) == 30
    assert env.manager.get_approval_preview(task_id) is not None
    env.manager.resolve_approval(task_id, 'allow')
    assert env.manager.wait(task_id, 5).status == TaskStatus.DONE
    assert len(env.client.calls) == 31


def test_rate_limited_denial_never_dispatches(env):
    trust(env)
    env.tools.limiter = TrustRateLimiter(limit=1)
    settle(env, env.tools.call_tool('browser', 'lookup', {}))
    requested = threading.Event()
    env.manager._approval.set_notifier(lambda task: requested.set())
    response = env.tools.call_tool('browser', 'lookup', {})
    assert requested.wait(5)
    env.manager.resolve_approval(response['data']['task_id'], 'deny')
    assert len(env.client.calls) == 1


def test_revoked_trust_after_session_open_blocks_dispatch(env):
    trust(env)
    def revoke():
        listing = env.registry.list()
        env.registry.revoke_trust(listing['profiles'][0]['id'], listing['revision'])
    env.client.before_guard = revoke
    task = settle(env, env.tools.call_tool('browser', 'lookup', {}))
    assert task.status == TaskStatus.FAILED
    assert env.client.calls == []


def test_schema_changed_after_discovery_requires_new_approval(env):
    trust(env)
    env.client.tools[0]['inputSchema'] = {'type': 'object', 'required': ['new']}
    task = settle(env, env.tools.call_tool('browser', 'lookup', {}))
    assert task.status == TaskStatus.FAILED and 'schema_changed' in task.error
    assert env.client.calls == []
    discover(env)
    response = env.tools.call_tool('browser', 'lookup', {'new': True})
    assert env.manager.get(response['data']['task_id']).status == TaskStatus.AWAITING_APPROVAL
    env.manager.resolve_approval(response['data']['task_id'], 'deny')


def test_profile_change_while_awaiting_approval_blocks_dispatch(env):
    discover(env)
    response = env.tools.call_tool('browser', 'lookup', {})
    listing = env.registry.list()
    env.registry.set_enabled(listing['profiles'][0]['id'], False, listing['revision'])
    env.manager.resolve_approval(response['data']['task_id'], 'allow')
    assert env.manager.wait(response['data']['task_id'], 5).status == TaskStatus.FAILED
    assert env.client.calls == []


def test_expired_catalog_and_result_denied_after_disable(env):
    task = discover(env)
    env.clock.now += 301
    assert env.tools.call_tool('browser', 'lookup', {})['code'] == 'catalog_expired'
    listing = env.registry.list()
    env.registry.set_enabled(listing['profiles'][0]['id'], False, listing['revision'])
    assert env.manager.get_external_result(task.id)['state'] == 'denied'


def test_large_result_rejected_without_truncation(env):
    trust(env)
    env.client.result['content'][0]['text'] = 'x' * 80001
    task = settle(env, env.tools.call_tool('browser', 'lookup', {}))
    assert task.status == TaskStatus.FAILED and 'external_result_limit' in task.error
    assert env.manager.get_external_result(task.id)['state'] == 'unavailable'
    assert len(env.client.calls) == 1


def test_raw_client_exception_not_saved_or_retried(env):
    trust(env)
    env.client.failure = RuntimeError(PRIVATE)
    task = settle(env, env.tools.call_tool('browser', 'lookup', {}))
    assert task.status == TaskStatus.FAILED
    assert PRIVATE not in json.dumps(task.to_dict())
    assert len(env.client.calls) == 1


def test_preview_strips_url_credentials_query_fragment():
    arguments = {'url': 'https://user:PRIVATE@example.test/path?token=PRIVATE#PRIVATE',
                 'api_key': PRIVATE}
    preview = argument_preview(arguments)
    assert PRIVATE not in preview['text']
    assert 'https://example.test/path' in preview['text']
    assert arguments['api_key'] == PRIVATE
