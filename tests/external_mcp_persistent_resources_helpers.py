"""P3c-C1 proposed API; observation only, no production admission implementation.

ResourceDispatchAdmission(manager,event,scope,*,registry,snapshot,tool_name,
 schema_digest,limiter,panel_available) in external_mcp.persistent_resources
extends DispatchAdmission and preserves dispatch_boundary -> one-use commit.
Detached snapshot/name/schema pins. Final checks under manager -> controller ->
ConfigStore -> supervisor; config retained through commit, released before I/O.
Approved skips trust/rate limit only. Authorized needs current trust. Limiter
charged once at commit AFTER runtime/cancel/deadline checks. Exhaustion raises
ExternalError('approval_required') BEFORE any dispatch marker. Adapter may map
only that proven pre-dispatch signal to ExternalApprovalRequired (same task).
Panel callback is a local no-I/O read, never a control-lock callback.

No SDK/process/network/FlowAgent or real delegation. Existing P3c-B rig provides
manager/controller/supervisor and fake transport. No live configuration changes.
"""
import copy
import importlib
from contextlib import contextmanager

from config_store import ConfigStore
from external_mcp.client import schema_digest
from external_mcp.persistent_admission import DispatchAdmission
from external_mcp.registry import ExternalMCPRegistry
from external_mcp.trust import TrustRateLimiter
from external_mcp_persistent_dispatch_helpers import DispatchRig, production_types
from external_mcp_persistent_fakes import TOOL
from state import load_config

DIGEST = schema_digest(TOOL['inputSchema'])


def resource_type():
    cls = importlib.import_module('external_mcp.persistent_resources').ResourceDispatchAdmission
    assert issubclass(cls, DispatchAdmission)
    return cls


class ResourceRig(DispatchRig):
    def __init__(self, path, cls):
        admission, supervisor = production_types()
        super().__init__(path, admission, supervisor)
        config_path = path / 'config.json'
        self.config = ConfigStore(load_config(config_path), config_path)
        self.registry = ExternalMCPRegistry(self.config)
        self.registry.add({'alias': 'fake', 'transport': 'stdio', 'command': 'fake-not-executed',
                           'stdio_mode': 'persistent', 'enabled': True}, self.registry.list()['revision'])
        self.profile = self.registry.resolve('fake')['profile']
        self.registry.set_trust(self.profile['id'], {'lookup': DIGEST}, self.registry.list()['revision'])
        self.refresh_pin()
        self.panel = True
        self.limiter = TrustRateLimiter(clock=self.clock.monotonic)
        rig = self
        class BoundResources(cls):
            def __init__(self, manager, event, scope):
                super().__init__(manager,event,scope,registry=rig.registry,
                    snapshot=rig.pin,tool_name='lookup',schema_digest=DIGEST,
                    limiter=rig.limiter,panel_available=lambda: rig.panel)
        self.admission_type = BoundResources

    def refresh_pin(self):
        self.pin = self.registry.resolve('fake')
        self.profile = copy.deepcopy(self.pin['profile'])

    def mutate(self, kind):
        revision = self.registry.list()['revision']
        pid = self.profile['id']
        if kind == 'disable':
            self.registry.set_enabled(pid, False, revision)
        elif kind == 'delete':
            self.registry.remove(pid, revision)
        elif kind == 'trust':
            self.registry.revoke_trust(pid, revision)
        else:
            proposal = {key: copy.deepcopy(value) for key,value in self.profile.items()
                        if key not in {'id','revision','trust'}}
            if kind == 'connection':
                proposal['command'] = 'other-fake-not-executed'
            elif kind == 'mode':
                proposal['stdio_mode'] = 'one_shot'
            else:
                raise AssertionError('unknown mutation')
            self.registry.update(pid, proposal, revision)

    def resource_admission(self, attempt, before=None, before_commit=None):
        observer, scope = self.admission(attempt, before)
        original = observer.dispatch_boundary
        rig = self
        @contextmanager
        def observe(name, sha, trust_check=None):
            with original(name,sha,trust_check) as commit:
                assert rig.config.lock._is_owned(), 'config not retained through boundary'
                if before_commit is not None:
                    before_commit()
                def checked_commit(mark):
                    assert rig.config.lock._is_owned(), 'config released before commit'
                    return commit(mark)
                yield checked_commit
        observer.dispatch_boundary = observe
        return observer,scope

    def hits(self):
        with self.limiter._lock:
            return len(self.limiter._hits.get(self.profile['id'], ()))

    def raw_call(self, event, observer, *, name='lookup', digest=DIGEST):
        return self.supervisor.call_tool(self.profile,name,{'q':'fake'},owner=self.session.id,
            expected_runtime_generation=self.generation,expected_schema_sha256=digest,
            cancel_event=event,dispatch_admission=observer)
