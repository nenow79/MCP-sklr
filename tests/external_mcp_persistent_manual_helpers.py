"""M1 adapter rig: real registry/TaskManager/WorkSession; fake supervisor.

Proposed injection API: ExternalMCPTools(..., persistent_supervisor=...).
No production policy is implemented here. Supervisor double records routing;
these tests do not accept spawn/commit races, lifecycle wiring, SDK or cleanup.
No network, subprocess, real profiles, FlowAgent or delegated accounting.
"""
import copy
import json
from types import SimpleNamespace

from config_store import ConfigStore
from external_mcp.client import schema_digest
from external_mcp.registry import ExternalMCPRegistry
from server.external_mcp_tools import ExternalMCPTools
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.models import TaskStatus
from tasks.session_controller import SessionController
from tasks.session_store import SessionStore
from tasks.store import TaskStore

SCHEMA = {'type': 'object', 'properties': {'q': {'type': 'string'}}}
TOOL = {'name': 'lookup', 'inputSchema': SCHEMA, 'description': 'local fake'}
DIGEST = schema_digest(SCHEMA)


class LegacyClient:
    def __init__(self):
        self.calls = []

    def fetch_catalog(self, *args, **kwargs):
        self.calls.append('catalog')
        raise AssertionError('Persistent routed to one-shot')

    def call_tool(self, *args, **kwargs):
        self.calls.append('call')
        raise AssertionError('Persistent routed to one-shot')


class SupervisorDouble:
    def __init__(self):
        self.generation = 'm1-fake-generation'
        self.discoveries = []
        self.calls = []

    def snapshot(self, *, owner, profile_id):
        return {'state': 'ready', 'runtime_generation': self.generation}

    def fetch_catalog(self, profile, *, owner, timeout=60, cancel_event=None, **kwargs):
        self.discoveries.append((owner, profile['id']))
        return {'tools': [copy.deepcopy(TOOL) | {'schemaSha256': DIGEST}],
                'runtime_generation': self.generation}

    def call_tool(self, profile, name, arguments, *, owner,
                  expected_runtime_generation, expected_schema_sha256=None,
                  timeout=60, cancel_event=None, dispatch_admission=None, **kwargs):
        # Assertions observe adapter inputs, not a substitute dispatch policy.
        assert dispatch_admission is not None
        assert 'before_dispatch' not in kwargs
        assert expected_runtime_generation == self.generation
        assert expected_schema_sha256 == DIGEST
        self.calls.append((owner, name, copy.deepcopy(arguments)))
        return {'result': {'content': [{'type': 'text', 'text': 'local result'}],
                           'isError': False}, 'schemaSha256': DIGEST,
                'runtime_generation': self.generation}


class ManualRig:
    def __init__(self, path, *, selected=True):
        path.mkdir(parents=True, exist_ok=True)
        config_path = path / 'config.json'
        self.config = ConfigStore(__import__('state').load_config(config_path), config_path)
        self.registry = ExternalMCPRegistry(self.config)
        self.registry.add({'alias': 'fake', 'transport': 'stdio',
            'command': 'never-executed', 'stdio_mode': 'persistent', 'enabled': True},
            self.registry.list()['revision'])
        self.profile = self.registry.resolve('fake')['profile']
        self.sessions = SessionStore(path / 'sessions.db')
        self.session = self.sessions.create(['sklr'])
        self.controller = SessionController(self.sessions)
        if selected:
            self.controller.bind(self.session.id, self.session.revision)
        self.store = TaskStore(str(path / 'tasks.db'))
        self.approval = ApprovalService()
        self.manager = TaskManager(self.store, self.approval, session_controller=self.controller)
        self.supervisor = SupervisorDouble()
        self.legacy = LegacyClient()
        self.tools = ExternalMCPTools(self.registry, self.manager, client=self.legacy,
                                      persistent_supervisor=self.supervisor)

    def settle(self, response):
        assert response['success'], response
        task = self.manager.wait(response['data']['task_id'], 5)
        assert task.is_terminal, task.status
        return task

    def discover(self):
        task = self.settle(self.tools.list_tools('fake'))
        assert task.status == TaskStatus.DONE, task.error
        data = self.manager.get_external_result(task.id)
        assert data['state'] == 'available', data
        assert data['data']['runtime_generation'] == self.supervisor.generation
        assert json.loads(data['data']['content'])['tools'][0]['inputSchema'] == SCHEMA
        return task

    def call(self, generation=None):
        return self.tools.call_tool('fake', 'lookup', {'q': 'local'},
            expected_runtime_generation=generation or self.supervisor.generation)

    def trust(self):
        self.registry.set_trust(self.profile['id'], {'lookup': DIGEST},
                                self.registry.list()['revision'])

    def close(self):
        self.manager.shutdown()
        close_worker = getattr(self.tools, 'close_lifecycle', None)
        if close_worker is not None:
            assert close_worker()
        self.store.close()
        self.sessions.close()
