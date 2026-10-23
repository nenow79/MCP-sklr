"""Diagnostic instrumentation preserves results/transactions and omits contents."""
import io
import inspect
import json
import sqlite3

import pytest

from scripts.pytest_diagnostics import Recorder, install_sqlite_measurements


def test_measurement_keeps_results_and_exceptions_without_serializing_them():
    stream = io.StringIO()
    recorder = Recorder(stream)
    recorder.begin({'stage': 'call'})
    private = object()
    assert recorder.wrap(lambda arg: arg, 'operation')(private) is private

    def fail(arg):
        raise ValueError(arg)

    with pytest.raises(ValueError, match='PRIVATE_SENTINEL'):
        recorder.wrap(fail, 'operation')('PRIVATE_SENTINEL')
    recorder.end()
    record = json.loads(stream.getvalue())
    assert record['operations']['operation']['calls'] == 2
    assert record['operations']['operation']['total_s'] >= 0
    assert 'PRIVATE_SENTINEL' not in stream.getvalue()


def test_sqlite_measurements_preserve_commit_rollback_and_factory(monkeypatch):
    stream = io.StringIO()
    recorder = Recorder(stream)
    recorder.begin({'stage': 'call'})
    install_sqlite_measurements(monkeypatch, recorder)
    connection = sqlite3.connect(':memory:')
    try:
        connection.execute('CREATE TABLE notes (body TEXT)')
        with connection:
            connection.execute('INSERT INTO notes VALUES (?)', ('PRIVATE_SENTINEL',))
        with pytest.raises(ValueError):
            with connection:
                connection.execute('DELETE FROM notes')
                raise ValueError('PRIVATE_EXCEPTION')
        assert connection.execute('SELECT body FROM notes').fetchall() == [('PRIVATE_SENTINEL',)]
        connection.commit()
        assert connection.execute('PRAGMA synchronous').fetchone()[0] == 2
        assert connection.execute('PRAGMA journal_mode').fetchone()[0] == 'memory'
    finally:
        connection.close()

    class CustomConnection(sqlite3.Connection):
        pass

    custom = sqlite3.connect(':memory:', factory=CustomConnection)
    try:
        assert type(custom) is CustomConnection
    finally:
        custom.close()
    recorder.end()
    record = json.loads(stream.getvalue())
    assert record['operations']['sqlite.other.transaction_exit']['calls'] == 2
    assert record['operations']['sqlite.other.commit']['calls'] == 1
    assert 'PRIVATE_SENTINEL' not in stream.getvalue()
    assert 'PRIVATE_EXCEPTION' not in stream.getvalue()


def test_slow_stack_samples_omit_frame_locals(monkeypatch):
    stream = io.StringIO()
    recorder = Recorder(stream)
    recorder.begin({'stage': 'call'})
    recorder.started -= 10
    private_local = 'PRIVATE_STACK_SENTINEL'
    frame = inspect.currentframe()
    iterations = iter((False, True))
    monkeypatch.setattr(recorder.stop, 'wait', lambda interval: next(iterations))
    # An arbitrary different thread ID makes this captured frame observable.
    monkeypatch.setattr('sys._current_frames', lambda captured=frame: {-1: captured})
    try:
        recorder.watch(1)
        record = json.loads(stream.getvalue())
        assert record['kind'] == 'slow_threads'
        assert record['stacks'][0]['frames'][0]['function'] == 'test_slow_stack_samples_omit_frame_locals'
        assert private_local not in stream.getvalue()
        assert all(set(location) == {'file', 'function', 'line'}
                   for stack in record['stacks'] for location in stack['frames'])
    finally:
        del frame


def test_report_write_failure_does_not_replace_measured_result():
    class BrokenStream:
        def write(self, value):
            raise OSError('PRIVATE_DISK_ERROR')

    recorder = Recorder(BrokenStream())
    recorder.begin({'stage': 'call'})
    assert recorder.wrap(lambda: 42, 'operation')() == 42
    recorder.end()
    assert recorder.write_failed
