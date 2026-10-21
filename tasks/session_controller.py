"""Single-instance admission foundation; not wired to production yet.

Only authenticated panel integration may bind/pause. No model-facing mutation.
One controller must be shared by tools and TaskManager. Admission tokens mark
local action start, not completion or authorization. Callers must release each
accepted token in finally. No lock may be held across actual tool execution.
P2 integration must define lock ordering with TaskManager before using this API.
"""
import threading
from contextlib import contextmanager

from tasks.session_models import SessionConflict, SessionNotFound, SessionState


class SessionBlocked(RuntimeError):
    """Fixed control signal, never foreign tool contents."""


class SessionController:
    def __init__(self, store):
        self._store = store
        self._lock = threading.RLock()
        self._selected = None
        self._blocked = True
        self._tokens = set()

    def bind(self, sid, expected_revision):
        """Panel-only integration: bind once. Switching/resuming is P3 work.

        Does not recover a store or select a session automatically on reconnect.
        Authentication and valid root registry entries must be checked by caller.
        """
        with self._lock:
            item = self._store.get(sid)
            if item is None:
                raise SessionNotFound('Session not found.')
            if type(expected_revision) is not int or expected_revision < 1:
                raise ValueError('Expected a positive revision.')
            if item.revision != expected_revision:
                raise SessionConflict('Session revision changed.')
            if self._selected is not None:
                if sid != self._selected:
                    raise SessionConflict('Session switching is not supported.')
                # Rebinding/reconnecting can never clear the pause latch.
                return self.snapshot()
            self._selected = sid
            self._blocked = item.state != SessionState.ACTIVE
            return self.snapshot()

    def begin_action(self):
        """Atomic local start. No session ID is accepted from the model.

        Each actual execution needs its own token: admission of a tool that
        submits a queued task does not authorize that task's later dispatch.
        """
        with self._lock:
            if self._selected is None or self._blocked:
                raise SessionBlocked('Session execution is blocked.')
            try:
                item = self._store.get(self._selected)
            except Exception:
                self._blocked = True
                raise SessionBlocked('Session state is unavailable.') from None
            if item is None or item.state != SessionState.ACTIVE:
                self._blocked = True
                raise SessionBlocked('Session execution is blocked.')
            token = object()
            self._tokens.add(token)
            return token

    @contextmanager
    def task_submission(self):
        """Internal manager only; manager lock precedes controller lock.

        Keep assignment and task creation on the same side of the pause gate.
        This is synchronization, NOT a transaction spanning the two databases.
        No manager callbacks may be invoked while acquiring locks in reverse.
        """
        with self._lock:
            if self._selected is None or self._blocked:
                raise SessionBlocked('Session execution is blocked.')
            try:
                item = self._store.get(self._selected)
            except Exception:
                self._blocked = True
                raise SessionBlocked('Session state is unavailable.') from None
            if item is None or item.state != SessionState.ACTIVE:
                self._blocked = True
                raise SessionBlocked('Session execution is blocked.')

            def associate(task_id):
                try:
                    self._store.link_task(item.id, task_id, item.revision)
                except Exception:
                    self._blocked = True
                    raise SessionBlocked('Task association could not be saved.') from None

            yield associate

    def begin_task(self, task_id):
        """Internal dispatch: validate immutable association before admission.

        TaskManager holds its condition first. The controller never calls back
        into TaskManager. Paused/invalid tasks remain queued but unscheduled.
        """
        with self._lock:
            if self._selected is None or self._blocked:
                raise SessionBlocked('Session execution is blocked.')
            try:
                item = self._store.get(self._selected)
                link = self._store.get_task_link(task_id)
            except Exception:
                self._blocked = True
                raise SessionBlocked('Session state is unavailable.') from None
            if (item is None or item.state != SessionState.ACTIVE or link is None
                    or link.session_id != self._selected
                    or link.session_revision != item.revision):
                self._blocked = True
                raise SessionBlocked('Task session association is not current.')
            return self.begin_action()

    def finish_action(self, token):
        """Release exactly once, including failed/cancelled actions.

        Invalid releases never decrease the count. A persistence failure leaves
        the latch blocked and the durable state unfinalized; it is not success.
        """
        with self._lock:
            if token not in self._tokens:
                raise ValueError('Unknown action token.')
            self._tokens.remove(token)
            if self._blocked and not self._tokens and self._selected is not None:
                self._finalize_pause()

    def request_pause(self, expected_revision):
        """Panel-only integration. Persistence errors do not reopen admission."""
        with self._lock:
            if self._selected is None:
                raise SessionConflict('No session is selected.')
            # Close admission before I/O. Even conflicts leave admission closed;
            # user must inspect status and retry with the current revision.
            self._blocked = True
            self._store.request_pause(self._selected, expected_revision)
            if not self._tokens:
                self._finalize_pause()
            return self.snapshot()

    def _finalize_pause(self):
        item = self._store.get(self._selected)
        if item is None:
            raise SessionNotFound('Session not found.')
        if item.state == SessionState.PAUSE_REQUESTED:
            self._store.mark_paused(item.id, item.revision)

    def snapshot(self):
        """Detached structured state; no task outputs or checkpoint contents.

        execution_blocked is separate from durable state in case persistence
        failed. A selected session applies to ALL clients of this instance.
        """
        with self._lock:
            item = self._store.get(self._selected) if self._selected else None
            return {
                'scope': 'instance',
                'session_id': self._selected,
                'execution_blocked': self._blocked,
                'running_actions': len(self._tokens),
                'state': item.state.value if item else None,
                'reason': item.reason if item else '',
                'revision': item.revision if item else None,
                'checkpoint_state': item.checkpoint_state.value if item else None,
            }
