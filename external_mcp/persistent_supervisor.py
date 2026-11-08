"""Persistent runtime supervisor, P3b-S1; NOT wired into the server.

Transport/session factories are required deliberately: the existing one-shot
stdio cleanup has not been accepted as a shared-deadline persistent adapter.
This module alone cannot enable persistent profiles or authorize WorkSessions.
The caller must supply current per-operation authority/dispatch guards; P3c is
responsible for the synchronized manager/controller/store admission boundary.

One lazy asyncio/AnyIO thread; one owner task enters AND exits each transport and
ClientSession. Executors never receive a live session or an async context manager.
There is one mailbox per runtime, not a replay queue. Waiters have bounded slots.
No raw upstream exceptions or payloads are logged, cached on disk or propagated.
"""
import asyncio
import concurrent.futures
import threading
import time
from dataclasses import dataclass, field
from uuid import uuid4

import anyio

from external_mcp.client import _catalog
from external_mcp.limits import ExternalError, arguments_view, result_view, timeout_seconds
from external_mcp.profiles import connection_digest, validate_profile
from external_mcp.runtime_config import prepare_runtime
from external_mcp.schema_validation import validate_arguments as default_validate
from external_mcp.stdio_transport import cleanup_deadline_context

MAX_INSTANCES = 4
START_S = 15.0
SLOT_S = 8.0
IDLE_S = 600.0
CLEANUP_S = 5.0
POLL_S = 0.005


class _Clock:
    monotonic = staticmethod(time.monotonic)

    @staticmethod
    async def sleep(seconds):
        await anyio.sleep(seconds)


def _safe_code(exc, default='client_failed'):
    if isinstance(exc, ExternalError):
        return exc.code
    children = getattr(exc, 'exceptions', ())
    for child in children:
        code = _safe_code(child, default)
        if code != default:
            return code
    return default


@dataclass(repr=False)
class _Request:
    kind: str
    profile: dict
    deadline: float
    cancel: object
    future: object = field(default_factory=concurrent.futures.Future)
    generation: str = None
    name: str = None
    arguments: dict = None
    schema_digest: str = None
    before_start: object = None
    before_dispatch: object = None
    dispatch_admission: object = None
    dispatched: bool = False
    abandoned: bool = False


@dataclass(repr=False)
class _Runtime:
    key: tuple
    digest: str
    state: str = 'starting'
    generation: str = None
    reason: str = ''
    blocked: bool = False
    reserved: bool = True
    request: object = None
    active: object = None
    wake: object = None
    task: object = None
    last_activity: float = 0.0


class _ObservedReadStream:
    """Observe public stream EOF without a competing reader or SDK internals.

    ClientSession owns consumption; this wrapper preserves async context/iterator
    behavior and forwards transport metadata such as last_context unchanged.
    """

    def __init__(self, stream, on_loss):
        self._stream = stream
        self._on_loss = on_loss

    def __getattr__(self, name):
        return getattr(self._stream, name)

    async def __aenter__(self):
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._stream.__aexit__(*exc)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None

    async def receive(self):
        try:
            item = await self._stream.receive()
        except (anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError):
            self._on_loss()
            raise
        if isinstance(item, Exception):
            self._on_loss()
        return item


class PersistentSupervisor:
    """Internal synchronous facade. owner must be pinned by the server, not LLM.

    Factories and callbacks are internal trusted dependencies, never MCP inputs.
    before_start() and before_dispatch(name, schema_digest) must be bounded,
    synchronous, return None, and raise ExternalError on refusal. Per-operation
    contexts must be passed explicitly: the thread starts with no inherited
    ContextVars. Generation is continuity metadata, not authorization.
    """

    def __init__(self, *, transport, session_factory, validate_arguments=None,
                 clock=None, environ=None, blocked_ports=()):
        if not callable(transport) or not callable(session_factory):
            raise ExternalError('persistent_factory')
        self._transport = transport
        self._session_factory = session_factory
        self._validate = validate_arguments or default_validate
        self._clock = clock or _Clock()
        self._environ = environ
        self._blocked_ports = tuple(blocked_ports)
        self._lock = threading.RLock()
        self._runtimes = {}
        self._closed = False
        self.thread = None
        self._loop = None
        self._stop = None
        self._boot = threading.Event()
        self._boot_failed = False

    def _key(self, owner, profile_id):
        if not isinstance(owner, str) or not owner or len(owner) > 256:
            raise ExternalError('persistent_owner')
        return owner, profile_id

    def snapshot(self, *, owner, profile_id):
        key = self._key(owner, profile_id)
        with self._lock:
            runtime = self._runtimes.get(key)
            if runtime is None:
                return {'state': 'absent', 'runtime_generation': None, 'reason': ''}
            return {'state': runtime.state,
                    'runtime_generation': runtime.generation, 'reason': runtime.reason}

    def _start_thread(self):
        with self._lock:
            if self._closed:
                raise ExternalError('persistent_closed')
            if self.thread is None:
                self.thread = threading.Thread(
                    target=self._thread_main, name='external-mcp-supervisor', daemon=True)
                self.thread.start()
        if not self._boot.wait(5) or self._boot_failed:
            raise ExternalError('persistent_supervisor_failed')

    def _thread_main(self):
        # New Thread starts with an empty context also on free-threaded Python.
        from contextvars import Context
        try:
            Context().run(anyio.run, self._main, backend='asyncio')
        except BaseException:
            with self._lock:
                self._boot_failed = True
                self._closed = True
                for runtime in self._runtimes.values():
                    runtime.generation = None
                    runtime.blocked = True
                    runtime.state = 'broken'
                    runtime.reason = 'persistent_supervisor_failed'
                    for request in (runtime.request, runtime.active):
                        self._refuse(request, 'persistent_supervisor_failed')
        finally:
            self._boot.set()

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._boot.set()
        idle = asyncio.create_task(self._idle_watch())
        try:
            await self._stop.wait()
        finally:
            idle.cancel()
            try:
                await idle
            except asyncio.CancelledError:
                pass

    def _checked(self, profile):
        row = validate_profile(profile, stored=True, blocked_ports=self._blocked_ports)
        if row['transport'] != 'stdio' or row.get('stdio_mode') != 'persistent':
            raise ExternalError('persistent_profile')
        if not row.get('enabled'):
            raise ExternalError('profile_disabled')
        return row

    def fetch_catalog(self, profile, *, owner, timeout=60, cancel_event=None,
                      before_start=None):
        row = self._checked(profile)
        self._key(owner, row['id'])
        if before_start is not None and not callable(before_start):
            raise ExternalError('dispatch_guard')
        request = _Request('discovery', row, self._clock.monotonic() + timeout_seconds(timeout),
                           cancel_event, before_start=before_start)
        return self._submit(owner, request, timeout_seconds(timeout))

    def call_tool(self, profile, tool_name, arguments, *, owner,
                  expected_runtime_generation, expected_schema_sha256=None,
                  timeout=60, cancel_event=None, before_dispatch=None,
                  dispatch_admission=None):
        row = self._checked(profile)
        self._key(owner, row['id'])
        if not isinstance(expected_runtime_generation, str) or not expected_runtime_generation:
            raise ExternalError('runtime_generation')
        if not isinstance(tool_name, str) or not tool_name or len(tool_name) > 256:
            raise ExternalError('tool_name')
        if before_dispatch is not None and not callable(before_dispatch):
            raise ExternalError('dispatch_guard')
        if dispatch_admission is not None:
            from external_mcp.persistent_admission import DispatchAdmission
            if before_dispatch is not None or not isinstance(dispatch_admission, DispatchAdmission):
                raise ExternalError('dispatch_guard')
            dispatch_admission.check_binding(owner, cancel_event)
        if expected_schema_sha256 is not None and not (
                isinstance(expected_schema_sha256, str) and len(expected_schema_sha256) == 64):
            raise ExternalError('schema_digest')
        limit = timeout_seconds(timeout)
        request = _Request('call', row, self._clock.monotonic() + limit, cancel_event,
                           generation=expected_runtime_generation, name=tool_name,
                           arguments=arguments_view(arguments), schema_digest=expected_schema_sha256,
                           before_dispatch=before_dispatch, dispatch_admission=dispatch_admission)
        # Absent/foreign/stale calls do not even start a supervisor thread.
        with self._lock:
            runtime = self._runtimes.get((owner, row['id']))
            self._check_runtime(runtime, request)
        return self._submit(owner, request, limit)

    def _submit(self, owner, request, limit):
        self._start_thread()
        # Avoid propagating executor ContextVars through run_coroutine_threadsafe.
        from contextvars import Context
        future = Context().run(asyncio.run_coroutine_threadsafe,
                               self._acquire(owner, request), self._loop)
        safety_deadline = time.monotonic() + limit + CLEANUP_S + 5
        try:
            future.result(timeout=max(0, safety_deadline - time.monotonic()))
            return request.future.result(timeout=max(0, safety_deadline - time.monotonic()))
        except ExternalError:
            raise
        except BaseException:
            # Mark before cancelling the bridge: cancellation can race slot
            # admission. Both acquisition and dispatch check this same flag.
            with self._lock:
                request.abandoned = True
            future.cancel()
            # Safety bound only: never resubmit or replay. Cancellation cannot
            # imply absence of a remote effect after local dispatch admission.
            self.invalidate(owner=owner, profile_id=request.profile['id'],
                            reason='persistent_executor_failed')
            raise ExternalError('call_failed_uncertain' if request.dispatched
                                else 'persistent_executor_failed') from None

    @staticmethod
    def _refuse(request, code):
        if request is not None and not request.future.done():
            if request.dispatched and 'uncertain' not in code:
                code = 'call_failed_uncertain'
            request.future.set_exception(ExternalError(code))

    def _check_runtime(self, runtime, request):
        if self._closed:
            raise ExternalError('persistent_closed')
        if runtime is None or runtime.blocked or runtime.state in ('closing', 'broken'):
            raise ExternalError('runtime_generation')
        if runtime.digest != connection_digest(request.profile):
            raise ExternalError('profile_changed')
        if request.kind == 'call' and runtime.generation != request.generation:
            raise ExternalError('runtime_generation')

    def _check_request(self, request):
        if request.abandoned:
            raise ExternalError('persistent_executor_failed')
        if request.cancel is not None and request.cancel.is_set():
            raise ExternalError('cancelled')
        if self._clock.monotonic() >= request.deadline:
            raise ExternalError('timeout')

    async def _acquire(self, owner, request):
        key = self._key(owner, request.profile['id'])
        slot_deadline = min(request.deadline, self._clock.monotonic() + SLOT_S)
        while True:
            self._check_request(request)
            with self._lock:
                if self._closed:
                    raise ExternalError('persistent_closed')
                self._check_request(request)
                runtime = self._runtimes.get(key)
                if runtime is None:
                    if request.kind != 'discovery':
                        raise ExternalError('runtime_generation')
                    if len(self._runtimes) >= MAX_INSTANCES:
                        raise ExternalError('persistent_instances_limit')
                    runtime = _Runtime(key, connection_digest(request.profile),
                                       request=request, wake=asyncio.Event())
                    self._runtimes[key] = runtime
                    runtime.task = asyncio.create_task(self._owner(runtime))
                    return
                self._check_runtime(runtime, request)
                if not runtime.reserved:
                    runtime.reserved = True
                    runtime.request = request
                    runtime.wake.set()
                    return
            if self._clock.monotonic() >= slot_deadline:
                raise ExternalError('persistent_slot_timeout')
            await anyio.sleep(POLL_S)

    async def _bounded(self, operation, deadline, cancel=None):
        """Execute in the session owner task; watchdog owns no SDK context."""
        outcome = None
        with anyio.CancelScope() as scope:
            async def watch():
                nonlocal outcome
                while True:
                    if cancel is not None and cancel.is_set():
                        outcome = 'cancelled'
                        scope.cancel()
                        return
                    if self._clock.monotonic() >= deadline:
                        outcome = 'timeout'
                        scope.cancel()
                        return
                    await anyio.sleep(POLL_S)
            watcher = asyncio.create_task(watch())
            try:
                if self._clock.monotonic() >= deadline:
                    raise ExternalError('timeout')
                value = await operation()
            finally:
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass
        if outcome is not None:
            raise ExternalError(outcome)
        return value

    async def _perform(self, runtime, session, request):
        with self._lock:
            self._check_runtime(runtime, request)
            self._check_request(request)
            runtime.state = 'busy'
            if request.kind == 'discovery':
                if request.before_start is not None and request.before_start() is not None:
                    raise ExternalError('dispatch_guard')
                request.generation = runtime.generation
        catalog = await _catalog(session)
        if request.kind == 'discovery':
            return {'tools': catalog, 'runtime_generation': request.generation}
        tool = next((item for item in catalog if item['name'] == request.name), None)
        if tool is None:
            raise ExternalError('tool_not_found')
        if request.schema_digest is not None and request.schema_digest != tool['schemaSha256']:
            raise ExternalError('schema_changed')
        await self._validate(tool['inputSchema'], request.arguments)
        if request.dispatch_admission is None:
            # Preserve the existing internal callback contract for legacy users.
            with self._lock:
                self._check_runtime(runtime, request)
                self._check_request(request)
                if request.before_dispatch is not None:
                    if request.before_dispatch(request.name, tool['schemaSha256']) is not None:
                        raise ExternalError('dispatch_guard')
                self._check_runtime(runtime, request)
                self._check_request(request)
                request.dispatched = True
        else:
            admission = request.dispatch_admission
            # Never enter manager/controller while holding the supervisor lock.
            # Scope recheck happens before the boundary acquires control locks.
            with admission.dispatch_boundary(request.name, tool['schemaSha256']) as commit:
                with self._lock:
                    admission.check_binding(runtime.key[0], request.cancel)
                    self._check_runtime(runtime, request)
                    self._check_request(request)
                    commit(lambda: setattr(request, 'dispatched', True))
            # No pre-dispatch checks after commit. A later cancellation/timeout
            # or protocol failure is conservatively uncertain, never retried.
        result = await session.call_tool(
            request.name, request.arguments,
            read_timeout_seconds=max(0.001, request.deadline - self._clock.monotonic()))
        return {'result': result_view(result), 'schemaSha256': tool['schemaSha256'],
                'runtime_generation': request.generation}

    async def _owner(self, runtime):
        """Only this task enters/exits SDK contexts, including failure cleanup."""
        config = transport_cm = session_cm = session = None
        transport_entered = session_entered = False
        cleanup_failed = False
        cleanup_expired = False
        request = runtime.request
        # Outer scope precedes SDK task groups: teardown never exits their scopes
        # from a different task or beneath an incorrectly nested cleanup scope.
        with anyio.CancelScope() as lifetime:
            try:
                with self._lock:
                    self._check_request(request)
                    if runtime.blocked or self._closed:
                        raise ExternalError('runtime_generation')
                    if request.before_start is not None:
                        if request.before_start() is not None:
                            raise ExternalError('dispatch_guard')
                config = prepare_runtime(request.profile, environ=self._environ,
                                         blocked_ports=self._blocked_ports)
                transport_cm = self._transport(config, self._blocked_ports)
                start_deadline = min(request.deadline, self._clock.monotonic() + START_S)

                async def start():
                    nonlocal transport_entered, session_cm, session, session_entered
                    streams = await transport_cm.__aenter__()
                    transport_entered = True
                    if callable(getattr(streams[0], 'receive', None)):
                        def connection_lost():
                            self._mark_closing(runtime, 'persistent_connection_lost')
                            runtime.wake.set()
                        streams = (_ObservedReadStream(streams[0], connection_lost),
                                   *streams[1:])
                    session_cm = self._session_factory(streams, START_S)
                    session = await session_cm.__aenter__()
                    session_entered = True
                    await session.initialize()
                # The start budget must not surround SDK context ENTER in an
                # inner CancelScope that exits before the SDK's own task group.
                start_expired = None
                owner_task = asyncio.current_task()
                async def watch_start():
                    nonlocal start_expired
                    while True:
                        with self._lock:
                            blocked = runtime.blocked or self._closed or request.abandoned
                        if blocked:
                            start_expired = 'runtime_generation'
                        elif request.cancel is not None and request.cancel.is_set():
                            start_expired = 'cancelled'
                        elif self._clock.monotonic() >= start_deadline:
                            start_expired = 'timeout'
                        if start_expired is not None:
                            # One-shot task cancellation interrupts nested SDK
                            # entry/initialize without permanently cancelling the
                            # outer lifetime scope needed for bounded cleanup.
                            owner_task.cancel()
                            return
                        await anyio.sleep(POLL_S)
                starter = asyncio.create_task(watch_start())
                try:
                    await start()
                finally:
                    starter.cancel()
                    if start_expired is not None:
                        # Consume only the cancellation issued by our start
                        # watchdog; do not reset SDK scopes or unrelated cancels.
                        owner_task.uncancel()
                with self._lock:
                    if runtime.blocked or self._closed:
                        raise ExternalError('runtime_generation')
                    runtime.generation = str(uuid4())
                    runtime.state = 'ready'
                while True:
                    with self._lock:
                        request = runtime.request
                        runtime.request = None
                        runtime.active = request
                        blocked = runtime.blocked
                    if request is None:
                        if blocked:
                            break
                        await runtime.wake.wait()
                        runtime.wake.clear()
                        continue
                    fatal = False
                    try:
                        value = await self._bounded(
                            lambda: self._perform(runtime, session, request),
                            request.deadline, request.cancel)
                    except Exception as exc:
                        code = _safe_code(exc)
                        fatal = code in ('timeout', 'cancelled', 'client_failed',
                                         'stdio_read_failed', 'stdio_write_failed')
                        if request.dispatched:
                            code = {'timeout': 'call_timeout_uncertain',
                                    'cancelled': 'call_cancelled_uncertain'}.get(
                                        code, 'call_failed_uncertain')
                            fatal = True
                        self._refuse(request, code)
                    else:
                        if not request.future.done():
                            request.future.set_result(value)
                    finally:
                        with self._lock:
                            runtime.active = None
                            runtime.reserved = False
                            runtime.last_activity = self._clock.monotonic()
                            if not runtime.blocked:
                                runtime.state = 'ready'
                    if fatal:
                        self._mark_closing(runtime, 'persistent_operation_failed')
                    if runtime.blocked:
                        break
            except BaseException as exc:
                # SDK EOF may cancel its owner while idle. Never propagate the
                # raw ExceptionGroup/CancelledError to the thread's event logger.
                code = locals().get('start_expired') or _safe_code(exc)
                self._refuse(request, code)
                self._mark_closing(runtime, code)
            finally:
                self._mark_closing(runtime, runtime.reason or 'persistent_closed')
                lifetime.shield = True
                cleanup_deadline = self._clock.monotonic() + CLEANUP_S
                # Transport uses real monotonic time; the injected clock remains
                # for supervisor logic tests. Never pass a virtual timestamp to
                # the actual stdio adapter. Both start at this teardown boundary.
                cleanup_context_token = cleanup_deadline_context.set(
                    time.monotonic() + CLEANUP_S)
                async def watch_cleanup():
                    nonlocal cleanup_expired
                    while self._clock.monotonic() < cleanup_deadline:
                        await anyio.sleep(POLL_S)
                    cleanup_expired = True
                    lifetime.cancel()
                cleaner = asyncio.create_task(watch_cleanup())
                try:
                    if session_entered:
                        try:
                            await session_cm.__aexit__(None, None, None)
                        except BaseException as exc:
                            # EOF is a protocol failure, not evidence that the
                            # subsequent transport cleanup failed. Other failures
                            # conservatively retain capacity as broken.
                            if _safe_code(exc) != 'stdio_read_failed':
                                cleanup_failed = True
                    if transport_entered:
                        try:
                            await transport_cm.__aexit__(None, None, None)
                        except BaseException:
                            cleanup_failed = True
                finally:
                    cleaner.cancel()
                    cleanup_deadline_context.reset(cleanup_context_token)
                    if config is not None:
                        config.clear()
                    with self._lock:
                        self._refuse(runtime.request, 'runtime_generation')
                        self._refuse(runtime.active, 'persistent_operation_failed')
                        runtime.request = runtime.active = None
                        runtime.reserved = False
                        runtime.generation = None
                        if cleanup_failed or cleanup_expired:
                            runtime.state = 'broken'
                            runtime.reason = 'persistent_cleanup_failed'
                        elif self._runtimes.get(runtime.key) is runtime:
                            del self._runtimes[runtime.key]

    def _mark_closing(self, runtime, reason):
        with self._lock:
            runtime.blocked = True
            runtime.generation = None
            if runtime.state != 'broken':
                runtime.state = 'closing'
            runtime.reason = reason

    def invalidate(self, *, owner, profile_id, reason):
        # Caller-supplied descriptive text is not reflected as an error/log.
        safe_reason = reason if reason in ('pause', 'end', 'idle', 'shutdown',
                                           'profile_changed', 'disabled', 'project_changed') \
            else 'persistent_invalidated'
        with self._lock:
            runtime = self._runtimes.get(self._key(owner, profile_id))
            if runtime is None:
                return
            self._mark_closing(runtime, safe_reason)
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(runtime.wake.set)

    def invalidate_generation(self, *, owner, profile_id,
                              expected_runtime_generation, reason):
        """Leaf lifecycle operation: compare and close under the same lock.

        A delayed event cannot invalidate a replacement process. Never accepts
        a missing generation as a wildcard. Cleanup remains owner-task work.
        """
        if not isinstance(expected_runtime_generation, str) or not expected_runtime_generation:
            raise ExternalError('runtime_generation')
        safe_reason = reason if reason in ('pause', 'end', 'idle', 'shutdown',
            'profile_changed', 'disabled', 'project_changed') else 'persistent_invalidated'
        with self._lock:
            runtime = self._runtimes.get(self._key(owner, profile_id))
            if (runtime is None or runtime.blocked
                    or runtime.generation != expected_runtime_generation):
                return False
            self._mark_closing(runtime, safe_reason)
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(runtime.wake.set)
            return True

    async def _reap(self):
        with self._lock:
            for runtime in list(self._runtimes.values()):
                if (runtime.state == 'ready' and not runtime.reserved and
                        self._clock.monotonic() - runtime.last_activity >= IDLE_S):
                    self._mark_closing(runtime, 'idle')
                    runtime.wake.set()

    async def _idle_watch(self):
        while True:
            await anyio.sleep(POLL_S)
            await self._reap()

    def reap_idle(self):
        with self._lock:
            loop = self._loop
            if loop is None or loop.is_closed() or self._closed:
                return
        try:
            asyncio.run_coroutine_threadsafe(self._reap(), loop).result(timeout=5)
        except BaseException:
            raise ExternalError('persistent_supervisor_failed') from None

    async def _shutdown(self):
        with self._lock:
            runtimes = list(self._runtimes.values())
            for runtime in runtimes:
                self._mark_closing(runtime, 'shutdown')
                runtime.wake.set()
        # Admitted calls drain under their original operation budget, followed by
        # one cleanup budget. No store is involved or closed by this module.
        if runtimes:
            await asyncio.gather(*(runtime.task for runtime in runtimes))
        self._stop.set()

    def shutdown(self):
        with self._lock:
            self._closed = True
            thread, loop = self.thread, self._loop
            if thread is None:
                return
        if thread is threading.current_thread():
            raise ExternalError('persistent_supervisor_thread')
        if not thread.is_alive():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout=310)
            thread.join(timeout=5)
        except BaseException:
            raise ExternalError('persistent_shutdown_failed') from None
        if thread.is_alive():
            raise ExternalError('persistent_shutdown_failed')
