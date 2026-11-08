"""S4 local JSON-RPC MCP peer and real supervisor/SDK rig.

Nothing starts on import. Only an explicitly executed fixture spawns Python.
Temporary files contain PIDs, counters and barriers, never arguments or tokens.
No network, FlowAgent, API wiring, production profiles or configuration changes.
"""
import concurrent.futures
import ctypes
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from mcp import ClientSession

from external_mcp.persistent_supervisor import PersistentSupervisor
from external_mcp.profiles import validate_profile
from external_mcp.stdio_transport import bounded_stdio_client

SERVER = textwrap.dedent(r'''
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import threading
    import time
    from uuid import uuid4

    folder = Path(sys.argv[1])

    def publish(name, value):
        target = folder / name
        temporary = folder / (name + '.tmp')
        temporary.write_text(str(value), encoding='utf-8')
        os.replace(temporary, target)

    publish('server.pid', os.getpid())
    initialize_count = 0
    call_count = 0
    token = None
    names = ('preview', 'apply', 'status', 'slow', 'spawn_child')
    tools = [{'name': name, 'description': 'Local S4 test tool',
              'inputSchema': {'type': 'object', 'properties':
                  ({'token': {'type': 'string'}} if name == 'apply' else {}),
                  'required': (['token'] if name == 'apply' else []),
                  'additionalProperties': False}}
             for name in names]

    def watch_exit():
        while True:
            if (folder / 'exit.signal').exists():
                os._exit(0)
            time.sleep(0.01)

    threading.Thread(target=watch_exit, daemon=True).start()

    def response(identifier, result):
        print(json.dumps({'jsonrpc': '2.0', 'id': identifier, 'result': result}), flush=True)

    for line in sys.stdin:
        message = json.loads(line)
        if 'id' not in message:
            continue
        method = message['method']
        params = message.get('params', {})
        if method == 'initialize':
            initialize_count += 1
            publish('initialize.count', initialize_count)
            response(message['id'], {
                'protocolVersion': params['protocolVersion'],
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'local-s4', 'version': '1'}})
        elif method == 'tools/list':
            response(message['id'], {'tools': tools})
        elif method == 'ping':
            response(message['id'], {})
        elif method == 'tools/call':
            call_count += 1
            publish('call.count', call_count)
            name = params['name']
            failed = False
            if name == 'preview':
                token = str(uuid4())
                value = token
            elif name == 'apply':
                failed = token is None or params.get('arguments', {}).get('token') != token
                value = 'token-rejected' if failed else 'applied'
                if not failed:
                    token = None
            elif name == 'status':
                value = json.dumps({'pid': os.getpid(), 'initialize': initialize_count,
                                    'calls': call_count})
            elif name == 'slow':
                publish('dispatch.signal', 1)
                time.sleep(60)
                value = 'late'
            elif name == 'spawn_child':
                child_source = (
                    "import os,time; from pathlib import Path; "
                    "p=Path('child.pid'); t=Path('child.pid.tmp'); "
                    "t.write_text(str(os.getpid()), encoding='utf-8'); "
                    "os.replace(t,p); time.sleep(120)")
                child = subprocess.Popen([sys.executable, '-c', child_source],
                    cwd=str(folder), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                publish('child-launcher.pid', child.pid)
                until = time.monotonic() + 5
                while not (folder / 'child.pid').exists():
                    if time.monotonic() >= until:
                        raise RuntimeError('local child startup failed')
                    time.sleep(0.01)
                value = 'spawned'
            else:
                value = 'unknown'
                failed = True
            response(message['id'], {'content': [{'type': 'text', 'text': value}],
                                      'isError': failed})
        else:
            print(json.dumps({'jsonrpc': '2.0', 'id': message['id'],
                              'error': {'code': -32601, 'message': 'Unsupported method'}}),
                  flush=True)
''')


def eventually(predicate, *, seconds=8, label='local S4 barrier'):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), label


def alive(pid):
    if os.name == 'nt':
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.GetExitCodeProcess.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            # Access denied is not proof of termination.
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return not api.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A killed, unreaped descendant is not an executing process (Linux only).
    try:
        from pathlib import Path
        stat = Path('/proc') / str(pid) / 'stat'
        if stat.exists() and stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
            return False
    except (OSError, IndexError):
        pass
    return True


def force_kill(pid):
    if not alive(pid):
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5, check=False)
    else:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


class Invocation:
    def __init__(self, operation):
        self.future = concurrent.futures.Future()

        def run():
            try:
                self.future.set_result(operation())
            except BaseException as exc:
                self.future.set_exception(exc)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def result(self, seconds=20):
        return self.future.result(timeout=seconds)


class SDKRig:
    def __init__(self, folder):
        self.folder = folder
        script = folder / 'persistent server with spaces.py'
        script.write_text(SERVER, encoding='utf-8')
        self.owner = 'local-s4-owner'
        self.row = validate_profile({
            'alias': 'local-s4', 'transport': 'stdio', 'stdio_mode': 'persistent',
            'enabled': True, 'command': sys.executable,
            'args': [str(script), str(folder)], 'cwd': str(folder)})
        self.row.update(id=str(uuid4()), revision=str(uuid4()))
        self.transport_entries = 0
        self.invocations = []

        @asynccontextmanager
        async def transport(runtime, blocked_ports):
            self.transport_entries += 1
            async with bounded_stdio_client(runtime) as streams:
                yield streams

        self.supervisor = PersistentSupervisor(
            transport=transport,
            session_factory=lambda streams, timeout: ClientSession(
                streams[0], streams[1], read_timeout_seconds=timeout))

    def discover(self):
        return self.supervisor.fetch_catalog(self.row, owner=self.owner, timeout=30)

    def call(self, generation, name, arguments=None, **kwargs):
        return self.supervisor.call_tool(
            self.row, name, {} if arguments is None else arguments,
            owner=self.owner, expected_runtime_generation=generation,
            timeout=kwargs.pop('timeout', 30), **kwargs)

    def launch(self, operation):
        invocation = Invocation(operation)
        self.invocations.append(invocation)
        return invocation

    def snapshot(self):
        return self.supervisor.snapshot(owner=self.owner, profile_id=self.row['id'])

    def invalidate(self):
        self.supervisor.invalidate(owner=self.owner, profile_id=self.row['id'], reason='pause')

    def number(self, filename):
        return int((self.folder / filename).read_text(encoding='utf-8').strip())

    def pids(self):
        return [self.number(name) for name in
                ('server.pid', 'child-launcher.pid', 'child.pid')
                if (self.folder / name).exists()]

    def assert_gone(self, pids):
        # This assertion runs before fallback kills in fixture teardown.
        eventually(lambda: all(not alive(pid) for pid in pids),
                   seconds=7, label='process tree survived persistent cleanup')

    def finish(self):
        shutdown = self.launch(self.supervisor.shutdown)
        fallback_used = False
        try:
            try:
                shutdown.result(seconds=15)
            except concurrent.futures.TimeoutError:
                fallback_used = True
                for pid in self.pids():
                    force_kill(pid)
                shutdown.result(seconds=10)
            survivors = [pid for pid in self.pids() if alive(pid)]
            for pid in survivors:
                fallback_used = True
                force_kill(pid)
            for invocation in self.invocations:
                invocation.thread.join(2)
                assert not invocation.thread.is_alive(), 'local S4 invocation leaked'
            thread = self.supervisor.thread
            assert thread is None or not thread.is_alive(), 'local S4 supervisor leaked'
            assert not fallback_used, 'fixture needed fallback kill; cleanup not accepted'
        finally:
            # Safety cleanup is never evidence that production cleanup passed.
            for pid in self.pids():
                force_kill(pid)


def text_of(response):
    assert response['result']['isError'] is False
    return response['result']['content'][0]['text']


@pytest.fixture
def sdk_rig(tmp_path):
    rig = SDKRig(tmp_path)
    try:
        yield rig
    finally:
        rig.finish()
