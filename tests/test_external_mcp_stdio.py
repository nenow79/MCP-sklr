"""Stdio framing/cleanup/env tests; no real server or subprocess is launched."""
import subprocess

import anyio
import pytest

from external_mcp.limits import ExternalError
from external_mcp.runtime_config import RuntimeConfig
from external_mcp.process_containment import NullContainment
from external_mcp.stdio_transport import LineFramer, bounded_stdio_client, stop_process


@pytest.fixture(autouse=True)
def no_real_containment(monkeypatch):
    # These tests use fake processes; real Job Objects/groups are tested elsewhere.
    monkeypatch.setattr('external_mcp.stdio_transport.create_containment', NullContainment)


def test_framing_split_unicode_crlf_and_multiple_messages():
    framer = LineFramer(30)
    raw = '{"text":"ą"}\r\n'.encode('utf-8')
    assert framer.feed(raw[:10]) == []
    assert framer.feed(raw[10:] + b'{}\n\n') == [raw[:-2], b'{}']
    framer.finish()


def test_exact_limit_and_oversized_unterminated_message():
    framer = LineFramer(4)
    assert framer.feed(b'1234\n') == [b'1234']
    assert framer.feed(b'123') == []
    with pytest.raises(ExternalError, match='message_limit'):
        framer.feed(b'45')


def test_eof_with_unterminated_json_is_error():
    framer = LineFramer()
    framer.feed(b'{}')
    with pytest.raises(ExternalError, match='stdio_incomplete_message'):
        framer.finish()


class Pipe:
    def __init__(self):
        self.closed = False
    async def aclose(self):
        self.closed = True
    async def receive(self, count):
        await anyio.sleep_forever()
    async def send(self, data):
        pass


class Process:
    pid = 4242

    def __init__(self, graceful=True, survive=False):
        self.stdin, self.stdout = Pipe(), Pipe()
        self.returncode = None
        self.graceful, self.survive = graceful, survive
        self.actions = []
    async def wait(self):
        if self.graceful or (self.actions and not self.survive):
            self.returncode = 0
            return 0
        await anyio.sleep_forever()
    def terminate(self):
        self.actions.append('terminate')
    def kill(self):
        self.actions.append('kill')
    async def aclose(self):
        self.actions.append('close')


def test_cleanup_graceful():
    process = Process()
    anyio.run(stop_process, process)
    assert process.stdin.closed and process.returncode == 0
    assert process.actions == ['close']


def test_cleanup_terminate_and_survivor(monkeypatch):
    monkeypatch.setattr('external_mcp.stdio_transport.CLEANUP_TIMEOUT_S', 0.03)
    process = Process(graceful=False)
    anyio.run(stop_process, process)
    assert process.actions == ['terminate', 'close']
    survivor = Process(graceful=False, survive=True)
    with pytest.raises(ExternalError, match='stdio_cleanup_failed'):
        anyio.run(stop_process, survivor)
    assert survivor.actions == ['terminate', 'kill']


def test_exact_environment_and_no_stderr_logging(monkeypatch):
    recorded = {}
    process = Process()
    async def fake_open(command, **kwargs):
        recorded.update(command=command, **kwargs)
        return process
    monkeypatch.setattr(anyio, 'open_process', fake_open)
    runtime = RuntimeConfig(transport='stdio', command='python', args=['with spaces'],
                            env={'PATH': 'allowed', 'API_TOKEN': 'private'})
    async def use():
        async with bounded_stdio_client(runtime):
            pass
    anyio.run(use)
    assert recorded['command'] == ['python', 'with spaces']
    assert recorded['env'] == runtime.env
    assert recorded['env'] is not runtime.env
    assert recorded['stderr'] == subprocess.DEVNULL
    assert process.returncode == 0


def test_spawn_errors_are_sanitized(monkeypatch):
    async def fail(*args, **kwargs):
        raise OSError('PRIVATE_VALUE')
    monkeypatch.setattr(anyio, 'open_process', fail)
    async def use():
        async with bounded_stdio_client(RuntimeConfig(transport='stdio', command='python')):
            pass
    with pytest.raises(ExternalError) as raised:
        anyio.run(use)
    assert 'PRIVATE_VALUE' not in str(raised.value)


def test_cancellation_stops_direct_process(monkeypatch):
    process = Process()
    async def fake_open(*args, **kwargs):
        return process
    monkeypatch.setattr(anyio, 'open_process', fake_open)
    async def use():
        with anyio.move_on_after(0.02) as scope:
            async with bounded_stdio_client(RuntimeConfig(transport='stdio', command='python')):
                await anyio.sleep_forever()
        assert scope.cancelled_caught
    anyio.run(use)
    assert process.stdin.closed and process.returncode == 0
