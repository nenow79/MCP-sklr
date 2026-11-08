"""M1-L real adapter/control/supervisor with in-memory fake transport.

No helper-owned lifecycle notification or invalidate call. These first tests
exercise automatic idle-runtime closure; spawn races, commit drain and shutdown
store ordering require additional cases. No SDK/network/subprocess/FlowAgent.
"""
from external_mcp.persistent_supervisor import PersistentSupervisor
from external_mcp_persistent_fakes import Clock, Hub
from external_mcp_persistent_manual_helpers import ManualRig
from external_mcp_persistent_admission_helpers import until


class LifecycleRig(ManualRig):
    def __init__(self, path):
        super().__init__(path)
        self.clock = Clock()
        self.hub = Hub(self.clock)
        self.supervisor = PersistentSupervisor(transport=self.hub.transport,
            session_factory=self.hub.session_factory,
            validate_arguments=self.hub.validate, clock=self.clock)
        # Same accepted injection seam as M1. Production adapter must arrange
        # notifications; the test never binds a replacement lifecycle policy.
        self.tools.persistent_supervisor = self.supervisor

    def discover(self):
        import json
        from tasks.models import TaskStatus
        from external_mcp_persistent_manual_helpers import SCHEMA
        task = self.settle(self.tools.list_tools('fake'))
        assert task.status == TaskStatus.DONE, task.error
        result = self.manager.get_external_result(task.id)
        assert result['state'] == 'available', result
        state = self.supervisor.snapshot(owner=self.session.id,
                                         profile_id=self.profile['id'])
        self.generation = result['data']['runtime_generation']
        assert self.generation and self.generation == state['runtime_generation']
        assert json.loads(result['data']['content'])['tools'][0]['inputSchema'] == SCHEMA
        return task

    def call(self, generation=None):
        return self.tools.call_tool('fake', 'lookup', {'q': 'local'},
            expected_runtime_generation=generation or self.generation)

    def assert_closed(self):
        until(lambda: self.hub.backends[0].closed.is_set(),
              'automatic runtime cleanup')
        state = self.supervisor.snapshot(owner=self.session.id,
                                         profile_id=self.profile['id'])
        assert state['runtime_generation'] is None
        assert state['state'] == 'absent', state
        assert self.hub.backends[0].calls == []
        assert self.hub.backends[0].cleanup_attempts == 1

    def close(self):
        # Teardown safety only, not proof of a production notification. Release
        # every fake gate before shutdown so assertion failure cannot leak thread.
        for backend in self.hub.backends:
            for name in ('start', 'transport_start', 'session_start', 'catalog',
                         'validation', 'call', 'cleanup'):
                getattr(backend, name).release.set()
        try:
            close_worker = getattr(self.tools, 'close_lifecycle', None)
            if close_worker is not None:
                assert close_worker() is True, 'lifecycle worker did not stop'
            self.supervisor.shutdown()
        finally:
            super().close()
