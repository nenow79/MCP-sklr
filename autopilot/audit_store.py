"""SQLite metadata journal, with protected unresolved operations and bounded history.

No authority is reconstructed from this database. A commit exception is always
uncertain to the caller; no automatic retry is performed, even for local writes.
"""
import json
import math
import sqlite3
import threading
import time
from dataclasses import asdict

from autopilot.audit_models import AuditEvent
from autopilot.models import ContractBinding


class AuditError(RuntimeError):
    pass


class AuditStore:
    def __init__(self, path, *, clock=time.time):
        self._lock = threading.RLock()
        self._clock = clock
        self._db = None
        try:
            self._db = sqlite3.connect(str(path), check_same_thread=False)
            self._db.execute('PRAGMA synchronous=FULL')
            self._db.executescript('''
                CREATE TABLE IF NOT EXISTS autopilot_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    utc REAL NOT NULL, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS autopilot_operations (
                    operation_id TEXT PRIMARY KEY, phase TEXT NOT NULL,
                    utc REAL NOT NULL, metadata TEXT NOT NULL);
            ''')
            self._db.commit()
            self.purge()
        except Exception:
            if self._db is not None:
                self._db.close()
            raise AuditError('audit_unavailable') from None

    def _now(self):
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise AuditError('audit_clock')
        return value

    def _purge(self, now):
        self._db.execute('DELETE FROM autopilot_events WHERE utc < ?', (now - 30 * 86400,))
        self._db.execute('''DELETE FROM autopilot_events WHERE sequence NOT IN
                          (SELECT sequence FROM autopilot_events ORDER BY sequence DESC LIMIT 1000)''')

    def append(self, event):
        if type(event) is not AuditEvent:
            raise AuditError('invalid_audit_event')
        # Reconstruct validation, protecting this boundary even from unsafe object mutation.
        AuditEvent(**{name: getattr(event, name) for name in event.__dataclass_fields__})
        payload = json.dumps(asdict(event), sort_keys=True, separators=(',', ':'), allow_nan=False)
        with self._lock:
            try:
                now = self._now()
                with self._db:
                    if event.event == 'started':
                        self._db.execute('INSERT INTO autopilot_operations VALUES (?, ?, ?, ?)',
                                         (event.operation_id, 'started', now, payload))
                    elif event.event in {'finished', 'resolved'}:
                        row = self._db.execute('SELECT phase FROM autopilot_operations WHERE operation_id=?',
                                               (event.operation_id,)).fetchone()
                        if row is None or (event.event == 'finished' and row[0] != 'started'):
                            raise AuditError('invalid_audit_transition')
                        if event.code == 'uncertain':
                            self._db.execute('UPDATE autopilot_operations SET phase=?, utc=?, metadata=? '
                                             'WHERE operation_id=?',
                                             ('uncertain', now, payload, event.operation_id))
                        else:
                            self._db.execute('DELETE FROM autopilot_operations WHERE operation_id=?',
                                             (event.operation_id,))
                    self._db.execute('INSERT INTO autopilot_events(utc, metadata) VALUES (?, ?)', (now, payload))
                    self._purge(now)
            except Exception:
                raise AuditError('audit_commit_uncertain') from None

    def unresolved(self):
        with self._lock:
            try:
                return tuple(self._decode(row[0]) for row in self._db.execute(
                    'SELECT metadata FROM autopilot_operations ORDER BY utc, operation_id'))
            except Exception:
                raise AuditError('audit_unavailable') from None

    def history(self):
        with self._lock:
            try:
                return tuple({'utc': row[0], **self._decode(row[1])} for row in self._db.execute(
                    'SELECT utc, metadata FROM autopilot_events ORDER BY sequence'))
            except Exception:
                raise AuditError('audit_unavailable') from None

    @staticmethod
    def _decode(payload):
        fields = json.loads(payload)
        if type(fields) is not dict or set(fields) != set(AuditEvent.__dataclass_fields__):
            raise AuditError('invalid_audit_event')
        fields['binding'] = ContractBinding.from_dict(fields['binding'])
        if type(fields['rule_ids']) is not list:
            raise AuditError('invalid_audit_event')
        fields['rule_ids'] = tuple(fields['rule_ids'])
        return asdict(AuditEvent(**fields))

    def purge(self):
        with self._lock:
            try:
                with self._db:
                    self._purge(self._now())
            except Exception:
                raise AuditError('audit_unavailable') from None

    def close(self):
        with self._lock:
            self._db.close()
