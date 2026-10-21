"""Panel-only work-session adapter. Not registered as a model tool.

Caller must use authenticated panel endpoints (Host/Origin/session checks).
The adapter never resumes/switches sessions or changes root permissions.
Roots describe project membership, not a new argument-level authorization scope.
No production wiring in this stage.
"""
import threading

from tasks.session_models import SessionConflict, root_aliases


class SessionAdmin:
    def __init__(self, store, controller, roots):
        self._store = store
        self._controller = controller
        self._roots = roots
        self._lock = threading.RLock()

    def status(self):
        with self._lock:
            state = self._controller.snapshot()
            item = self._store.get(state['session_id']) if state['session_id'] else None
            return {
                **state,
                'roots': list(item.roots) if item else [],
                'checkpoint_available': False,
                'resume_available': False,
                'switch_available': False,
                'warning': 'Pauza obejmuje wszystkie rozmowy tej instancji sklr.',
            }

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
