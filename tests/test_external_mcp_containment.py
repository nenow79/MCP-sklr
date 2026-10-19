"""Containment wiring with fakes (any OS) plus Windows/POSIX specifics. No real server."""
import os

import anyio
import pytest

from external_mcp import process_containment
from external_mcp.limits import ExternalError
from external_mcp.process_containment import PosixContainment, WindowsContainment
from external_mcp.runtime_config import RuntimeConfig
from external_mcp.stdio_transport import bounded_stdio_client


class Pipe:
    async def aclose(self):
        pass
    async def receive(self, count):
        await anyio.sleep_forever()
    async def send(self, data):
        pass


class Process:
    pid = 4242
    def __init__(self):
        self.stdin, self.stdout = Pipe(), Pipe()
        self.returncode = None
        self.actions = []
    async def wait(self):
        if self.actions:
            self.returncode = 0
        return self.returncode
    def terminate(self):
        self.actions.append('terminate')
    def kill(self):
        self.actions.append('kill')
        self.returncode = 0
    async def aclose(self):
        pass


class FakeContainment:
    def __init__(self, spawn_kwargs=None, attach_error=False):
        self.spawn_kwargs = spawn_kwargs or {}
        self.attach_error = attach_error
        self.events = []
    def attach(self, pid):
        self.events.append(('attach', pid))
        if self.attach_error:
            raise ExternalError('stdio_containment_failed')
    def kill_tree(self):
        self.events.append('kill_tree')
    def close(self):
        self.events.append('close')


def run(monkeypatch, containment, process=None):
    process = process or Process()
    recorded = {}
    async def fake_open(command, **kwargs):
        recorded.update(kwargs)
        return process
    monkeypatch.setattr(anyio, 'open_process', fake_open)
    monkeypatch.setattr('external_mcp.stdio_transport.create_containment', lambda: containment)
    async def use():
        async with bounded_stdio_client(RuntimeConfig(transport='stdio', command='python')):
            pass
    anyio.run(use)
    return recorded, process


def test_spawn_kwargs_forwarded_and_tree_killed_after_normal_exit(monkeypatch):
    containment = FakeContainment({'start_new_session': True})
    recorded, _ = run(monkeypatch, containment)
    assert recorded['start_new_session'] is True
    assert containment.events == [('attach', 4242), 'kill_tree', 'close']


def test_attach_failure_kills_direct_process_and_fails_closed(monkeypatch):
    containment, process = FakeContainment(attach_error=True), Process()
    with pytest.raises(ExternalError) as raised:
        run(monkeypatch, containment, process)
    assert raised.value.code == 'stdio_containment_failed'
    assert 'kill' in process.actions
    assert 'close' in containment.events


def test_spawn_failure_closes_containment(monkeypatch):
    containment = FakeContainment()
    async def fail(*args, **kwargs):
        raise OSError('PRIVATE_VALUE')
    monkeypatch.setattr(anyio, 'open_process', fail)
    monkeypatch.setattr('external_mcp.stdio_transport.create_containment', lambda: containment)
    async def use():
        async with bounded_stdio_client(RuntimeConfig(transport='stdio', command='python')):
            pass
    with pytest.raises(ExternalError) as raised:
        anyio.run(use)
    assert raised.value.code == 'stdio_spawn_failed'
    assert 'PRIVATE_VALUE' not in str(raised.value)
    assert 'close' in containment.events


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process groups')
def test_posix_group_kill(monkeypatch):
    killed = []
    monkeypatch.setattr(os, 'killpg', lambda pgid, sig: killed.append(pgid))
    containment = PosixContainment()
    assert containment.spawn_kwargs == {'start_new_session': True}
    containment.attach(4242)
    containment.kill_tree()
    containment.kill_tree()
    assert killed == [4242]


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process groups')
@pytest.mark.parametrize('pid', [0, 1, -5, True.__class__])
def test_posix_rejects_unsafe_group_ids(pid):
    with pytest.raises(ExternalError):
        PosixContainment().attach(pid)


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object')
def test_windows_job_creates_closes_and_rejects_bad_pid():
    containment = WindowsContainment()
    assert containment.spawn_kwargs == {'creationflags': process_containment.CREATE_SUSPENDED}
    with pytest.raises(ExternalError, match='stdio_containment_failed'):
        containment.attach(0x7FFFFFF0)
    containment.close()
    containment.close()
