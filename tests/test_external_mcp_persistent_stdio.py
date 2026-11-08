"""S2b: 12 stdio cleanup/entry tests; no process/network or OS job is created.

Tests exercise existing stdio functions, not a missing adapter import. A short
scaled cleanup budget tests total deadline semantics; independent rescue gates
bound deliberately hanging fakes. Real SDK/session/descendants remain P4.
These tests alone do not test supervisor broken/replacement (covered by S1/S2a).
"""
import subprocess
import time

import anyio
import pytest

from external_mcp import stdio_transport as stdio
from external_mcp.limits import ExternalError
from external_mcp.runtime_config import RuntimeConfig

BUDGET = 0.12
RESCUE = 0.55
TOLERANCE = 0.10


def leaves(exc):
    children = getattr(exc, 'exceptions', None)
    return [exc] if children is None else [leaf for child in children for leaf in leaves(child)]


class Pipe:
    def __init__(self):
        self.close_gate = anyio.Event()
        self.close_gate.set()
        self.closed = False
        self.chunks = None
        self.sent = []

    async def aclose(self):
        await self.close_gate.wait()
        self.closed = True

    async def receive(self, count):
        if self.chunks is not None:
            if self.chunks:
                return self.chunks.pop(0)
            raise anyio.EndOfStream
        await anyio.sleep_forever()

    async def send(self, value):
        self.sent.append(value)


class Process:
    pid = 4242

    def __init__(self, mode='graceful'):
        self.stdin, self.stdout = Pipe(), Pipe()
        self.returncode = None
        self.mode = mode
        self.events = []
        self.close_gate = anyio.Event()
        self.close_gate.set()
        self.rescue = anyio.Event()
        self.close_count = 0

    async def wait(self):
        self.events.append(('wait', time.monotonic()))
        actions = [event[0] for event in self.events]
        if self.mode == 'graceful' or self.mode == 'terminate' and 'terminate' in actions:
            self.returncode = 0
            return 0
        if self.mode == 'kill' and 'kill' in actions:
            self.returncode = 0
            return 0
        await self.rescue.wait()
        # Test safety release does not pretend the survivor was cleaned up.
        return self.returncode

    def terminate(self):
        self.events.append(('terminate', time.monotonic()))

    def kill(self):
        self.events.append(('kill', time.monotonic()))
        if self.mode != 'survivor':
            self.returncode = -9

    async def aclose(self):
        self.close_count += 1
        self.events.append(('close', time.monotonic()))
        await self.close_gate.wait()

    def release(self):
        self.stdin.close_gate.set()
        self.close_gate.set()
        self.rescue.set()


class Containment:
    spawn_kwargs = {'start_new_session': True}

    def __init__(self, process):
        self.process = process
        self.events = []
        self.attach_error = False
        self.kill_error = False

    def attach(self, pid):
        self.events.append('attach')
        if self.attach_error:
            raise OSError('PRIVATE_ATTACH')

    def kill_tree(self):
        self.events.append('kill_tree')
        if self.kill_error:
            raise OSError('PRIVATE_TREE')
        if self.process.mode != 'survivor':
            self.process.returncode = -9

    def close(self):
        self.events.append('close')


@pytest.fixture
def env(monkeypatch):
    # Async primitives are constructed inside each test's anyio.run.
    def make(process):
        containment = Containment(process)
        recorded = []
        async def open_process(command, **kwargs):
            recorded.append((command, kwargs))
            return process
        monkeypatch.setattr(stdio, 'CLEANUP_TIMEOUT_S', BUDGET)
        monkeypatch.setattr(stdio, 'create_containment', lambda: containment)
        monkeypatch.setattr(anyio, 'open_process', open_process)
        return containment, recorded
    return make


async def measured(process, operation):
    """A shielded sibling frees hangs even if tested code cancels its caller."""
    outcome = {}
    async with anyio.create_task_group() as group:
        async def rescue():
            with anyio.CancelScope(shield=True):
                await anyio.sleep(RESCUE)
                process.release()
        group.start_soon(rescue)
        start = time.monotonic()
        try:
            await operation()
        except Exception as exc:
            outcome['errors'] = leaves(exc)
        finally:
            outcome['elapsed'] = time.monotonic() - start
            process.release()
            group.cancel_scope.cancel()
    return outcome


async def use_transport():
    async with stdio.bounded_stdio_client(RuntimeConfig(transport='stdio', command='fake')):
        pass


@pytest.mark.parametrize('hang', ['stdin', 'wait_close', 'process_close', 'survivor'])
def test_stop_process_total_budget_includes_every_stage(env, hang):
    async def run():
        process = Process('kill' if hang == 'wait_close' else
                          'survivor' if hang == 'survivor' else 'graceful')
        env(process)
        if hang == 'stdin':
            process.stdin.close_gate = anyio.Event()
        if hang in ('wait_close', 'process_close'):
            process.close_gate = anyio.Event()
        result = await measured(process, lambda: stdio.stop_process(process))
        assert result['elapsed'] <= BUDGET + TOLERANCE, 'cleanup exceeded one total budget'
        assert result.get('errors'), 'unconfirmed cleanup must not report success'
        assert all(isinstance(error, ExternalError) for error in result['errors'])
        if hang in ('wait_close', 'survivor'):
            actions = [name for name, _ in process.events]
            assert 'terminate' in actions and 'kill' in actions
    anyio.run(run)


def test_transport_finally_never_restarts_full_cleanup_budget(env):
    async def run():
        process = Process('survivor')
        containment, _ = env(process)
        result = await measured(process, use_transport)
        assert result['elapsed'] <= BUDGET + TOLERANCE
        assert [name for name, _ in process.events].count('terminate') <= 1
        assert [name for name, _ in process.events].count('kill') <= 1
        assert containment.events.count('close') == 1
        assert result.get('errors')
    anyio.run(run)


def test_cancel_during_spawn_closes_containment(env, monkeypatch):
    async def run():
        process = Process()
        containment, _ = env(process)
        entered = anyio.Event()
        async def blocked_spawn(*args, **kwargs):
            entered.set()
            await anyio.sleep_forever()
        monkeypatch.setattr(anyio, 'open_process', blocked_spawn)
        with anyio.move_on_after(0.03) as cancel:
            await use_transport()
        assert entered.is_set() and cancel.cancelled_caught
        assert containment.events == ['close']
    anyio.run(run)


def test_attach_failure_cleanup_is_bounded_and_sanitized(env):
    async def run():
        process = Process()
        process.stdin.close_gate = anyio.Event()
        containment, _ = env(process)
        containment.attach_error = True
        result = await measured(process, use_transport)
        assert result['elapsed'] <= BUDGET + TOLERANCE
        assert containment.events.count('close') == 1
        assert 'kill' in [name for name, _ in process.events]
        assert result.get('errors')
        assert all(isinstance(error, ExternalError) for error in result['errors'])
        assert 'PRIVATE' not in repr(result['errors'])
    anyio.run(run)


def test_stream_allocation_failure_unwinds_attached_process(env, monkeypatch):
    async def run():
        process = Process()
        containment, _ = env(process)
        def fail_streams(*args, **kwargs):
            raise RuntimeError('PRIVATE_ALLOCATION')
        # No global task-group fixture uses streams; this patch targets only the
        # transport allocation after process attach and before its main try.
        monkeypatch.setattr(anyio, 'create_memory_object_stream', fail_streams)
        result = await measured(process, use_transport)
        assert process.returncode is not None
        assert containment.events.count('close') == 1
        assert result.get('errors')
        assert all(isinstance(error, ExternalError) for error in result['errors'])
        assert 'PRIVATE' not in repr(result['errors'])
    anyio.run(run)


@pytest.mark.parametrize('wire', ['eof', 'invalid_json'])
def test_idle_receive_failure_reaches_session_and_cleanup(env, wire):
    async def run():
        process = Process()
        process.stdout.chunks = [] if wire == 'eof' else [b'PRIVATE_INVALID_JSON\n']
        containment, _ = env(process)
        async def receive():
            async with stdio.bounded_stdio_client(
                    RuntimeConfig(transport='stdio', command='fake')) as streams:
                with anyio.fail_after(0.2):
                    try:
                        item = await streams[0].receive()
                    except anyio.EndOfStream:
                        assert wire == 'eof'
                    else:
                        assert wire == 'invalid_json' and isinstance(item, ExternalError)
                        assert 'PRIVATE' not in str(item)
        result = await measured(process, receive)
        assert not result.get('errors'), 'session did not receive bounded EOF/protocol indication'
        assert process.returncode is not None
        assert containment.events.count('close') == 1
    anyio.run(run)


def test_spawn_exact_arguments_environment_and_containment_order(env):
    async def run():
        process = Process()
        containment, recorded = env(process)
        runtime = RuntimeConfig(transport='stdio', command='fake executable',
                                args=['entry with spaces.py', '--flag'],
                                cwd=None, env={'PATH': 'fake-path', 'TOKEN': 'PRIVATE_SENTINEL'})
        async with stdio.bounded_stdio_client(runtime):
            assert containment.events == ['attach']
        command, kwargs = recorded[0]
        assert command == ['fake executable', 'entry with spaces.py', '--flag']
        assert kwargs['env'] == runtime.env and kwargs['env'] is not runtime.env
        assert kwargs['stderr'] == subprocess.DEVNULL
        assert kwargs['start_new_session'] is True
        assert 'shell' not in kwargs or kwargs['shell'] is False
        assert containment.events == ['attach', 'kill_tree', 'close']
    anyio.run(run)


def test_tree_cleanup_failure_is_safe_and_close_still_runs(env):
    async def run():
        process = Process()
        containment, _ = env(process)
        containment.kill_error = True
        result = await measured(process, use_transport)
        assert containment.events.count('close') == 1
        assert result.get('errors')
        assert all(isinstance(error, ExternalError) for error in result['errors'])
        assert 'PRIVATE' not in repr(result['errors'])
    anyio.run(run)
