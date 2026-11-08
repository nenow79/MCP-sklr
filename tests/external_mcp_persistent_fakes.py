"""MCP-P2-S1 test seams; no network, subprocess, SDK session or production code.

Proposed internal API for external_mcp.persistent_supervisor.PersistentSupervisor:
  constructor(transport, session_factory, validate_arguments, clock)
  fetch_catalog(profile, *, owner, timeout=60, cancel_event=None, before_start=None)
  call_tool(profile, name, arguments, *, owner, expected_runtime_generation,
            timeout=60, cancel_event=None, before_dispatch=None)
  snapshot(*, owner, profile_id) -> {state, runtime_generation, reason?}
  invalidate(*, owner, profile_id, reason)  # closes admission, schedules drain/cleanup
  reap_idle()  # synchronous acknowledgement of a sweep, not an idle refresh
  shutdown()  # bounded cleanup and thread join

Discovery/call results use internal runtime_generation alongside tools/result.
owner is a server-provided opaque WorkSession identity, never a model argument.
The injected clock has monotonic() and async sleep(); all runtime deadlines use it.
Fake Session contexts fail on EOF even when idle, like an owned SDK task group.
This suite does NOT verify actual SDK/TaskManager/API, OS containment or authority
of the supplied owner/guards. Those require separate P3c/P4 suites.
"""
import asyncio
import importlib
import threading
import time
from concurrent.futures import Future
from contextlib import asynccontextmanager
from contextvars import ContextVar
from types import SimpleNamespace

import anyio
import pytest

from external_mcp.limits import ExternalError
from external_mcp.profiles import validate_profile
from external_mcp.schema_worker import validate_payload

SCHEMA = {'type': 'object', 'properties': {'q': {'type': 'string'}}}
TOOL = {'name': 'lookup', 'description': 'fake', 'inputSchema': SCHEMA}
_BACKEND = ContextVar('persistent_fake_backend', default=None)


def identity():
    return (threading.get_ident(), id(asyncio.get_running_loop()),
            anyio.get_current_task().id)


def wait(event, label='barrier'):
    assert event.wait(2), f'{label} was not reached within the test safety bound'


class Clock:
    """Virtual runtime time; short real checkpoints only schedule test threads."""
    def __init__(self):
        self._now = 0.0
        self._lock = threading.Lock()
        self.sleeps = []

    def monotonic(self):
        with self._lock:
            return self._now

    def advance(self, seconds):
        assert seconds >= 0
        with self._lock:
            self._now += seconds

    async def sleep(self, seconds):
        deadline = self.monotonic() + seconds
        with self._lock:
            self.sleeps.append((self._now, seconds))
        while self.monotonic() < deadline:
            await anyio.sleep(0.001)


class Gate:
    def __init__(self, blocked=False):
        self.entered = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()

    async def run(self):
        self.entered.set()
        while not self.release.is_set():
            await anyio.sleep(0.001)


class Backend:
    def __init__(self):
        self.start = Gate()
        self.transport_start = Gate()
        self.session_start = Gate()
        self.catalog = Gate()
        self.validation = Gate()
        self.call = Gate()
        self.cleanup = Gate()
        self.eof = threading.Event()
        self.opened = threading.Event()
        self.closed = threading.Event()
        self.trace = []
        self.calls = []
        self.tokens = set()
        self.call_error = None
        self.cleanup_error = None
        self.business_error = False
        self.cleanup_delays = ()
        self.cleanup_budgets = []
        self.active = self.maximum_active = 0
        self.lists = self.initializes = self.cleanup_attempts = 0

    def record(self, name):
        self.trace.append((name, identity()))

    @asynccontextmanager
    async def operation(self, phase):
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.record(phase + '_enter')
        try:
            yield
        finally:
            self.record(phase + '_exit')
            self.active -= 1


class Hub:
    def __init__(self, clock):
        self.clock = clock
        self.backends = []
        self.prepared = []
        self.runtimes = []
        self._lock = threading.Lock()

    def prepare(self, **attributes):
        backend = Backend()
        for name, value in attributes.items():
            setattr(backend, name, value)
        with self._lock:
            self.prepared.append(backend)
        return backend

    @asynccontextmanager
    async def transport(self, runtime, blocked_ports=()):
        with self._lock:
            backend = self.prepared.pop(0) if self.prepared else Backend()
            self.backends.append(backend)
            self.runtimes.append(runtime)
        backend.record('transport_enter')
        backend.opened.set()
        try:
            # A resource exists before __aenter__ returns. The context manager
            # itself must unwind partial entry; its caller cannot call __aexit__
            # for an __aenter__ that never completed.
            await backend.transport_start.run()
            yield backend, backend
        finally:
            backend.cleanup_attempts += 1
            backend.record('transport_cleanup')
            try:
                await backend.cleanup.run()
                # Simulate several teardown stages. A supervisor must bound their
                # combined duration, not give each stage a new 5-second window.
                for delay in backend.cleanup_delays:
                    backend.cleanup_budgets.append(self.clock.monotonic())
                    await self.clock.sleep(delay)
                if backend.cleanup_error:
                    raise backend.cleanup_error
                backend.closed.set()
            finally:
                backend.record('transport_exit')

    def session_factory(self, streams, timeout):
        return Session(streams[0])

    async def validate(self, schema, arguments):
        # Test-only session identity, inherited by work performed under its
        # owner task. This is NOT authorization or captured autopilot state.
        backend = _BACKEND.get()
        assert backend in self.backends, 'validation ran outside its session owner'
        await backend.validation.run()
        code = validate_payload(schema, arguments)
        if code != 'ok':
            raise ExternalError(code)


class Session:
    def __init__(self, backend):
        self.backend = backend
        self.group = None
        self.context_token = None

    async def __aenter__(self):
        self.backend.record('session_enter')
        self.context_token = _BACKEND.set(self.backend)
        self.group = anyio.create_task_group()
        await self.group.__aenter__()
        self.group.start_soon(self._watch_eof)
        try:
            await self.backend.session_start.run()
        except BaseException:
            # Unwind the task group in its entering task, before re-raising the
            # original cancellation. No new scope may surround group __aexit__.
            await self.__aexit__(None, None, None)
            raise
        return self

    async def _watch_eof(self):
        while not self.backend.eof.is_set():
            await anyio.sleep(0.001)
        raise ExternalError('stdio_read_failed')

    async def __aexit__(self, *exc):
        self.backend.record('session_exit')
        self.group.cancel_scope.cancel()
        try:
            return await self.group.__aexit__(*exc)
        finally:
            _BACKEND.reset(self.context_token)

    async def initialize(self):
        self.backend.initializes += 1
        self.backend.record('initialize')
        await self.backend.start.run()

    async def list_tools(self, params=None):
        async with self.backend.operation('catalog'):
            self.backend.lists += 1
            await self.backend.catalog.run()
            return SimpleNamespace(tools=[TOOL], next_cursor=None)

    async def call_tool(self, name, arguments, read_timeout_seconds=None):
        backend = self.backend
        async with backend.operation('call'):
            backend.calls.append((name, dict(arguments), read_timeout_seconds))
            await backend.call.run()
            if backend.call_error:
                raise backend.call_error
            if arguments.get('q') == 'preview':
                backend.tokens.add('fake-preview')
                text = 'fake-preview'
            elif arguments.get('q') == 'apply':
                if 'fake-preview' not in backend.tokens:
                    return {'content': [], 'isError': True}
                backend.tokens.remove('fake-preview')
                text = 'applied'
            else:
                text = 'ok'
            return {'content': [{'type': 'text', 'text': text}],
                    'structuredContent': {'value': text},
                    'isError': backend.business_error}


class Invocation:
    def __init__(self, operation):
        self.future = Future()
        self.started = threading.Event()
        def run():
            self.started.set()
            try:
                self.future.set_result(operation())
            except BaseException as exc:
                self.future.set_exception(exc)
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        wait(self.started, 'executor thread')

    def result(self):
        return self.future.result(timeout=2)


class Rig:
    def __init__(self, supervisor_type):
        self.clock = Clock()
        self.hub = Hub(self.clock)
        self.supervisor = supervisor_type(
            transport=self.hub.transport, session_factory=self.hub.session_factory,
            validate_arguments=self.hub.validate, clock=self.clock)
        self.invocations = []
        self.owner = 'work-session-A'
        self.profile = validate_profile({
            'alias': 'fake', 'transport': 'stdio', 'command': 'fake-not-executed',
            'stdio_mode': 'persistent', 'enabled': True})
        self.profile.update(id='11111111-1111-4111-8111-111111111111',
                            revision='22222222-2222-4222-8222-222222222222')

    def launch(self, operation):
        task = Invocation(operation)
        self.invocations.append(task)
        return task

    def discover(self, *, owner=None, profile=None, **kwargs):
        return self.supervisor.fetch_catalog(
            profile or self.profile, owner=owner or self.owner, **kwargs)

    def call(self, generation, *, owner=None, arguments=None, **kwargs):
        return self.supervisor.call_tool(
            self.profile, 'lookup', arguments or {}, owner=owner or self.owner,
            expected_runtime_generation=generation, **kwargs)

    def snapshot(self, *, owner=None, profile=None):
        return self.supervisor.snapshot(
            owner=owner or self.owner, profile_id=(profile or self.profile)['id'])

    def invalidate(self, reason='pause'):
        self.supervisor.invalidate(owner=self.owner, profile_id=self.profile['id'],
                                   reason=reason)

    def eventually(self, predicate, label='state transition'):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.001)
        assert predicate(), f'{label} did not complete'

    def tick(self, seconds):
        # Small virtual increments give asynchronous deadline watchers a chance
        # to arm before the next increment, without waiting real contract seconds.
        steps = max(1, int(seconds * 20))
        for _ in range(steps):
            self.clock.advance(seconds / steps)
            time.sleep(0.001)

    def finish(self):
        for backend in self.hub.backends:
            for gate in (backend.start, backend.transport_start,
                         backend.session_start, backend.catalog,
                         backend.validation, backend.call, backend.cleanup):
                gate.release.set()
        task = self.launch(self.supervisor.shutdown)
        self.tick(6)
        task.result()
        for invocation in self.invocations:
            invocation.thread.join(timeout=2)
            assert not invocation.thread.is_alive(), 'executor thread leaked'


@pytest.fixture
def rig():
    # Import at setup, not collection: absent runtime gives 30 explicit failures.
    try:
        module = importlib.import_module('external_mcp.persistent_supervisor')
    except ModuleNotFoundError as exc:
        if exc.name != 'external_mcp.persistent_supervisor':
            raise
        pytest.fail('MCP-P2-S1 RED: persistent supervisor is not implemented',
                    pytrace=False)
    supervisor_type = getattr(module, 'PersistentSupervisor', None)
    if supervisor_type is None:
        pytest.fail('MCP-P2-S1 RED: PersistentSupervisor API is missing', pytrace=False)
    instance = Rig(supervisor_type)
    try:
        yield instance
    finally:
        instance.finish()
