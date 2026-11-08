"""S3 wire backend: actual SDK + actual stdio, fake process and containment.

No subprocess, network, OS job or schema worker process. Only wire replies and
process I/O are fake. Task/context tracing delegates to real SDK contexts.
"""
import asyncio
import json
import threading
import time

import anyio
import pytest
from mcp import ClientSession

from external_mcp import stdio_transport as stdio
from external_mcp import persistent_supervisor as supervisor_module
from external_mcp.limits import ExternalError
from external_mcp.profiles import validate_profile
from external_mcp.schema_worker import validate_payload
from external_mcp_persistent_fakes import Gate, Invocation, identity, wait

CLEANUP = 0.4


class WireProcess:
    pid = 4242

    def __init__(self):
        self.stdin = self
        self.stdout = self
        self.returncode = None
        self.buffer = bytearray()
        self.responses = asyncio.Queue()
        self.initialize = Gate()
        self.call = Gate()
        self.close_gate = Gate()
        self.request_seen = threading.Event()
        self.requests = []
        self.token = False
        self.closed = threading.Event()
        self.events = []
        self.fragment = b''

    async def send(self, chunk):
        self.buffer.extend(chunk)
        while b'\n' in self.buffer:
            line, _, rest = self.buffer.partition(b'\n')
            self.buffer = bytearray(rest)
            request = json.loads(line)
            self.requests.append(request)
            method = request.get('method')
            if 'id' not in request:
                continue
            if method == 'initialize':
                await self.initialize.run()
                result = {'protocolVersion': request['params']['protocolVersion'],
                          'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'wire-fake', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': [{'name': 'lookup', 'inputSchema': {
                    'type': 'object', 'properties': {'q': {'type': 'string'}}}}]}
            elif method == 'tools/call':
                self.request_seen.set()
                await self.call.run()
                action = request.get('params', {}).get('arguments', {}).get('q')
                if action == 'preview':
                    self.token = True
                    value = 'preview-token'
                elif action == 'apply':
                    value = 'applied' if self.token else 'missing-token'
                    self.token = False
                else:
                    value = 'ok'
                result = {'content': [{'type': 'text', 'text': value}],
                          'isError': value == 'missing-token'}
            else:
                result = {}
            await self.responses.put(json.dumps(
                {'jsonrpc': '2.0', 'id': request['id'], 'result': result}).encode() + b'\n')

    async def receive(self, count):
        if self.fragment:
            chunk, self.fragment = self.fragment[:count], self.fragment[count:]
            return chunk
        chunk = await self.responses.get()
        if chunk is None:
            raise anyio.EndOfStream
        self.fragment = chunk[count:]
        return chunk[:count]

    async def aclose(self):
        self.events.append('aclose')
        await self.close_gate.run()
        # Used both as fake stdin and process handle. Only process wait confirms
        # direct process exit, never the pipe close itself.
        if self.returncode is not None:
            self.closed.set()

    async def wait(self):
        self.returncode = 0
        return 0

    def terminate(self):
        self.events.append('terminate')
        self.returncode = -1

    def kill(self):
        self.events.append('kill')
        self.returncode = -9

    def release(self):
        for gate in (self.initialize, self.call, self.close_gate):
            gate.release.set()


class WireContainment:
    spawn_kwargs = {}

    def __init__(self, process):
        self.process = process
        self.events = []
        self.fail = False

    def attach(self, pid):
        self.events.append('attach')

    def kill_tree(self):
        self.events.append('kill_tree')
        if self.fail:
            raise OSError('PRIVATE_TREE')
        self.process.returncode = -9

    def close(self):
        self.events.append('close')


class WireRig:
    def __init__(self, monkeypatch):
        self.processes = []
        self.containments = []
        self.trace = []
        self.invocations = []
        self.init_blocked = False
        self.session_delay = 0
        self.session_cleanup = threading.Event()
        self.validation = Gate()
        self.owner = 'wire-work-session'
        self.row = validate_profile({'alias': 'wire', 'transport': 'stdio',
                                     'command': 'not-executed', 'enabled': True,
                                     'stdio_mode': 'persistent'})
        self.row.update(id='11111111-1111-4111-8111-111111111111',
                        revision='22222222-2222-4222-8222-222222222222')
        pending = []
        def containment_factory():
            # stdio creates containment before open_process; create fake I/O in
            # the actual supervisor loop, never in the test's synchronous thread.
            process = WireProcess()
            if self.init_blocked:
                process.initialize = Gate(blocked=True)
            containment = WireContainment(process)
            self.processes.append(process)
            self.containments.append(containment)
            pending.append(process)
            return containment
        async def open_process(*args, **kwargs):
            return pending.pop(0)
        monkeypatch.setattr(stdio, 'create_containment', containment_factory)
        monkeypatch.setattr(anyio, 'open_process', open_process)
        monkeypatch.setattr(stdio, 'CLEANUP_TIMEOUT_S', CLEANUP)
        monkeypatch.setattr(supervisor_module, 'CLEANUP_S', CLEANUP)
        rig = self
        class TracedSession(ClientSession):
            async def __aenter__(self):
                rig.trace.append(('session_enter', identity()))
                return await super().__aenter__()
            async def __aexit__(self, *exc):
                rig.trace.append(('session_exit', identity()))
                rig.session_cleanup.set()
                if rig.session_delay:
                    await anyio.sleep(rig.session_delay)
                return await super().__aexit__(*exc)
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def transport(runtime, blocked_ports):
            self.trace.append(('transport_enter', identity()))
            try:
                async with stdio.bounded_stdio_client(runtime) as streams:
                    yield streams
            finally:
                self.trace.append(('transport_exit', identity()))
        async def validate(schema, arguments):
            await self.validation.run()
            code = validate_payload(schema, arguments)
            if code != 'ok':
                raise ExternalError(code)
        self.supervisor = supervisor_module.PersistentSupervisor(
            transport=transport,
            session_factory=lambda streams, timeout: TracedSession(
                streams[0], streams[1], read_timeout_seconds=timeout),
            validate_arguments=validate, environ={})

    def discover(self, **kwargs):
        return self.supervisor.fetch_catalog(self.row, owner=self.owner, **kwargs)

    def call(self, generation, q='ok', **kwargs):
        return self.supervisor.call_tool(self.row, 'lookup', {'q': q}, owner=self.owner,
                                        expected_runtime_generation=generation, **kwargs)

    def launch(self, operation):
        task = Invocation(operation)
        self.invocations.append(task)
        return task

    def snapshot(self):
        return self.supervisor.snapshot(owner=self.owner, profile_id=self.row['id'])

    def invalidate(self):
        self.supervisor.invalidate(owner=self.owner, profile_id=self.row['id'], reason='pause')

    def eof(self):
        process = self.processes[-1]
        asyncio.run_coroutine_threadsafe(process.responses.put(None), self.supervisor._loop).result(2)

    def eventually(self, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        assert predicate(), 'wire state transition did not complete'

    def finish(self):
        self.validation.release.set()
        self.session_delay = 0
        for process in self.processes:
            process.release()
        shutdown = self.launch(self.supervisor.shutdown)
        shutdown.result()
        for task in self.invocations:
            task.thread.join(2)
            assert not task.thread.is_alive(), 'wire executor leaked'
        assert self.supervisor.thread is None or not self.supervisor.thread.is_alive()


@pytest.fixture
def wire(monkeypatch):
    rig = WireRig(monkeypatch)
    try:
        yield rig
    finally:
        rig.finish()
