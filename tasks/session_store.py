"""Standalone SQLite foundation. NOT a dispatch gate or an approval service.

Opening this store does not recover sessions or access tasks.db. Integration
must call recover_after_restart once at application bootstrap, not on reconnect.
Task IDs are internal references; existence/ownership is the caller's concern.
No free-text metadata or checkpoint contents are accepted by this API.
"""
import json
import sqlite3
import threading
from uuid import uuid4

from tasks.store import now_iso
from tasks.session_models import (
    CheckpointState, SessionConflict, SessionNotFound, SessionState,
    SessionTaskLink, WorkSession, positive_int, root_aliases, session_id,
)

SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_sessions (
    id TEXT PRIMARY KEY,
    roots TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active','pause_requested','paused')),
    checkpoint_state TEXT NOT NULL CHECK(checkpoint_state IN ('pending','saved','unavailable')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    reason TEXT NOT NULL CHECK(reason IN ('','user_requested_pause','server_restarted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_task_links (
    task_id INTEGER PRIMARY KEY CHECK(task_id >= 1),
    session_id TEXT NOT NULL REFERENCES work_sessions(id),
    session_revision INTEGER NOT NULL CHECK(session_revision >= 1)
);
CREATE INDEX IF NOT EXISTS idx_session_task_links_session
    ON session_task_links(session_id, task_id);
"""


def _session(row):
    return WorkSession(
        id=row['id'], roots=tuple(json.loads(row['roots'])),
        state=SessionState(row['state']),
        checkpoint_state=CheckpointState(row['checkpoint_state']),
        revision=row['revision'], reason=row['reason'],
        created_at=row['created_at'], updated_at=row['updated_at'],
    )


class SessionStore:
    def __init__(self, db_path):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False,
                                     timeout=5)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute('PRAGMA foreign_keys=ON')
            version = self._conn.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError('Unsupported session schema version.')
            # Fail closed if a task store path was supplied accidentally.
            tables = {r[0] for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if tables - {'work_sessions', 'session_task_links', 'sqlite_sequence'}:
                raise ValueError('Expected a dedicated session database.')
            if str(db_path) != ':memory:':
                self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            self._conn.commit()
        except Exception:
            self._conn.close()
            raise

    def close(self):
        with self._lock:
            self._conn.close()

    def create(self, roots):
        aliases = root_aliases(roots)
        sid, timestamp = uuid4().hex, now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                'INSERT INTO work_sessions VALUES (?,?,?,?,?,?,?,?)',
                (sid, json.dumps(aliases), SessionState.ACTIVE.value,
                 CheckpointState.UNAVAILABLE.value, 1, '', timestamp, timestamp))
            return self.get(sid)

    def get(self, sid):
        session_id(sid)
        with self._lock:
            row = self._conn.execute(
                'SELECT * FROM work_sessions WHERE id=?', (sid,)).fetchone()
            return _session(row) if row else None

    def list_saved_sessions(self, limit=200):
        """Bounded panel metadata listing; explicit truncation, never free text."""
        positive_int(limit)
        if limit > 200:
            raise ValueError('Session list limit exceeded.')
        with self._lock:
            rows = self._conn.execute(
                'SELECT * FROM work_sessions ORDER BY updated_at DESC, id LIMIT ?',
                (limit + 1,)).fetchall()
            return [_session(row) for row in rows[:limit]], len(rows) > limit

    def _require(self, sid, revision):
        positive_int(revision)
        item = self.get(sid)
        if item is None:
            raise SessionNotFound('Session not found.')
        if item.revision != revision:
            raise SessionConflict('Session revision changed.')
        return item

    def request_pause(self, sid, expected_revision):
        """Metadata transition only. P2 must synchronize this with dispatch."""
        return self._pause_transition(sid, expected_revision, False)

    def mark_paused(self, sid, expected_revision):
        """Internal caller must first establish there are no running actions."""
        return self._pause_transition(sid, expected_revision, True)

    def _pause_transition(self, sid, revision, finalize):
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            item = self._require(sid, revision)
            if finalize:
                if item.state == SessionState.PAUSED:
                    return item
                if item.state != SessionState.PAUSE_REQUESTED:
                    raise SessionConflict('Pause was not requested.')
                state = SessionState.PAUSED
                checkpoint = item.checkpoint_state
            else:
                if item.state != SessionState.ACTIVE:
                    return item
                state = SessionState.PAUSE_REQUESTED
                checkpoint = CheckpointState.PENDING
            self._conn.execute(
                'UPDATE work_sessions SET state=?, checkpoint_state=?, '
                'revision=revision+1, reason=?, updated_at=? WHERE id=?',
                (state.value, checkpoint.value, 'user_requested_pause', now_iso(), sid))
            return self.get(sid)

    def replace_roots(self, sid, expected_revision, roots):
        """Metadata only; caller holds admission closed with zero running actions."""
        aliases = root_aliases(roots)
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            item = self._require(sid, expected_revision)
            if item.state != SessionState.PAUSED:
                raise SessionConflict('Folder changes require a paused session.')
            self._conn.execute(
                'UPDATE work_sessions SET roots=?, revision=revision+1, updated_at=? WHERE id=?',
                (json.dumps(aliases), now_iso(), sid))
            return self.get(sid)

    def mark_checkpoint_saved(self, sid, expected_revision):
        """Metadata only; no checkpoint content enters SQLite.

        Caller owns RAM publication and must report missing content separately.
        Every accepted save advances the session revision, even a replacement.
        """
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            item = self._require(sid, expected_revision)
            if item.state not in (SessionState.PAUSE_REQUESTED, SessionState.PAUSED):
                raise SessionConflict('Checkpoint requires a paused session.')
            self._conn.execute(
                'UPDATE work_sessions SET checkpoint_state=?, revision=revision+1, '
                'updated_at=? WHERE id=?',
                (CheckpointState.SAVED.value, now_iso(), sid))
            return self.get(sid)

    def prepare_resume(self, sid, expected_revision):
        """Internal metadata transition; preparation remains durably PAUSED.

        Caller must keep admission closed and first enforce panel decision,
        zero running actions and old-task cancellation. No token or free text
        enters SQLite. RAM handshake is owned by the controller, not this API.
        """
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            item = self._require(sid, expected_revision)
            if item.state != SessionState.PAUSED:
                raise SessionConflict('Resume preparation requires a paused session.')
            self._conn.execute(
                'UPDATE work_sessions SET revision=revision+1, updated_at=? WHERE id=?',
                (now_iso(), sid))
            return self.get(sid)

    def confirm_resume(self, sid, expected_revision):
        """Internal metadata commit AFTER the controller validates RAM handshake.

        This method alone does not authorize dispatch. The controller must
        publish a matching confirmed RAM generation before opening admission;
        uncertain commits remain blocked. Restart recovery always pauses it.
        """
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            item = self._require(sid, expected_revision)
            if item.state != SessionState.PAUSED:
                raise SessionConflict('Resume confirmation requires a paused session.')
            self._conn.execute(
                'UPDATE work_sessions SET state=?, revision=revision+1, reason=?, '
                'updated_at=? WHERE id=?',
                (SessionState.ACTIVE.value, '', now_iso(), sid))
            return self.get(sid)

    def link_task(self, sid, task_id, expected_revision):
        """Immutable association, not permission to submit/run an actual task.

        Permitted only at the exact ACTIVE revision. P2 must establish ownership
        and synchronize this transaction with task submission and the pause gate.
        """
        positive_int(task_id)
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            item = self._require(sid, expected_revision)
            if item.state != SessionState.ACTIVE:
                raise SessionConflict('Session is not active.')
            old = self._conn.execute(
                'SELECT * FROM session_task_links WHERE task_id=?', (task_id,)).fetchone()
            link = SessionTaskLink(sid, task_id, expected_revision)
            if old:
                if (old['session_id'], old['session_revision']) != (sid, expected_revision):
                    raise SessionConflict('Task association already exists.')
                return link
            self._conn.execute('INSERT INTO session_task_links VALUES (?,?,?)',
                               (task_id, sid, expected_revision))
            return link

    def get_task_link(self, task_id):
        positive_int(task_id)
        with self._lock:
            row = self._conn.execute(
                'SELECT * FROM session_task_links WHERE task_id=?', (task_id,)).fetchone()
            if row is None:
                return None
            return SessionTaskLink(row['session_id'], row['task_id'], row['session_revision'])

    def list_task_links(self, sid):
        """All links of exactly one session, ordered by task id; metadata only.

        No recent-task limit: resume coordination must not overlook old pending
        tasks. Missing task rows after retention are resolved by TaskManager.
        """
        session_id(sid)
        with self._lock:
            rows = self._conn.execute(
                'SELECT session_id, task_id, session_revision FROM session_task_links '
                'WHERE session_id=? ORDER BY task_id', (sid,)).fetchall()
            return [SessionTaskLink(row['session_id'], row['task_id'], row['session_revision'])
                    for row in rows]

    def recover_after_restart(self):
        """Bootstrap only: invalidate stale generations, never resume execution.

        Includes already paused sessions so stale callbacks cannot be accepted.
        Repeated bootstrap calls invalidate revisions again; opening alone does not.
        No checkpoint contents exist in P1; their state becomes UNAVAILABLE.
        """
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            count = self._conn.execute(
                "UPDATE work_sessions SET state='paused', "
                "checkpoint_state='unavailable', revision=revision+1, "
                "reason='server_restarted', updated_at=?", (now_iso(),)).rowcount
            return count
