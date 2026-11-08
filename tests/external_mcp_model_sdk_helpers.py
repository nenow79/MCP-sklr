"""Isolated real model API/approvals/SDK composition for L3.

Only tmp profiles and local Python MCP peers. Main bootstrap and telemetry
writer are not imported. Token/arguments are never written to fixture files.
"""
import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio
import httpx2 as httpx
from fastmcp import Client

from config_store import ConfigStore
from external_mcp.registry import ExternalMCPRegistry
from external_mcp_model_api_helpers import model_server
from external_mcp_persistent_sdk_helpers import SERVER, alive, eventually, force_kill
from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from server.external_mcp_runtime import create_external_mcp_tools
from server.session_activation import stop_work_session
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.models import TaskStatus
from tasks.session_controller import SessionController
from tasks.session_store import SessionStore
from tasks.store import TaskStore


class ModelSDKRig:
    def __init__(self, folder):
        self.folder = folder
        self.peers = {}
        script = folder / 'model peer with spaces.py'
        script.write_text(SERVER, encoding='utf-8')
        config_path = folder / 'config.json'
        self.config = ConfigStore(__import__('state').load_config(config_path), config_path)
        self.registry = ExternalMCPRegistry(self.config)
        for alias, mode in (('fake', 'persistent'), ('one-shot', 'one_shot')):
            peer = folder / alias
            peer.mkdir()
            self.peers[alias] = peer
            self.registry.add({'alias': alias, 'transport': 'stdio', 'stdio_mode': mode,
                'enabled': True, 'command': sys.executable, 'args': [str(script), str(peer)],
                'cwd': str(peer)}, self.registry.list()['revision'])
        self.sessions = SessionStore(folder / 'sessions.db')
        self.session = self.sessions.create(['sklr'])
        self.controller = SessionController(self.sessions)
        self.controller.bind(self.session.id, self.session.revision)
        self.store = TaskStore(str(folder / 'tasks.db'))
        self.approval = ApprovalService()
        self.manager = TaskManager(self.store, self.approval, session_controller=self.controller)
        self.tools = create_external_mcp_tools(self.registry, self.manager, panel_available=lambda: True)
        self.model = model_server(self.tools, self.manager, self.controller)
        self.auth = PanelAuth(port=8102)
        self.panel = create_panel_app(self.manager, self.approval, self.auth)
        self.pids = set()

    @asynccontextmanager
    async def clients(self):
        session = self.auth.exchange_token(self.auth.token)
        async with Client(self.model) as model:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.panel),
                    base_url='http://127.0.0.1:8102',
                    headers={'Cookie': f'{COOKIE_NAME}={session}',
                             'Origin': 'http://127.0.0.1:8102'}) as panel:
                yield model, panel

    async def api(self, client, name, arguments):
        response = await client.call_tool(name, arguments)
        assert not response.is_error
        assert isinstance(response.structured_content, dict), response
        return response.structured_content

    async def decision(self, panel, response, value='allow'):
        assert response['success'], response
        tid = response['data']['task_id']
        assert self.manager.get(tid).status == TaskStatus.AWAITING_APPROVAL
        result = await panel.post(f'/api/tasks/{tid}/decision', json={'decision': value})
        assert result.status_code == 200, result.text
        return tid

    async def result(self, client, response):
        assert response['success'], response
        tid = response['data']['task_id']
        task = await anyio.to_thread.run_sync(self.manager.wait, tid, 10)
        assert task.is_terminal, task.status
        self.remember_pids()
        return await self.api(client, 'get_task', {'task_id': tid})

    async def discover(self, client, alias='fake'):
        response = await self.api(client, 'list_external_mcp_tools', {'alias': alias, 'timeout': 20})
        done = await self.result(client, response)
        assert done['data']['status'] == 'done', done
        result = done['data']['external_result']
        assert result['state'] == 'available', result
        return result['data']

    async def submit(self, client, name, arguments=None, generation=None, alias='fake', timeout=20):
        return await self.api(client, 'call_external_mcp_tool', {
            'alias': alias, 'tool_name': name, 'arguments': arguments or {},
            'timeout': timeout, 'expected_runtime_generation': generation})

    def number(self, filename, alias='fake', default=0):
        path = self.peers[alias] / filename
        return int(path.read_text()) if path.exists() else default

    def remember_pids(self):
        for alias in self.peers:
            pid = self.number('server.pid', alias)
            if pid:
                self.pids.add(pid)

    def assert_gone(self):
        self.remember_pids()
        eventually(lambda: all(not alive(pid) for pid in self.pids),
                   seconds=8, label='model SDK peer survived cleanup')

    def finish(self):
        self.remember_pids()
        runtime = SimpleNamespace(controller=self.controller, close=self.sessions.close)
        confirmed = False
        try:
            confirmed = stop_work_session(self.manager, runtime, budget=12, external_mcp=self.tools)
            self.assert_gone()
            assert confirmed, 'model SDK shutdown/store ordering unconfirmed'
        finally:
            for pid in self.pids:
                if alive(pid):
                    force_kill(pid)
                    confirmed = False
            if not confirmed:
                # Rescue only: never evidence of accepted cleanup.
                self.tools.close_persistent(timeout=10)
                for worker in self.manager._workers:
                    worker.join(10)
            assert not any(w.is_alive() for w in self.manager._workers)
            self.store.close()
            self.sessions.close()


def business(done):
    assert done['data']['status'] == 'done', done
    result = done['data']['external_result']
    assert result['state'] == 'available', result
    return json.loads(result['data']['content'])['result']
