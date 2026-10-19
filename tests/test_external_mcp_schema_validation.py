"""Launcher tests with fake processes: no subprocess/DNS/network is started."""
import json
import subprocess
import sys

import anyio
import pytest

from external_mcp import schema_validation as validation
from external_mcp.limits import ExternalError


class Input:
    def __init__(self):
        self.data = b''
        self.closed = False

    async def send(self, value):
        self.data += value

    async def aclose(self):
        self.closed = True


class Output:
    def __init__(self, data):
        self.data = data
        self.hang = False

    async def receive(self, limit):
        if self.hang:
            await anyio.sleep_forever()
        if not self.data:
            raise anyio.EndOfStream
        value, self.data = self.data[:limit], self.data[limit:]
        return value


class Process:
    pid = 123
    def __init__(self, output=b'ok\n', returncode=0):
        self.stdin, self.stdout = Input(), Output(output)
        self.returncode = returncode
        self.killed = self.closed = False

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode

    async def aclose(self):
        self.closed = True


class Containment:
    spawn_kwargs = {'start_new_session': True}
    def __init__(self):
        self.attached = None
        self.killed = self.closed = False

    def attach(self, pid):
        self.attached = pid

    def kill_tree(self):
        self.killed = True

    def close(self):
        self.closed = True


@pytest.fixture
def launcher(monkeypatch):
    process, containment, calls = Process(), Containment(), []
    async def spawn(command, **kwargs):
        calls.append((command, kwargs))
        return process
    monkeypatch.setattr(validation.anyio, 'open_process', spawn)
    monkeypatch.setattr(validation, 'create_containment', lambda: containment)
    return process, containment, calls


def test_fixed_command_minimal_env_and_payload_only_in_pipe(launcher, monkeypatch):
    process, containment, calls = launcher
    monkeypatch.setenv('PRIVATE_SCHEMA_SECRET', 'do-not-copy')
    monkeypatch.setenv('PYTHONPATH', 'do-not-copy')
    arguments = {'q': 'żółć PRIVATE_ARGUMENT'}
    anyio.run(validation.validate_arguments, {'type': 'object'}, arguments)
    command, options = calls[0]
    assert command == [sys.executable, '-I', str(validation.WORKER)]
    assert 'PRIVATE_ARGUMENT' not in repr(command)
    assert 'PRIVATE_SCHEMA_SECRET' not in options['env']
    assert 'PYTHONPATH' not in options['env']
    assert options['stderr'] == subprocess.DEVNULL
    assert json.loads(process.stdin.data)['arguments'] == arguments
    assert process.stdin.closed and process.closed
    assert containment.attached == process.pid and containment.closed
    assert validation.VALIDATION_TIMEOUT_S == 7


@pytest.mark.parametrize('output, expected', [
    (b'arguments_schema\n', 'arguments_schema'),
    (b'schema_reference_blocked\n', 'schema_reference_blocked'),
    (b'PRIVATE_EXCEPTION\n', 'schema_validation_failed'),
    (b'ok', 'schema_validation_failed'),
    (b'x' * 129, 'schema_validation_failed'),
    (b'\xff', 'schema_validation_failed'),
])
def test_worker_output_allowlist(launcher, output, expected):
    process, containment, _ = launcher
    process.stdout.data = output
    with pytest.raises(ExternalError) as raised:
        anyio.run(validation.validate_arguments, {}, {})
    assert raised.value.code == expected
    assert 'PRIVATE' not in str(raised.value)
    assert process.closed and containment.closed


def test_timeout_kills_before_cleanup(launcher, monkeypatch):
    process, containment, _ = launcher
    process.returncode = None
    process.stdout.hang = True
    monkeypatch.setattr(validation, 'VALIDATION_TIMEOUT_S', 0.02)
    with pytest.raises(ExternalError, match='schema_validation_timeout'):
        anyio.run(validation.validate_arguments, {}, {})
    assert process.killed and containment.killed
    assert process.closed and containment.closed


def test_cancellation_kills_and_propagates(launcher):
    process, containment, _ = launcher
    process.returncode = None
    process.stdout.hang = True
    async def use():
        with anyio.move_on_after(0.02) as scope:
            await validation.validate_arguments({}, {})
        assert scope.cancel_called
    anyio.run(use)
    assert process.killed and process.closed and containment.closed


def test_invalid_arguments_do_not_spawn(launcher):
    _, _, calls = launcher
    with pytest.raises(ExternalError):
        anyio.run(validation.validate_arguments, {}, [])
    assert calls == []


def test_attachment_failure_is_closed_and_safe(launcher, monkeypatch):
    process, containment, _ = launcher
    process.returncode = None
    def fail(pid):
        raise RuntimeError('PRIVATE_ATTACH')
    monkeypatch.setattr(containment, 'attach', fail)
    with pytest.raises(ExternalError, match='schema_validation_failed') as raised:
        anyio.run(validation.validate_arguments, {}, {})
    assert 'PRIVATE_ATTACH' not in str(raised.value)
    assert process.killed and process.closed and containment.closed
