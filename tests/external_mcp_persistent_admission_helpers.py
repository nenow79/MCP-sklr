"""P3c-A admission test rig; real manager/controller, fake business calls.

Accepted internal API (not implemented by this test helper):
  SessionController.admit_dispatch(task_id): current task owner/gate check,
    raises SessionBlocked, does not allocate another action token.
  TaskManager.admit_external_dispatch(event): exact live external executor,
    manager -> controller -> stores; returns immutable ticket with
    task_id, session_id, consent ('approved' or 'authorized').
  DispatchAdmission(manager, event, scope): explicit per-operation scope.
    guard(name, schema_sha, trust_check=None) -> None; fixed dispatch_guard
    refusal; authorized consent needs trust_check(name, schema_sha) == True.
    admitted: bool; mark_uncertain_if_admitted() only after admission.

Scope is a test double, not a real delegated AutopilotRuntime: these tests do
not accept autopilot accounting, profile/schema/limiter checks or API routing.
No PersistentSupervisor, SDK, process, network, FlowAgent or production main.
The callback is invoked on a context-empty thread to reproduce the supervisor
context boundary, but NOT its lock nesting or transport lifecycle.
"""
import queue
import threading
import time
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import Context

from autopilot.context import CURRENT
from external_mcp.limits import ExternalError
from tasks.approval import ApprovalService
from tasks.manager import EXTERNAL_KIND, TaskManager
from tasks.models import TaskStatus
from tasks.session_controller import SessionController
from tasks.session_store import SessionStore
from tasks.store import TaskStore
from tasks.volatile_results import VolatileResult

SCHEMA_SHA = 'a' * 64
TOOL = 'fake_operation'
WAIT_S = 5.0


def wait(event, label):
    assert event.wait(WAIT_S), label + ' did not reach its barrier'


def until(predicate, label):
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.005)
    assert predicate(), label + ' did not reach its final state'


class Scope:
    """Observe explicit recheck/dispatch/uncertain without granting delegation."""
    def __init__(self, refuse=False):
        self.refuse = refuse
        self.dispatched = False
        self.uncertain = False
        self.checks = []

    def recheck(self):
        assert CURRENT.get() is self, 'Wrong per-operation ContextVar scope'
        self.checks.append(threading.get_ident())
        if self.refuse:
            raise ExternalError('trust_revoked')


class Attempt:
    """One real executor stays alive until the test releases preparation/call.

    Commands execute on its worker, not on the pytest thread. Future propagates
    helper/assertion errors instead of losing them in confidential task output.
    The idle queue has a finite safety bound; tests control progress explicitly.
    """
    def __init__(self, rig, *, approved=True):
        self.rig = rig
        self.commands = queue.Queue()
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.event = None
        self.trace = []
        self.calls = []
        self.barriers = []
        fields = {'kind': EXTERNAL_KIND, 'sensitive': True, 'offer_trust': False,
                  'title': 'Local admission fake', 'timeout': 30}
        self.task = rig.manager.submit(fields, self.execute, approved)
        if approved:
            rig.approval.resolve(self.task.id, 'allow')
        wait(self.entered, 'executor start')
        assert rig.manager.get(self.task.id).status == TaskStatus.RUNNING
        rig.attempts.append(self)

    def execute(self, event):
        self.event = event
        self.entered.set()
        try:
            while True:
                command = self.commands.get(timeout=30)
                if command is None:
                    break
                function, future = command
                try:
                    value = function(event)
                except BaseException as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(value)
            assert not event.is_set(), 'Pause/end must not cancel started executor'
            return VolatileResult({'content': 'local fake completed'})
        finally:
            self.finished.set()

    def command(self, function):
        future = Future()
        self.commands.put((function, future))
        return future

    def run(self, function):
        return self.command(function).result(timeout=WAIT_S)

    def barrier(self):
        release = threading.Event()
        self.barriers.append(release)
        return release

    def finish(self):
        self.commands.put(None)
        assert self.rig.manager.wait(self.task.id, WAIT_S).status == TaskStatus.DONE
        wait(self.finished, 'executor finish')


class Rig:
    def __init__(self, path):
        path.mkdir(parents=True, exist_ok=True)
        self.sessions = SessionStore(path / 'sessions.db')
        self.tasks = TaskStore(str(path / 'tasks.db'))
        self.session = self.sessions.create(['sklr'])
        self.controller = SessionController(self.sessions)
        self.controller.bind(self.session.id, self.session.revision)
        self.approval = ApprovalService()
        self.manager = TaskManager(self.tasks, self.approval, max_parallel=3,
                                   session_controller=self.controller)
        self.attempts = []
        self.threads = []

    def start(self, *, approved=True):
        return Attempt(self, approved=approved)

    def pause(self):
        state = self.controller.snapshot()
        return self.controller.request_pause(state['revision'])

    def end(self):
        state = self.controller.snapshot()
        return self.manager.end_session(state['session_id'], state['revision'])

    def drained(self):
        until(lambda: self.controller.snapshot()['running_actions'] == 0,
              'action tokens released')

    def thread(self, function):
        future = Future()
        def run():
            try:
                future.set_result(function())
            except BaseException as exc:
                future.set_exception(exc)
        thread = threading.Thread(target=lambda: Context().run(run), daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread, future

    def join(self, thread, future):
        thread.join(timeout=WAIT_S)
        assert not thread.is_alive(), 'Control/admission thread did not finish'
        return future.result(timeout=0)

    def close(self):
        for attempt in self.attempts:
            for release in attempt.barriers:
                release.set()
            attempt.commands.put(None)
        self.manager.shutdown()
        for thread in self.threads:
            thread.join(timeout=6)
        for worker in self.manager._workers:
            worker.join(timeout=6)
        live = (any(thread.is_alive() for thread in self.threads)
                or any(worker.is_alive() for worker in self.manager._workers))
        # Never close a store beneath an executor/control thread that survived.
        assert not live, 'Admission rig teardown left a live thread'
        self.tasks.close()
        self.sessions.close()


@contextmanager
def rig_at(path):
    rig = Rig(path)
    try:
        yield rig
    finally:
        rig.close()
