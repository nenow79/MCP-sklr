"""Detached P2e bootstrap of the work-session stack. Not wired to main.py.

enabled=False builds nothing and touches no files. enabled=True opens a
dedicated session database, invalidates stale generations once, and creates
the single shared controller/admin. Failure is fail-closed: a fixed error is
raised (no path or storage details) and the store is closed. A restart never
selects or resumes a session; the controller stays blocked until the panel
user starts a new one (resume/switch is P3 work).
"""
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from server.session_admin import SessionAdmin
from tasks.session_controller import SessionController
from tasks.session_store import SessionStore
from tasks.resume_store import ResumeStore


class SessionBootstrapError(RuntimeError):
    """Fixed message only; never a path, SQL or foreign content."""


@dataclass(frozen=True)
class WorkSessionRuntime:
    store: SessionStore
    controller: SessionController
    admin: SessionAdmin
    recovered: int

    def close(self):
        try:
            if self.controller._resumes is not None:
                self.controller._resumes.clear()
        finally:
            self.store.close()


def backup_before_start(db_path):
    """Atomic latest pre-start snapshot via SQLite backup, including committed WAL.

    Runs before SessionStore opens/migrates. No output contains database content.
    Any failure leaves the source unchanged and prevents lifecycle cleanup.
    """
    if str(db_path) == ':memory:':
        return
    path = Path(db_path).absolute()
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise ValueError('Invalid session database.')
    target = path.with_name(path.name + '.pre-project-profiles.bak')
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError('Invalid session backup destination.')
    temporary = target.with_name(target.name + '.' + uuid4().hex)
    source = destination = None
    try:
        source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - {'work_sessions', 'session_task_links', 'sqlite_sequence'}:
            raise ValueError('Expected a dedicated session database.')
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        destination = sqlite3.connect(str(temporary), timeout=5)
        source.backup(destination)
        if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Invalid session backup.')
        destination.close()
        destination = None
        source.close()
        source = None
        # Windows _commit (os.fsync) requires a writable descriptor.
        # Only the completed temporary backup is opened writable, never source.
        with temporary.open('r+b') as snapshot:
            os.fsync(snapshot.fileno())
        os.replace(temporary, target)
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        if temporary.exists():
            temporary.unlink()


def build_work_session(db_path, roots, *, enabled):
    """Return None when disabled; otherwise a runtime sharing one controller.

    db_path must be a dedicated session database, never the task database
    (SessionStore rejects foreign tables). Backup precedes opening/migration.
    Old open/ending sessions are retired; already ended records are unchanged.
    """
    if type(enabled) is not bool:
        raise ValueError('enabled must be a boolean.')
    if not enabled:
        return None
    store = None
    try:
        backup_before_start(db_path)
        store = SessionStore(db_path)
        recovered = store.retire_after_restart()
        controller = SessionController(store, resumes=ResumeStore())
        admin = SessionAdmin(store, controller, roots)
        return WorkSessionRuntime(store, controller, admin, recovered)
    except Exception:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
        raise SessionBootstrapError('Work-session bootstrap failed.') from None
