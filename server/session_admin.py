"""Panel-only work-session adapter. Not registered as a model tool.

Caller must use authenticated panel endpoints (Host/Origin/session checks).
Resume preparation uses only the shared TaskManager; no session switching
or root permission changes are allowed.
Roots describe project membership, not a new argument-level authorization scope.
No production wiring in this stage.
"""
import threading
from contextlib import nullcontext

from tasks.session_models import SessionConflict, SessionState, root_aliases, session_id


class SessionAdmin:
    def __init__(self, store, controller, roots):
        self._store = store
        self._controller = controller
        self._roots = roots
        self._lock = threading.RLock()
        self._manager = None

    def attach_manager(self, manager):
        """Composition only: reject a different controller or manager."""
        with self._lock:
            if (getattr(manager, '_sessions', None) is not self._controller
                    or (self._manager is not None and self._manager is not manager)):
                raise ValueError('Work-session manager mismatch.')
            self._manager = manager

    def resume_preview(self, sid, revision):
        with self._lock:
            if self._manager is None:
                raise ValueError('Resume coordinator unavailable.')
            return self._manager.session_resume_preview(sid, revision)

    def prepare_resume(self, sid, revision, supplement, cancel_count,
                       allow_missing_checkpoint, confirmed):
        with self._lock:
            if confirmed is not True:
                raise ValueError('Explicit panel confirmation required.')
            if self._manager is None:
                raise ValueError('Resume coordinator unavailable.')
            return self._manager.prepare_session_resume(
                supplement, revision, panel_session_id=sid,
                expected_cancel_count=cancel_count,
                allow_missing_checkpoint=allow_missing_checkpoint)

    def resume_plan(self):
        with self._lock:
            return self._controller.get_confirmed_resume_plan()

    def saved_sessions(self):
        with self._lock:
            selected = self._controller.snapshot()['session_id']
            available = {entry['alias'] for entry in self._roots.list_status()
                         if entry.get('valid') is True}
            items, truncated = self._store.list_saved_sessions()
            return {'sessions': [
                {'session_id': item.id, 'revision': item.revision,
                 'roots': list(item.roots), 'state': item.state.value,
                 'reason': item.reason, 'updated_at': item.updated_at,
                 'ram_lost': item.reason == 'server_restarted',
                 'recoverable': selected is None and item.state == SessionState.PAUSED
                     and item.reason == 'server_restarted'
                     and all(alias in available for alias in item.roots)}
                for item in items], 'truncated': truncated}

    def recover(self, sid, revision, confirmed):
        session_id(sid)
        with self._lock:
            if confirmed is not True:
                raise ValueError('Explicit panel confirmation required.')
            item = self._store.get(sid)
            if item is None:
                raise SessionConflict('Saved session changed.')
            available = {entry['alias'] for entry in self._roots.list_status()
                         if entry.get('valid') is True}
            if any(alias not in available for alias in item.roots):
                raise ValueError('Session roots unavailable.')
            self._controller.recover_selection(sid, revision)
            return self.status()

    def status(self):
        with self._lock:
            state = self._controller.snapshot()
            item = self._store.get(state['session_id']) if state['session_id'] else None
            return {
                **state,
                'roots': list(item.roots) if item else [],
                'checkpoint_available': state.get('checkpoint_available') is True,
                'checkpoint_content_available': state.get('checkpoint_content_available') is True,
                'resume_available': self._manager is not None
                    and self._controller._resumes is not None,
                'ram_lost': bool(item and item.reason == 'server_restarted'),
                'switch_available': False,
                'warning': 'Pauza obejmuje wszystkie rozmowy tej instancji sklr.',
            }

    def checkpoint(self):
        """Authenticated panel read only; no logging, persistence or task results.

        Availability is checked by the controller under its own lock. Durable
        saved metadata alone does not mean content is present in RAM.
        """
        with self._lock:
            return self._controller.get_checkpoint()

    def start(self, roots):
        """Explicit panel user action only. Starts at most one session per instance.

        Availability is checked at selection time, not an authorization guarantee
        for later operations. Failed bind leaves durable metadata for diagnosis,
        but never silently starts or retries a different session.
        """
        aliases = root_aliases(roots)
        with self._lock:
            state = self._controller.snapshot()
            if state['session_id'] is not None:
                raise SessionConflict('Sesja jest już przypięta; przełączenie niedostępne.')
            available = {entry['alias'] for entry in self._roots.list_status()
                         if entry.get('valid') is True}
            if any(alias not in available for alias in aliases):
                raise ValueError('Wybrane foldery są nieznane lub niedostępne.')
            item = self._store.create(aliases)
            self._controller.bind(item.id, item.revision)
            return self.status()

    def change_folders(self, operation, identity, autopilot=None, *, removing=None, adding=False):
        """Panel mutation serialized with dispatch and resume, never a live scope expansion."""
        manager_lock = self._manager._cond if self._manager is not None else nullcontext()
        autopilot_lock = autopilot._lock if autopilot is not None else nullcontext()
        with self._lock, autopilot_lock, manager_lock, self._controller._lock:
            if self._controller._stopping or (self._manager is not None and self._manager._stopped):
                raise SessionConflict('Serwer jest zatrzymywany.')
            state = self._controller.snapshot()
            if state['session_id'] is not None:
                if (identity.get('session_id') != state['session_id']
                        or type(identity.get('revision')) is not int
                        or identity['revision'] != state['revision']
                        or state['state'] != 'paused' or state['running_actions']
                        or state.get('resume_pending')):
                    raise SessionConflict('Wstrzymaj sesję i odśwież stan przed zmianą folderów.')
                available = [r['alias'] for r in self._roots.list_status() if r.get('valid') is True]
                if adding and len(available) >= 32:
                    raise SessionConflict('Sesja może obejmować najwyżej 32 foldery.')
                if removing is not None and not [a for a in available if a != removing]:
                    raise SessionConflict('Dodaj inny dostępny folder przed usunięciem ostatniego folderu sesji.')
                if self._controller._autopilot is not None and autopilot is None:
                    raise SessionConflict('Obsługa zmiany zakresu autopilota jest niedostępna.')
                if autopilot is not None:
                    autopilot.action('manual', dict(identity, confirmed=True,
                        decision_revision=autopilot._revision))
                self._controller._invalidate_resume()
            elif identity.get('session_id') is not None:
                raise SessionConflict('Sesja się zmieniła. Odśwież stan.')
            result = operation()
            if state['session_id'] is not None:
                aliases = [r['alias'] for r in self._roots.list_status() if r.get('valid') is True]
                # Empty/oversized membership fails closed, even if the registry write succeeded.
                saved = self._store.replace_roots(state['session_id'], state['revision'], aliases)
                if self._controller._checkpoint_current_revision == state['revision']:
                    self._controller._checkpoint_current_revision = saved.revision
            return result

    def end(self, sid, expected_revision, autopilot=None):
        autopilot_lock = autopilot._lock if autopilot is not None else nullcontext()
        with self._lock, autopilot_lock:
            if self._manager is None:
                raise ValueError('Session coordinator unavailable.')
            state = self._controller.snapshot()
            if (state['session_id'] != sid or type(expected_revision) is not int
                    or state['revision'] != expected_revision):
                raise SessionConflict('Session selection or revision changed.')
            try:
                self._manager.end_session(sid, expected_revision)
            finally:
                item = self._store.get(sid)
                if item is not None and item.lifecycle != 'open' and autopilot is not None:
                    autopilot.discard_session_internal()
            return self.status()

    def pause(self, session_id, expected_revision):
        """Exact selected ID prevents a stale tab from controlling another session.

        Controller owns atomic admission/pause ordering. No manager callbacks
        and no lock inversion with TaskManager. Exceptions must be mapped to
        safe panel error responses by the future endpoint layer.
        """
        with self._lock:
            state = self._controller.snapshot()
            if state['session_id'] is None or session_id != state['session_id']:
                raise SessionConflict('Sesja panelu nie jest aktualnie przypięta.')
            self._controller.request_pause(expected_revision)
            return self.status()
