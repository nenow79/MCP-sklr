"""Opt-in pytest timings and safe thread stacks; no application startup.

Load with -p scripts.pytest_diagnostics --windows-diagnostics. Reports contain
fixed operation labels, counts and durations, never SQL, parameters, locals,
exception text or source lines. Timings are inclusive and must not be summed
across nested operations. SQLite settings and task timeouts are unchanged.
"""
from contextlib import contextmanager
from functools import wraps
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import platform
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone

import pytest


class Recorder:
    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.Lock()
        self.phase = None
        self.started = 0
        self.metrics = {}
        self.events = []
        self.events_truncated = False
        self.samples = 0
        self.stop = threading.Event()
        self.write_failed = False

    def write(self, record):
        with self.lock:
            try:
                self.stream.write(json.dumps(record, sort_keys=True) + '\n')
                self.stream.flush()
            except (OSError, ValueError):
                self.write_failed = True

    def begin(self, phase):
        with self.lock:
            self.phase = phase
            self.started = time.perf_counter()
            self.metrics = {}
            self.events = []
            self.events_truncated = False
            self.samples = 0

    def end(self):
        with self.lock:
            record = dict(kind='phase', phase=self.phase,
                          elapsed_s=time.perf_counter() - self.started,
                          operations=self.metrics, task_events=self.events,
                          task_events_truncated=self.events_truncated)
            self.phase = None
        self.write(record)

    @contextmanager
    def measure(self, name):
        with self.lock:
            phase = self.phase
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            with self.lock:
                if phase is not None and self.phase is phase:
                    row = self.metrics.setdefault(name, dict(calls=0, total_s=0, max_s=0))
                    row['calls'] += 1
                    row['total_s'] += elapsed
                    row['max_s'] = max(row['max_s'], elapsed)

    def wrap(self, method, name):
        @wraps(method)
        def measured(*args, **kwargs):
            with self.measure(name):
                return method(*args, **kwargs)
        return measured

    def task_event(self, event, task_id, status=None):
        # IDs and closed enum values only; never a Task repr or its payload.
        from tasks.models import TaskStatus
        if type(task_id) is not int or status is not None and type(status) is not TaskStatus:
            return
        with self.lock:
            if self.phase is not None:
                if len(self.events) < 2000:
                    self.events.append(dict(event=event, task=task_id,
                                            status=status.value if status is not None else None,
                                            elapsed_s=time.perf_counter() - self.started))
                else:
                    self.events_truncated = True

    def watch(self, slow_seconds):
        while not self.stop.wait(0.5):
            with self.lock:
                if (self.phase is None or self.samples >= 3 or
                        time.perf_counter() - self.started < slow_seconds * (self.samples + 1)):
                    continue
                phase = self.phase
                self.samples += 1
            # Deliberately omit source lines, absolute paths and frame locals.
            stacks = []
            frames = sys._current_frames()
            try:
                for ident, frame in list(frames.items())[:32]:
                    if ident == threading.get_ident():
                        continue
                    locations = []
                    while frame is not None and len(locations) < 64:
                        locations.append(dict(file=Path(frame.f_code.co_filename).name,
                                              function=frame.f_code.co_name, line=frame.f_lineno))
                        frame = frame.f_back
                    stacks.append(dict(thread=ident, frames=locations))
            finally:
                frames.clear()
                frame = None
            self.write(dict(kind='slow_threads', phase=phase, stacks=stacks))


def install_measurements(patch, recorder):
    from autopilot.audit_store import AuditStore
    from okf_engine import service
    from okf_engine.documents import DocumentReader
    from okf_engine.index import KnowledgeIndex
    from server.knowledge_sources import KnowledgeSources
    from tasks.manager import TaskManager

    targets = (
        (AuditStore, 'append', 'audit.append'),
        (TaskManager, 'submit', 'queue.submit'),
        (TaskManager, '_finish', 'queue.finish'),
        (service, 'scan_bundle', 'okf.scan'),
        (KnowledgeIndex, 'publish', 'okf.publish'),
        (KnowledgeIndex, 'patch', 'okf.patch'),
        (KnowledgeSources, 'source', 'okf.sources'),
        (KnowledgeSources, 'current', 'okf.current'),
        (DocumentReader, '_authorize_path', 'okf.authorize'),
        (DocumentReader, '_resolve_concept', 'okf.resolve'),
        (Path, 'resolve', 'filesystem.resolve'),
    )
    for owner, attribute, label in targets:
        patch.setattr(owner, attribute, recorder.wrap(getattr(owner, attribute), label))

    original_wait, original_run = TaskManager.wait, TaskManager._run

    @wraps(original_wait)
    def wait(manager, task_id, seconds):
        recorder.task_event('wait_enter', task_id)
        with recorder.measure('queue.wait'):
            task = original_wait(manager, task_id, seconds)
        recorder.task_event('wait_return', task.id, task.status)
        return task

    @wraps(original_run)
    def run(manager, task_id):
        recorder.task_event('worker_enter', task_id)
        try:
            with recorder.measure('queue.run'):
                return original_run(manager, task_id)
        finally:
            recorder.task_event('worker_exit', task_id)

    patch.setattr(TaskManager, 'wait', wait)
    patch.setattr(TaskManager, '_run', run)
    install_sqlite_measurements(patch, recorder)


def install_sqlite_measurements(patch, recorder):
    class MeasuredConnection(sqlite3.Connection):
        label = 'sqlite.other'

        def execute(self, *args, **kwargs):
            with recorder.measure(self.label + '.execute'):
                return super().execute(*args, **kwargs)

        def executescript(self, *args, **kwargs):
            with recorder.measure(self.label + '.executescript'):
                return super().executescript(*args, **kwargs)

        def commit(self):
            with recorder.measure(self.label + '.commit'):
                return super().commit()

        def __exit__(self, *args):
            with recorder.measure(self.label + '.transaction_exit'):
                return super().__exit__(*args)

    original = sqlite3.connect
    owners = {'tasks.store': 'tasks', 'tasks.session_store': 'sessions',
              'autopilot.audit_store': 'audit', 'okf_engine.cache_store': 'cache'}

    def connect(*args, **kwargs):
        # Respect custom factories and positional connection arguments.
        if len(args) > 5 or 'factory' in kwargs:
            return original(*args, **kwargs)
        owner = owners.get(sys._getframe(1).f_globals.get('__name__'), 'other')
        with recorder.measure('sqlite.' + owner + '.connect'):
            connection = original(*args, **kwargs, factory=MeasuredConnection)
        connection.label = 'sqlite.' + owner
        return connection

    patch.setattr(sqlite3, 'connect', connect)


def pytest_addoption(parser):
    group = parser.getgroup('Windows diagnostics')
    group.addoption('--windows-diagnostics', action='store_true',
                    help='Write safe phase timings and slow thread stacks to logs/.')
    group.addoption('--diagnostic-slow-seconds', type=float, default=2.0,
                    help='Stack sampling threshold only; does not change task timeouts.')


class DiagnosticsPlugin:
    def __init__(self, recorder, slow_seconds):
        self.recorder = recorder
        self.patch = pytest.MonkeyPatch()
        self.case = -1
        self.watcher = threading.Thread(target=recorder.watch, args=(slow_seconds,),
                                        name='pytest-diagnostics', daemon=True)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_collection(self):
        self.recorder.begin(dict(stage='collection'))
        try:
            yield
        finally:
            self.recorder.end()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        # Parameter IDs can contain arbitrary user data. Case index distinguishes
        # parameter variants without serializing those IDs or fixture values.
        self.case += 1
        self.test = dict(test=item.nodeid.split('[', 1)[0], case=self.case)
        yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item):
        self.recorder.begin(dict(self.test, stage='setup'))
        try:
            yield
        finally:
            self.recorder.end()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        self.recorder.begin(dict(self.test, stage='call'))
        try:
            yield
        finally:
            self.recorder.end()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(self, item, nextitem):
        self.recorder.begin(dict(self.test, stage='teardown'))
        try:
            yield
        finally:
            self.recorder.end()

    def pytest_runtest_logreport(self, report):
        self.recorder.write(dict(kind='outcome', phase=dict(self.test, stage=report.when),
                                 outcome=report.outcome))

    def pytest_sessionstart(self, session):
        self.recorder.begin(dict(stage='instrumentation'))
        try:
            install_measurements(self.patch, self.recorder)
        finally:
            self.recorder.end()
        self.watcher.start()

    def pytest_unconfigure(self, config):
        self.recorder.stop.set()
        if self.watcher.ident is not None:
            self.watcher.join(2)
        self.patch.undo()
        self.recorder.stream.close()

    def pytest_terminal_summary(self, terminalreporter):
        terminalreporter.write_line('Diagnostics: ' + str(self.recorder.stream.name))
        if self.recorder.write_failed:
            terminalreporter.write_line('Diagnostic report incomplete: could not write all records.')


def pytest_configure(config):
    if not config.getoption('--windows-diagnostics'):
        return
    slow = config.getoption('--diagnostic-slow-seconds')
    if not math.isfinite(slow) or slow <= 0:
        raise pytest.UsageError('--diagnostic-slow-seconds must be finite and positive')
    if hasattr(config, 'workerinput') or config.getoption('numprocesses', default=0):
        raise pytest.UsageError('Windows diagnostics require a serial pytest run')
    directory = Path(config.rootpath) / 'logs'
    directory.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    report = directory / ('pytest-diagnostics-' + stamp + '.jsonl')
    recorder = Recorder(report.open('x', encoding='utf-8'))
    packages = {}
    for name in ('pytest', 'fastmcp', 'mcp', 'starlette', 'anyio'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = 'not-installed'
    recorder.write(dict(kind='environment', python=platform.python_version(),
                        platform=sys.platform, sqlite=sqlite3.sqlite_version, packages=packages))
    config.pluginmanager.register(DiagnosticsPlugin(recorder, slow), 'windows-diagnostics-recorder')
    reporter = config.pluginmanager.getplugin('terminalreporter')
    if reporter is not None:
        reporter.write_line('Safe diagnostics: ' + str(report))
