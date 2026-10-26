"""C3b: telemetria poufna nie serializuje wejścia/wyniku ani treści błędu."""
import ast
import json
from pathlib import Path
import queue
from types import SimpleNamespace

import pytest

from telemetry import track_mcp_tool

SECRET = "SENTINEL-TELEMETRY-PRIVATE"


class CaptureQueue(queue.Queue):
    """Pełny interfejs Queue dla wątku tła; rekordy testowe trafiają tylko do listy."""

    def __init__(self):
        super().__init__()
        self.records = []

    def put_nowait(self, record):
        self.records.append(record)


def decorate(monkeypatch, function, *, confidential=False):
    captured = CaptureQueue()
    monkeypatch.setattr("telemetry._RECORD_QUEUE", captured)
    wrapped = track_mcp_tool(function, lambda: "workspace-test", confidential=confidential)
    return wrapped, captured


def test_confidential_success_never_serializes_input_or_result(monkeypatch):
    result = {"success": True, "secret_result": {"content": SECRET}}
    calls = []

    def forbidden(value):
        calls.append(value)
        raise AssertionError("confidential path must not serialize")

    monkeypatch.setattr("telemetry._json_size_bytes", forbidden)
    wrapped, captured = decorate(
        monkeypatch, lambda token: result, confidential=True)

    assert wrapped(SECRET) is result
    assert calls == []
    assert len(captured.records) == 1
    record = captured.records[0]
    assert record["input_bytes"] == "" and record["output_bytes"] == ""
    assert record["success"] is True and record["error_type"] == ""
    assert SECRET not in json.dumps(record)


def test_confidential_tool_error_uses_constant_summary(monkeypatch):
    calls = []
    monkeypatch.setattr("telemetry._json_size_bytes", lambda value: calls.append(value))
    wrapped, captured = decorate(
        monkeypatch,
        lambda: {"success": False, "error": "private detail " + SECRET},
        confidential=True,
    )

    result = wrapped()
    assert result["success"] is False
    assert calls == []
    record = captured.records[0]
    assert record["input_bytes"] == "" and record["output_bytes"] == ""
    assert record["error_type"] == "tool_error"
    assert record["error_summary"] == "Błąd narzędzia poufnego."
    assert SECRET not in json.dumps(record)


def test_confidential_exception_propagates_without_telemetry_leak(monkeypatch):
    calls = []
    monkeypatch.setattr("telemetry._json_size_bytes", lambda value: calls.append(value))

    def fail():
        raise RuntimeError("private exception " + SECRET)

    wrapped, captured = decorate(monkeypatch, fail, confidential=True)
    with pytest.raises(RuntimeError, match=SECRET):
        wrapped()

    assert calls == []
    record = captured.records[0]
    assert record["input_bytes"] == "" and record["output_bytes"] == ""
    assert record["error_type"] == "confidential_exception"
    assert record["error_summary"] == "Wyjątek narzędzia poufnego."
    assert SECRET not in json.dumps(record)


def test_ordinary_tool_keeps_existing_size_measurements(monkeypatch):
    measured = []
    monkeypatch.setattr("telemetry._json_size_bytes", lambda value: measured.append(value) or 17)
    result = {"success": True, "data": "ordinary"}
    wrapped, captured = decorate(monkeypatch, lambda text: result)

    assert wrapped("ordinary input") is result
    assert len(measured) == 2
    record = captured.records[0]
    assert record["input_bytes"] == 17 and record["output_bytes"] == 17
    assert record["success"] is True


def test_confidential_flag_must_be_explicit_bool():
    with pytest.raises(ValueError, match="bool"):
        track_mcp_tool(lambda: None, lambda: "", confidential=1)


def run_worker_steps(steps, *, fail_write=False):
    """Run only the worker definition with a fake clock and scripted queue.

    No module startup, global patches, threads or wall-clock waits.
    None means Empty after the requested timeout; each step may request shutdown.
    """
    source = Path(__file__).resolve().parents[1] / "telemetry.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    worker = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_telemetry_worker")
    clock = SimpleNamespace(now=0.0)
    stop = SimpleNamespace(value=False)
    remaining = list(steps)
    timeouts, batches = [], []

    class ScriptedQueue:
        def get(self, *, timeout):
            timeouts.append(timeout)
            assert remaining, "unexpected extra queue operation"
            record, elapsed, stopping = remaining.pop(0)
            clock.now += timeout if elapsed is None else elapsed
            stop.value = stopping
            if record is None:
                raise queue.Empty
            return record

        def empty(self):
            return not remaining

    def write(records):
        batches.append(list(records))
        if fail_write:
            raise OSError("test write failure")

    namespace = {
        "queue": queue,
        "time": SimpleNamespace(monotonic=lambda: clock.now),
        "_STOP_EVENT": SimpleNamespace(is_set=lambda: stop.value),
        "_RECORD_QUEUE": ScriptedQueue(),
        "_FLUSH_INTERVAL_SECONDS": 1.0,
        "_BATCH_SIZE": 20,
        "_write_records": write,
    }
    exec(compile(ast.Module(body=[worker], type_ignores=[]), str(source), "exec"), namespace)
    namespace["_telemetry_worker"]()
    assert not remaining
    return timeouts, batches


def test_worker_idle_queue_keeps_positive_wait_after_each_interval():
    timeouts, batches = run_worker_steps([
        (None, None, False), (None, None, False), (None, None, True),
    ])
    assert timeouts == [1.0, 1.0, 1.0]
    assert batches == []


def test_worker_flushes_pending_records_at_interval_and_resumes_idle_wait():
    record = {"test": "interval"}
    timeouts, batches = run_worker_steps([
        (record, 0.25, False), (None, None, False), (None, None, True),
    ])
    assert timeouts == [1.0, 0.75, 1.0]
    assert batches == [[record]]


def test_worker_flushes_full_batch_before_interval():
    records = [{"test": index} for index in range(20)]
    timeouts, batches = run_worker_steps(
        [(record, 0.0, False) for record in records] + [(None, None, True)])
    assert timeouts == [1.0] * 21
    assert batches == [records]


@pytest.mark.parametrize("fail_write", [False, True])
def test_worker_shutdown_drains_queue_and_tolerates_write_failure(fail_write):
    records = [{"test": "first"}, {"test": "second"}]
    timeouts, batches = run_worker_steps([
        (records[0], 0.25, True), (records[1], 0.0, True),
    ], fail_write=fail_write)
    assert timeouts == [1.0, 1.0]
    assert batches == [[records[0]], [records[1]]]


def test_full_queue_does_not_change_confidential_result(monkeypatch):
    class FullQueue(queue.Queue):
        def put_nowait(self, record):
            raise queue.Full

    monkeypatch.setattr("telemetry._RECORD_QUEUE", FullQueue())
    monkeypatch.setattr("telemetry._json_size_bytes", lambda value: pytest.fail("serialized"))
    result = {"success": True, "secret_result": {"content": SECRET}}
    wrapped = track_mcp_tool(lambda: result, lambda: "", confidential=True)
    assert wrapped() is result
