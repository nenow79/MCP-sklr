"""Containment of a stdio server process tree (M2c).

Windows: Job Object with KILL_ON_JOB_CLOSE. The child starts SUSPENDED, is assigned to the
job and only then resumed, so no descendant can be created outside the job (a venv launcher
starts the real interpreter as a child within milliseconds). Closing the job handle also
kills the tree if sklr itself dies. Nested jobs need Windows 8+.
POSIX: new session/process group, SIGKILL to the group. Group id reuse after the whole
group has died is theoretically possible and accepted.
This limits lifetime of descendants. It is NOT a sandbox: no filesystem, network or
privilege restriction. Importing this module never creates a job or a process.
"""
import os
import signal

from external_mcp.limits import ExternalError

CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_SUSPEND_RESUME = 0x0800

_API = None


class NullContainment:
    spawn_kwargs = {}

    def attach(self, pid):
        pass

    def kill_tree(self):
        pass

    def close(self):
        pass


class PosixContainment:
    spawn_kwargs = {'start_new_session': True}

    def __init__(self):
        self.pgid = None

    def attach(self, pid):
        if not isinstance(pid, int) or pid <= 1:
            raise ExternalError('stdio_containment_failed')
        self.pgid = pid  # start_new_session makes the child its own group leader

    def kill_tree(self):
        pgid, self.pgid = self.pgid, None
        if pgid is None:
            return
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            raise ExternalError('stdio_containment_failed') from None

    def close(self):
        self.kill_tree()


def _windows_api():
    global _API
    if _API is not None:
        return _API
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    ntdll = ctypes.WinDLL('ntdll')

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ('PerProcessUserTimeLimit', ctypes.c_longlong),
            ('PerJobUserTimeLimit', ctypes.c_longlong),
            ('LimitFlags', wintypes.DWORD),
            ('MinimumWorkingSetSize', ctypes.c_size_t),
            ('MaximumWorkingSetSize', ctypes.c_size_t),
            ('ActiveProcessLimit', wintypes.DWORD),
            ('Affinity', ctypes.c_size_t),
            ('PriorityClass', wintypes.DWORD),
            ('SchedulingClass', wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ('BasicLimitInformation', BasicLimits),
            ('IoInfo', IoCounters),
            ('ProcessMemoryLimit', ctypes.c_size_t),
            ('JobMemoryLimit', ctypes.c_size_t),
            ('PeakProcessMemoryUsed', ctypes.c_size_t),
            ('PeakJobMemoryUsed', ctypes.c_size_t),
        ]

    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    _API = (ctypes, kernel32, ntdll, ExtendedLimits)
    return _API


class WindowsContainment:
    spawn_kwargs = {'creationflags': CREATE_SUSPENDED}

    def __init__(self):
        ctypes, kernel32, _, ExtendedLimits = _windows_api()
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ExternalError('stdio_containment_failed')
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
                job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(limits), ctypes.sizeof(limits)):
            kernel32.CloseHandle(job)
            raise ExternalError('stdio_containment_failed')
        self.job = job

    def attach(self, pid):
        """Assign the SUSPENDED process to the job, then resume it. Fail closed."""
        ctypes, kernel32, ntdll, _ = _windows_api()
        if not isinstance(pid, int) or pid <= 0 or self.job is None:
            raise ExternalError('stdio_containment_failed')
        handle = kernel32.OpenProcess(
            PROCESS_TERMINATE | PROCESS_SET_QUOTA | PROCESS_SUSPEND_RESUME, False, pid)
        if not handle:
            raise ExternalError('stdio_containment_failed')
        try:
            if not kernel32.AssignProcessToJobObject(self.job, handle):
                raise ExternalError('stdio_containment_failed')
            if ntdll.NtResumeProcess(handle) != 0:
                raise ExternalError('stdio_containment_failed')
        finally:
            kernel32.CloseHandle(handle)

    def kill_tree(self):
        _, kernel32, _, _ = _windows_api()
        if self.job is not None:
            kernel32.TerminateJobObject(self.job, 1)

    def close(self):
        """Closing the last handle kills any remaining member (KILL_ON_JOB_CLOSE)."""
        _, kernel32, _, _ = _windows_api()
        job, self.job = self.job, None
        if job is not None:
            kernel32.CloseHandle(job)


def create_containment():
    if os.name == 'nt':
        return WindowsContainment()
    return PosixContainment()
