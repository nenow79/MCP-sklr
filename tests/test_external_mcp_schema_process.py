"""Real schema worker acceptance. USER runs it; starts local Python processes.

No network/server. Temporary wrapper records only interpreter PID/readiness, never
schemas/arguments. The wrapper invokes the real worker implementation and keeps
its stdin protocol. Tracks both the Windows venv launcher and real interpreter.
Forced cleanup in teardown is a test safeguard, not evidence of production cleanup.
"""
import os
import signal
import subprocess
import textwrap
import time

import anyio
import pytest

from external_mcp import schema_validation as validation
from external_mcp.limits import ExternalError


def alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 5:
                raise AssertionError('Cannot inspect worker: access denied')
            return False
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise AssertionError('Cannot inspect worker exit state')
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        raise AssertionError('Cannot inspect worker: access denied') from None
    return True


def force_kill(pid):
    if not alive(pid):
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        os.kill(pid, signal.SIGKILL)


@pytest.fixture
def worker(tmp_path, monkeypatch):
    actual = validation.WORKER
    pid_file = tmp_path / 'worker.pid'
    ready_file = tmp_path / 'validation.ready'
    wrapper = tmp_path / 'schema acceptance wrapper.py'
    wrapper.write_text(textwrap.dedent(f'''
        import os
        import runpy
        from pathlib import Path
        Path({str(pid_file)!r}).write_text(str(os.getpid()), encoding='ascii')
        namespace = runpy.run_path({str(actual)!r}, run_name='schema_acceptance_worker')
        # Import first so readiness distinguishes validation from cold startup.
        import jsonschema
        import referencing
        original = namespace['validate_payload']
        def marked(schema, arguments):
            Path({str(ready_file)!r}).write_text('ready', encoding='ascii')
            return original(schema, arguments)
        namespace['main'].__globals__['validate_payload'] = marked
        namespace['main']()
    '''), encoding='utf-8')
    processes = []
    real_open = validation.anyio.open_process
    async def capture(*args, **kwargs):
        process = await real_open(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(validation, 'WORKER', wrapper)
    monkeypatch.setattr(validation.anyio, 'open_process', capture)
    yield pid_file, ready_file, processes
    # Only after assertions: this must not hide failed production cleanup.
    if pid_file.exists():
        force_kill(int(pid_file.read_text(encoding='ascii')))
    for process in processes:
        if process.returncode is None:
            force_kill(process.pid)


def assert_gone(worker):
    pid_file, ready_file, processes = worker
    assert pid_file.exists(), 'real interpreter did not record its PID'
    assert ready_file.exists(), 'worker never entered validation after imports'
    assert processes, 'launcher was not observed'
    pid = int(pid_file.read_text(encoding='ascii'))
    deadline = time.monotonic() + 5
    pids = {pid, *(process.pid for process in processes)}
    while time.monotonic() < deadline:
        if all(not alive(value) for value in pids):
            break
        time.sleep(0.05)
    assert all(not alive(value) for value in pids), 'worker or venv launcher survived cleanup'
    assert all(process.returncode is not None for process in processes)


@pytest.mark.parametrize('arguments, expected', [
    ({'q': 'żółć'}, None), ({'q': 123}, 'arguments_schema'), ({}, 'arguments_schema'),
])
def test_real_worker_local_ref_and_argument_validation(worker, arguments, expected):
    schema = {'type': 'object', 'required': ['q'], '$defs': {'text': {'type': 'string'}},
              'properties': {'q': {'$ref': '#/$defs/text'}}}
    if expected is None:
        anyio.run(validation.validate_arguments, schema, arguments)
    else:
        with pytest.raises(ExternalError) as raised:
            anyio.run(validation.validate_arguments, schema, arguments)
        assert raised.value.code == expected
    assert_gone(worker)


REGEX_SCHEMA = {'type': 'object', 'required': ['q'],
                'properties': {'q': {'type': 'string', 'pattern': '^(a+)+$'}}}
REGEX_ARGUMENTS = {'q': 'a' * 256 + '!'}


def test_real_costly_regex_timeout_kills_worker(worker):
    assert validation.VALIDATION_TIMEOUT_S == 7
    started = time.monotonic()
    with pytest.raises(ExternalError) as raised:
        anyio.run(validation.validate_arguments, REGEX_SCHEMA, REGEX_ARGUMENTS)
    elapsed = time.monotonic() - started
    assert raised.value.code == 'schema_validation_timeout'
    # Allow separate bounded cleanup and scheduler variance, not another full run.
    assert 6.5 <= elapsed < 15, f'unexpected elapsed time: {elapsed:.2f}s'
    assert_gone(worker)


def test_real_costly_regex_cancel_kills_worker(worker):
    _, ready_file, _ = worker
    reached = []
    async def use():
        async with anyio.create_task_group() as group:
            async def run():
                await validation.validate_arguments(REGEX_SCHEMA, REGEX_ARGUMENTS)
            group.start_soon(run)
            with anyio.fail_after(6):
                while not ready_file.exists():
                    await anyio.sleep(0.02)
            reached.append(True)
            # Give worker time to enter its non-cooperative regex.
            await anyio.sleep(0.1)
            group.cancel_scope.cancel()
    anyio.run(use)
    assert reached == [True]
    assert_gone(worker)
