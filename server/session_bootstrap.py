"""Detached P2e bootstrap of the work-session stack. Not wired to main.py.

enabled=False builds nothing and touches no files. enabled=True opens a
dedicated session database, invalidates stale generations once, and creates
the single shared controller/admin. Failure is fail-closed: a fixed error is
raised (no path or storage details) and the store is closed. A restart never
selects or resumes a session; the controller stays blocked until the panel
user starts a new one (resume/switch is P3 work).
"""
from dataclasses import dataclass

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


def build_work_session(db_path, roots, *, enabled):
    """Return None when disabled; otherwise a runtime sharing one controller.

    db_path must be a dedicated session database, never the task database
    (SessionStore rejects foreign tables). recover_after_restart runs exactly
    once per call and invalidates revisions of all stored sessions.
    """
    if type(enabled) is not bool:
        raise ValueError('enabled must be a boolean.')
    if not enabled:
        return None
    store = None
    try:
        store = SessionStore(db_path)
        recovered = store.recover_after_restart()
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
