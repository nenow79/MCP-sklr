#!/usr/bin/env python3
"""TaskStore: rejestr zadań w SQLite (bezpieczny przy wielu wątkach)."""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from tasks.models import ACTIVE_STATUSES, TERMINAL_STATUSES, Task, TaskStatus

_JSON_FIELDS = ("args", "command")        # listy
_DICT_FIELDS = ("payload",)               # słowniki (JSON)
_BOOL_FIELDS = ("offer_trust", "stdout_truncated", "stderr_truncated", "sensitive")

SCHEMA_VERSION = 1

_COLUMNS = (
    "created_at", "started_at", "finished_at", "status", "skill", "source",
    "script", "skill_dir", "script_preview", "args", "timeout", "command",
    "skill_hash", "approval_reason", "offer_trust", "decision", "decided_at", "exit_code", "stdout", "stderr",
    "stdout_truncated", "stderr_truncated", "duration_s", "error",
    "kind", "title", "payload", "sensitive",
)

# Kolumny dodane po fazie 4b: (nazwa, definicja SQL) - migracja addytywna starych baz.
_ADDED_COLUMNS = (
    ("kind", "TEXT NOT NULL DEFAULT 'skill_script'"),
    ("title", "TEXT NOT NULL DEFAULT ''"),
    ("payload", "TEXT NOT NULL DEFAULT '{}'"),
    ("sensitive", "INTEGER NOT NULL DEFAULT 0"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT NOT NULL,
    skill TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    script TEXT NOT NULL DEFAULT '',
    skill_dir TEXT NOT NULL DEFAULT '',
    script_preview TEXT NOT NULL DEFAULT '',
    args TEXT NOT NULL DEFAULT '[]',
    timeout INTEGER NOT NULL DEFAULT 60,
    command TEXT NOT NULL DEFAULT '[]',
    skill_hash TEXT NOT NULL DEFAULT '',
    approval_reason TEXT NOT NULL DEFAULT '',
    offer_trust INTEGER NOT NULL DEFAULT 0,
    decision TEXT NOT NULL DEFAULT '',
    decided_at TEXT,
    exit_code INTEGER,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    stdout_truncated INTEGER NOT NULL DEFAULT 0,
    stderr_truncated INTEGER NOT NULL DEFAULT 0,
    duration_s REAL,
    error TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'skill_script',
    title TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    sensitive INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode(field_name: str, value: Any) -> Any:
    if field_name in _JSON_FIELDS:
        return json.dumps(list(value), ensure_ascii=False)
    if field_name in _DICT_FIELDS:
        return json.dumps(dict(value or {}), ensure_ascii=False)
    if field_name in _BOOL_FIELDS:
        return 1 if value else 0
    if isinstance(value, TaskStatus):
        return value.value
    return value


def _row_to_task(row: sqlite3.Row) -> Task:
    d = dict(row)
    for f in _JSON_FIELDS:
        d[f] = json.loads(d[f])
    for f in _DICT_FIELDS:
        d[f] = json.loads(d[f]) if d.get(f) else {}
    for f in _BOOL_FIELDS:
        d[f] = bool(d[f])
    d["status"] = TaskStatus(d["status"])
    return Task(**d)


class TaskStore:
    def __init__(self, db_path: str):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if str(db_path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Dodaje brakujące kolumny w bazach sprzed fazy 5 (tylko addytywnie) i ustawia user_version."""
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(tasks)")}
        for name, definition in _ADDED_COLUMNS:
            if name not in existing:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _check_fields(fields: Dict[str, Any]) -> None:
        unknown = set(fields) - set(_COLUMNS)
        if unknown:
            raise ValueError(f"Nieznane pola zadania: {sorted(unknown)}")

    def create(self, **fields: Any) -> Task:
        self._check_fields(fields)
        fields.setdefault("created_at", now_iso())
        fields.setdefault("status", TaskStatus.QUEUED)
        cols = list(fields)
        sql = "INSERT INTO tasks ({}) VALUES ({})".format(
            ", ".join(cols), ", ".join("?" for _ in cols))
        with self._lock:
            cur = self._conn.execute(sql, [_encode(c, fields[c]) for c in cols])
            self._conn.commit()
            return self.get(cur.lastrowid)

    def update(self, task_id: int, **fields: Any) -> Task:
        self._check_fields(fields)
        with self._lock:
            if fields:
                sql = "UPDATE tasks SET {} WHERE id = ?".format(
                    ", ".join(f"{c} = ?" for c in fields))
                cur = self._conn.execute(
                    sql, [_encode(c, v) for c, v in fields.items()] + [task_id])
                self._conn.commit()
                if cur.rowcount == 0:
                    raise KeyError(f"Brak zadania {task_id}")
            task = self.get(task_id)
            if task is None:
                raise KeyError(f"Brak zadania {task_id}")
            return task

    def get(self, task_id: int) -> Optional[Task]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def list(self, status: Optional[TaskStatus] = None, limit: int = 20) -> List[Task]:
        sql = "SELECT * FROM tasks"
        params: List[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(TaskStatus(status).value)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_task(r) for r in rows]

    def purge(self, retention_days: int, retention_max: int) -> int:
        """Usuwa zadania terminalne: starsze niż retention_days oraz nadmiar ponad
        retention_max (najstarsze). Zadania aktywne nigdy nie są usuwane."""
        terminal = [s.value for s in TERMINAL_STATUSES]
        marks = ", ".join("?" for _ in terminal)
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=retention_days)).isoformat()
        with self._lock:
            removed = self._conn.execute(
                f"DELETE FROM tasks WHERE status IN ({marks}) AND created_at < ?",
                terminal + [cutoff]).rowcount
            removed += self._conn.execute(
                f"DELETE FROM tasks WHERE status IN ({marks}) AND id NOT IN "
                f"(SELECT id FROM tasks WHERE status IN ({marks}) "
                f"ORDER BY id DESC LIMIT ?)",
                terminal + terminal + [int(retention_max)]).rowcount
            self._conn.commit()
        return removed

    def mark_interrupted(self) -> int:
        """Start serwera: zadania aktywne -> interrupted."""
        active = [s.value for s in ACTIVE_STATUSES]
        marks = ", ".join("?" for _ in active)
        with self._lock:
            n = self._conn.execute(
                f"UPDATE tasks SET status = ?, finished_at = ?, error = ? "
                f"WHERE status IN ({marks})",
                [TaskStatus.INTERRUPTED.value, now_iso(), "serwer zrestartowany"]
                + active).rowcount
            self._conn.commit()
        return n
