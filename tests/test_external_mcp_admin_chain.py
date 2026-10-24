"""Admin -> REAL external_mcp.client.fetch_catalog; only transport and session are fake.

The panel API tests use a fake client, which cannot notice a signature or key mismatch with
the real one. This chain does: it fails if the admin passes arguments the client rejects or
if the catalog lacks the schema digests that trust is pinned to.
"""
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from external_mcp import client as real_client
from external_mcp.client import schema_digest
from external_mcp.registry import ExternalMCPRegistry
from server.external_mcp_admin import ExternalMCPAdmin
from state import load_config
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.models import TaskStatus
from tasks.store import TaskStore

SCHEMAS = {
    'read_page': {'type': 'object', 'properties': {'tab_index': {'type': 'integer'}}},
    'open_url': {'type': 'object', 'properties': {'url': {'type': 'string'}}, 'required': ['url']},
}


class Transport:
    @asynccontextmanager
    async def __call__(self, runtime, blocked_ports):
        yield ('read', 'write')


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def initialize(self):
        return None

    async def list_tools(self, params=None):
        tools = [{'name': name, 'description': 'd', 'inputSchema': schema}
                 for name, schema in SCHEMAS.items()]
        return SimpleNamespace(tools=tools, next_cursor=None)


class RealClientWithFakes:
    """Exposes the real fetch_catalog; the admin's keyword arguments pass through unchanged."""

    @staticmethod
    def fetch_catalog(profile, **kwargs):
        return real_client.fetch_catalog(
            profile, transport=Transport(), session_factory=lambda streams, timeout: Session(),
            **kwargs)


@pytest.fixture
def env(tmp_path):
    path = tmp_path / 'config.json'
    registry = ExternalMCPRegistry(ConfigStore(load_config(path), path))
    task_store = TaskStore(str(tmp_path / 'tasks.db'))
    manager = TaskManager(task_store, ApprovalService())
    admin = ExternalMCPAdmin(registry, manager, client=RealClientWithFakes, environ={})
    yield registry, manager, admin
    manager.shutdown()
    for worker in manager._workers:
        worker.join(5)
    assert not any(worker.is_alive() for worker in manager._workers)
    task_store.close()


def settle(admin, token, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = admin.get_inspection(token)
        if result['state'] != 'pending':
            return result
        time.sleep(0.05)
    raise AssertionError('inspection did not finish')


def test_inspection_goes_through_the_real_client_and_pins_real_digests(env):
    registry, manager, admin = env
    registry.add({'alias': 'browser', 'transport': 'http', 'url': 'https://example.test/mcp'},
                 registry.list()['revision'])
    profile = admin.list()['profiles'][0]
    started = admin.start_inspection(profile['id'])
    result = settle(admin, started['token'])
    assert result['state'] == 'ready', result
    digests = {tool['name']: tool['schemaSha256'] for tool in result['tools']}
    assert digests == {name: schema_digest(schema) for name, schema in SCHEMAS.items()}
    assert manager.wait(started['task_id'], 5).status == TaskStatus.DONE

    granted = admin.grant_trust(profile['id'], started['token'], None, admin.list()['revision'])
    assert granted['profiles'][0]['trust']['tools'] == digests
    enabled = registry.set_enabled(profile['id'], True, granted['revision'])
    assert enabled['profiles'][0]['enabled'] is True
    assert registry.trust_decision('browser', 'read_page', digests['read_page'])['trusted'] is True
    changed = schema_digest({'type': 'object', 'properties': {'extra': {'type': 'string'}}})
    assert registry.trust_decision('browser', 'read_page', changed)['reason'] == 'schema_changed'
