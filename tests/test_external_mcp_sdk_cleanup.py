"""Measures whether server processes are gone after timeout/cancel (real SDK, local server).

The server reports its OWN pid, so a venv launcher that spawns the real interpreter as a
child is detected. Starts child Python processes; no network. The user runs it.
Teardown force-kills any leftover pid so a failing run does not leave orphans.
"""
import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from external_mcp.client import call_tool
from external_mcp.limits import ExternalError
from external_mcp.profiles import validate_profile

SERVER = textwrap.dedent('''
    import os
    import subprocess
    import sys
    import time
    from fastmcp import FastMCP

    pid_file, child_file = sys.argv[1], sys.argv[2]
    with open(pid_file, "w") as handle:
        handle.write(str(os.getpid()))

    mcp = FastMCP("cleanup-test")

    @mcp.tool
    def slow() -> str:
        """Block long enough for timeout or cancel."""
        time.sleep(60)
        return "late"

    @mcp.tool
    def spawn_child() -> str:
        """Start a grandchild that outlives a naive terminate, then block."""
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(child_file, "w") as handle:
            handle.write(str(child.pid))
        time.sleep(60)
        return "late"

    if __name__ == "__main__":
        mcp.run()
''')


def alive(pid):
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # signal 0 only probes on POSIX; never used on Windows
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def force_kill(pid):
    if not alive(pid):
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, check=False)
    else:
        os.kill(pid, 9)


def gone_within(pid, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return not alive(pid)


@pytest.fixture
def server(tmp_path):
    script = tmp_path / 'cleanup server.py'
    script.write_text(SERVER, encoding='utf-8')
    pid_file, child_file = tmp_path / 'server.pid', tmp_path / 'child.pid'
    row = validate_profile({'alias': 'local', 'transport': 'stdio', 'command': sys.executable,
                            'args': [str(script), str(pid_file), str(child_file)],
                            'cwd': str(tmp_path)})
    row.update(id='11111111-1111-4111-8111-111111111111',
               revision='22222222-2222-4222-8222-222222222222')
    yield row, pid_file, child_file
    for path in (pid_file, child_file):
        if path.exists():
            try:
                force_kill(int(path.read_text()))
            except ValueError:
                pass


def read_pid(path):
    return int(path.read_text(encoding='utf-8').strip())


@pytest.mark.parametrize('how', ['timeout', 'cancel'])
def test_server_process_is_gone_after_timeout_or_cancel(server, how):
    row, pid_file, _ = server
    event = threading.Event()
    if how == 'cancel':
        threading.Timer(9, event.set).start()
    with pytest.raises(ExternalError) as raised:
        call_tool(row, 'slow', {}, timeout=10 if how == 'timeout' else 30, cancel_event=event)
    assert raised.value.code in ('call_timeout_uncertain', 'call_cancelled_uncertain',
                                 'timeout', 'cancelled')
    pid = read_pid(pid_file)
    assert gone_within(pid, 5), 'server process still alive after client cleanup'


def test_grandchild_is_gone_after_timeout(server):
    row, _, child_file = server
    with pytest.raises(ExternalError):
        call_tool(row, 'spawn_child', {}, timeout=10)
    assert gone_within(read_pid(child_file), 5)
