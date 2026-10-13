"""C3b: telemetria poufna nie serializuje wejścia/wyniku ani treści błędu."""
import json
import queue

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


def test_full_queue_does_not_change_confidential_result(monkeypatch):
    class FullQueue(queue.Queue):
        def put_nowait(self, record):
            raise queue.Full

    monkeypatch.setattr("telemetry._RECORD_QUEUE", FullQueue())
    monkeypatch.setattr("telemetry._json_size_bytes", lambda value: pytest.fail("serialized"))
    result = {"success": True, "secret_result": {"content": SECRET}}
    wrapped = track_mcp_tool(lambda: result, lambda: "", confidential=True)
    assert wrapped() is result
