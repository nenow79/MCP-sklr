"""Lekka, asynchroniczna telemetria wywołań narzędzi MCP."""

import atexit
import csv
import json
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable


_LOG_DIRECTORY = Path(__file__).resolve().parent / "logs"
_RETENTION_DAYS = 90
_FLUSH_INTERVAL_SECONDS = 1.0
_BATCH_SIZE = 20
_RECORD_QUEUE: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1_000)
_STOP_EVENT = threading.Event()

_CSV_COLUMNS = (
    "timestamp_utc",
    "workspace",
    "tool_name",
    "input_bytes",
    "output_bytes",
    "duration_ms",
    "success",
    "error_type",
    "error_summary",
)


def _json_size_bytes(value: Any) -> int:
    """Zwraca rozmiar JSON bez zapisywania jego zawartości."""
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return 0


def _summarize_error(error: Any) -> str:
    """Zapisuje krótki, jednoliniowy opis błędu bez tracebacku."""
    summary = " ".join(str(error).split())
    return summary[:240] if summary else "unknown"


def _cleanup_expired_logs() -> None:
    """Usuwa pliki dzienne starsze niż okres retencji."""
    if not _LOG_DIRECTORY.exists():
        return

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=_RETENTION_DAYS)
    for log_file in _LOG_DIRECTORY.glob("mcp_tool_usage-*.csv"):
        try:
            date_part = log_file.stem.removeprefix("mcp_tool_usage-")
            file_date = datetime.strptime(date_part, "%Y-%m-%d").date()
            if file_date < cutoff:
                log_file.unlink()
        except (OSError, ValueError):
            continue


def _ensure_csv_schema(log_file: Path) -> None:
    """Uzupełnia nagłówek istniejącego pliku o nowe kolumny telemetryczne."""
    with log_file.open("r", encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames == list(_CSV_COLUMNS):
            return
        existing_records = list(reader)

    migrated_records = []
    for record in existing_records:
        migrated_record = {column: record.get(column, "") for column in _CSV_COLUMNS}
        if "error_summary" not in record and record.get("success") == "False":
            migrated_record["error_summary"] = "unknown"
        migrated_records.append(migrated_record)

    with log_file.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(migrated_records)


def _migrate_existing_logs() -> None:
    """Migruje wszystkie zachowane pliki telemetryczne przy starcie serwera."""
    if not _LOG_DIRECTORY.exists():
        return

    for log_file in _LOG_DIRECTORY.glob("mcp_tool_usage-*.csv"):
        try:
            _ensure_csv_schema(log_file)
        except (OSError, csv.Error):
            continue


def _write_records(records: list[dict[str, Any]]) -> None:
    """Dopisuje partię rekordów do właściwego pliku dziennego CSV."""
    if not records:
        return

    _LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    records_by_file: dict[Path, list[dict[str, Any]]] = {}
    for record in records:
        log_date = record["timestamp_utc"][:10]
        log_file = _LOG_DIRECTORY / f"mcp_tool_usage-{log_date}.csv"
        records_by_file.setdefault(log_file, []).append(record)

    for log_file, file_records in records_by_file.items():
        file_is_new = not log_file.exists()
        if not file_is_new:
            _ensure_csv_schema(log_file)
        with log_file.open("a", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=_CSV_COLUMNS)
            if file_is_new:
                writer.writeheader()
            writer.writerows(file_records)


def _telemetry_worker() -> None:
    """Zapisuje kolejkę partiami; błędy telemetryki nie wpływają na MCP."""
    pending: list[dict[str, Any]] = []
    last_flush = time.monotonic()

    while not _STOP_EVENT.is_set() or not _RECORD_QUEUE.empty():
        timeout = max(0.0, _FLUSH_INTERVAL_SECONDS - (time.monotonic() - last_flush))
        try:
            pending.append(_RECORD_QUEUE.get(timeout=timeout))
        except queue.Empty:
            if not pending:
                # Przesuń termin także przy bezczynności. W przeciwnym razie
                # po pierwszym interwale get(timeout=0) tworzy aktywną pętlę.
                last_flush = time.monotonic()
                continue

        if pending and (
            len(pending) >= _BATCH_SIZE
            or time.monotonic() - last_flush >= _FLUSH_INTERVAL_SECONDS
            or _STOP_EVENT.is_set()
        ):
            try:
                _write_records(pending)
            except OSError:
                pass
            pending.clear()
            last_flush = time.monotonic()


def _shutdown_telemetry() -> None:
    _STOP_EVENT.set()
    _WORKER.join(timeout=2.0)


def track_mcp_tool(
    function: Callable[..., Any], workspace_name_getter: Callable[[], str],
    *, confidential: bool = False,
) -> Callable[..., Any]:
    """Dekoruje narzędzie MCP lekkim, nieblokującym pomiarem użycia.

    confidential wybiera rejestrujący, nigdy analiza treści odpowiedzi.
    W tym trybie wejście/wynik nie trafiają do serializatora, pomiaru ani
    streszczenia błędu. Puste rozmiary CSV oznaczają niemierzone, nie zero.
    Odczytujemy tylko zewnętrzną flagę success; wynik zwracamy bez zmiany.
    Wyjątek propaguje jak dotychczas, ale telemetria nie zapisuje jego treści
    ani nazwy klasy. Poufne narzędzie musi niezależnie zapewniać safe errors.
    """
    if type(confidential) is not bool:
        raise ValueError("confidential musi być bool.")
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        started_at = time.perf_counter()
        result: Any = None
        success = False
        error_type = ""
        error_summary = ""

        try:
            result = function(*args, **kwargs)
            success = not (isinstance(result, dict) and result.get("success") is False)
            if not success:
                error_type = "tool_error"
                error_summary = ("Błąd narzędzia poufnego." if confidential
                                 else _summarize_error(result.get("error")))
            return result
        except Exception as error:
            error_type = "confidential_exception" if confidential else type(error).__name__
            error_summary = ("Wyjątek narzędzia poufnego." if confidential
                             else _summarize_error(error))
            raise
        finally:
            try:
                workspace_name = workspace_name_getter()
            except Exception:
                workspace_name = ""

            record = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "workspace": workspace_name,
                "tool_name": function.__name__,
                "input_bytes": "" if confidential else _json_size_bytes({"args": args, "kwargs": kwargs}),
                "output_bytes": ("" if confidential else
                                 _json_size_bytes(result) if error_type != type(None).__name__ else 0),
                "duration_ms": round((time.perf_counter() - started_at) * 1_000, 3),
                "success": success,
                "error_type": error_type,
                "error_summary": error_summary,
            }
            try:
                _RECORD_QUEUE.put_nowait(record)
            except queue.Full:
                pass

    return wrapped


_cleanup_expired_logs()
_migrate_existing_logs()
_WORKER = threading.Thread(target=_telemetry_worker, name="mcp-telemetry", daemon=True)
_WORKER.start()
atexit.register(_shutdown_telemetry)
