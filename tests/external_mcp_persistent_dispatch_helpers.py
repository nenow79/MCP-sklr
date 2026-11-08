"""P3c-B rig: real manager/controller/supervisor, fake transport/session.

Accepted proposed INTERNAL API, not implemented by this helper:
  PersistentSupervisor.call_tool(..., dispatch_admission=None).
  dispatch_admission and before_dispatch are mutually exclusive. Runtime owner
  must match the admission's pinned task owner; no session_id from model input.
  DispatchAdmission.dispatch_boundary(name, schema_sha, trust_check=None):
    context manager yielding commit(mark_request_dispatched).
    Explicit scope/trust recheck BEFORE manager/controller locks; then hold
    manager -> controller while supervisor acquires its own lock and performs
    final runtime/cancel/deadline checks. commit is single-use, marks admission,
    scope and request in one protected boundary. No commit on refusal/exception.
    No locks held during business I/O. Existing guard API remains available.

Observing wrappers below call production APIs, never implement admission.
No SDK, subprocess, network, production routing, profile/trust atomicity or
real delegated accounting. Lifecycle changes are explicitly driven by tests;
this does NOT accept production lifecycle callbacks or OS containment.
"""
import importlib
import inspect
import threading
from contextlib import contextmanager

from autopilot.context import CURRENT
from external_mcp.limits import ExternalError
from external_mcp_persistent_admission_helpers import Rig as AdmissionRig, Scope, until, wait
from external_mcp_persistent_fakes import Clock, Gate, Hub, TOOL
from external_mcp.client import schema_digest
from external_mcp.profiles import validate_profile
from tasks.models import TaskStatus

DIGEST = schema_digest(TOOL['inputSchema'])


def production_types():
    admission = importlib.import_module('external_mcp.persistent_admission').DispatchAdmission
    supervisor = importlib.import_module('external_mcp.persistent_supervisor').PersistentSupervisor
    assert callable(getattr(admission, 'dispatch_boundary', None)), 'P3c-B missing dispatch_boundary API'
    assert 'dispatch_admission' in inspect.signature(supervisor.call_tool).parameters, 'P3c-B missing supervisor admission API'
    return admission, supervisor


class CheckedScope(Scope):
    def __init__(self, rig):
        super().__init__()
        self.rig = rig

    def recheck(self):
        assert not self.rig.manager._cond._is_owned()
        assert not self.rig.controller._lock._is_owned()
        assert not self.rig.supervisor._lock._is_owned()
        super().recheck()


class DispatchRig(AdmissionRig):
    def __init__(self, path, admission_type, supervisor_type):
        super().__init__(path)
        self.clock = Clock()
        self.hub = Hub(self.clock)
        self.supervisor = supervisor_type(transport=self.hub.transport,
            session_factory=self.hub.session_factory,
            validate_arguments=self.hub.validate, clock=self.clock)
        self.profile = validate_profile({'alias': 'fake', 'transport': 'stdio',
            'command': 'fake-not-executed', 'stdio_mode': 'persistent', 'enabled': True})
        self.profile.update(id='11111111-1111-4111-8111-111111111111',
                            revision='22222222-2222-4222-8222-222222222222')
        self.admission_type = admission_type
        self.generation = None
        self.commits = []
        self.prepares = []
        self.observers = []
        self.slot_wait = threading.Event()
        original = self.supervisor._check_request
        def observe_request(request):
            # Observation only; no scheduler/admission replacement. A busy
            # reserved runtime means the second request cannot own the slot.
            if request.kind == 'call':
                with self.supervisor._lock:
                    runtime = self.supervisor._runtimes.get((self.session.id, self.profile['id']))
                    if runtime is not None and runtime.reserved and runtime.active is not request:
                        self.slot_wait.set()
            return original(request)
        self.supervisor._check_request = observe_request

    def discover(self, attempt, scope=None):
        def operation(event):
            marker = CURRENT.set(scope)
            try:
                return self.supervisor.fetch_catalog(self.profile, owner=self.session.id,
                    cancel_event=event, before_start=lambda: self.controller.admit_dispatch(attempt.task.id) and None)
            finally:
                CURRENT.reset(marker)
        result = attempt.run(operation)
        self.generation = result['runtime_generation']
        return result

    @property
    def backend(self):
        return self.hub.backends[-1]

    def admission(self, attempt, before=None):
        rig, base = self, self.admission_type
        class ObservedAdmission(base):
            @contextmanager
            def dispatch_boundary(self, name, sha, trust_check=None):
                assert CURRENT.get() is None, 'Supervisor inherited operation context'
                assert not rig.supervisor._lock._is_owned(), 'Boundary entered under supervisor lock'
                rig.prepares.append(attempt.task.id)
                if before is not None:
                    before()
                with super().dispatch_boundary(name, sha, trust_check) as commit:
                    assert rig.manager._cond._is_owned()
                    assert rig.controller._lock._is_owned()
                    def observe_commit(mark_request):
                        assert rig.supervisor._lock._is_owned()
                        value = commit(mark_request)
                        runtime = rig.supervisor._runtimes[(rig.session.id, rig.profile['id'])]
                        request = runtime.active
                        assert request.cancel is attempt.event
                        assert request.dispatched and self.admitted and self._scope.dispatched
                        rig.commits.append(attempt.task.id)
                        return value
                    yield observe_commit
                assert CURRENT.get() is None, 'Boundary leaked operation scope'
        scope = CheckedScope(self)
        observer = attempt.run(lambda event: ObservedAdmission(self.manager, event, scope))
        self.observers.append(observer)
        return observer, scope

    def call(self, attempt, observer, timeout=60):
        def operation(event):
            try:
                return self.supervisor.call_tool(self.profile, 'lookup', {'q': 'fake'},
                    owner=self.session.id, expected_runtime_generation=self.generation,
                    expected_schema_sha256=DIGEST, timeout=timeout,
                    cancel_event=event, dispatch_admission=observer)
            except BaseException:
                observer.mark_uncertain_if_admitted()
                raise
        return attempt.command(operation)

    def invalidate(self, reason='pause'):
        self.supervisor.invalidate(owner=self.session.id, profile_id=self.profile['id'], reason=reason)

    def lifecycle(self, kind):
        state = getattr(self, kind)()
        # Explicit harness action, OUTSIDE controller/manager locks. This is
        # not a production listener and cannot accept lifecycle wiring.
        self.invalidate(kind)
        return state

    def finish_attempt(self, attempt):
        if not attempt.event.is_set():
            attempt.finish()
        else:
            # The reused P3c-A executor deliberately asserts no cancel. Here
            # cancellation is the subject: confidential finalization gives the
            # set cancel event precedence over that executor assertion error.
            attempt.commands.put(None)
            assert self.manager.wait(attempt.task.id, 5).status == TaskStatus.CANCELLED
            wait(attempt.finished, 'cancelled executor finish')

    def close(self):
        # Unblock ALL phases before waiting for executor or owner completion.
        for attempt in self.attempts:
            for release in attempt.barriers:
                release.set()
        for backend in self.hub.backends:
            for gate in (backend.start, backend.transport_start, backend.session_start,
                         backend.catalog, backend.validation, backend.call, backend.cleanup):
                gate.release.set()
        # Supervisor may still call manager/controller until its shutdown ends.
        self.supervisor.shutdown()
        super().close()


def refusal(future, admission, scope):
    try:
        future.result(timeout=5)
    except ExternalError as exc:
        assert 'uncertain' not in exc.code
    else:
        raise AssertionError('Pre-dispatch call was not refused')
    assert not admission.admitted and not scope.dispatched and not scope.uncertain
