"""Manual-first discovery admission, outside the supervisor lock and I/O."""
import copy
from contextlib import contextmanager

from external_mcp.limits import ExternalError


class DiscoveryAdmission:
    """Bind discovery to one live executor and recheck at spawn/catalog entry.

    Discovery uses enabled-profile authorization, never trust or a synthetic
    Allow. It does not commit a business call or consume a second action token.
    """

    def __init__(self, manager, event, *, registry, snapshot, lifecycle,
                 panel_available):
        self._manager, self._event = manager, event
        self._registry, self._lifecycle = registry, lifecycle
        self._snapshot = copy.deepcopy(snapshot)
        self._panel_available = panel_available
        try:
            self._ticket = manager.admit_external_dispatch(event)
        except Exception:
            raise ExternalError('dispatch_guard') from None

    def check_binding(self, owner, event):
        if owner != self._ticket.session_id or event is not self._event:
            raise ExternalError('dispatch_guard')

    def _check_control(self, controller):
        ticket = self._manager.admit_external_dispatch(self._event)
        state = controller.snapshot()
        autopilot = controller._autopilot
        if (ticket != self._ticket or ticket.consent != 'authorized'
                or state['session_id'] != ticket.session_id
                or state['state'] != 'active' or state['execution_blocked']
                or autopilot is not None and autopilot.engaged):
            raise ExternalError('dispatch_guard')

    def _check_profile(self):
        pinned = self._snapshot['profile']
        current = self._registry.resolve(pinned['alias'],
            expected_revision=self._snapshot['revision'])['profile']
        if (current['id'] != pinned['id'] or current['revision'] != pinned['revision']
                or current['transport'] != 'stdio'
                or current.get('stdio_mode') != 'persistent'
                or self._panel_available() is not True):
            raise ExternalError('dispatch_guard')

    @contextmanager
    def boundary(self):
        # No supervisor lock on entry. Retain short RAM/store checks through
        # the caller's runtime check and generation pin; release before I/O.
        with self._manager._cond:
            controller = self._manager._sessions
            if controller is None:
                raise ExternalError('dispatch_guard')
            with controller._lock:
                try:
                    self._check_control(controller)
                except Exception:
                    raise ExternalError('dispatch_guard') from None
                with self._registry.store.lock:
                    try:
                        self._check_profile()
                    except Exception:
                        raise ExternalError('dispatch_guard') from None
                    with self._lifecycle.discovery_admission():
                        active = True
                        def track(generation):
                            if not active:
                                raise ExternalError('dispatch_guard')
                            self._lifecycle.track(self._ticket.session_id,
                                self._snapshot['profile'], generation)
                        try:
                            yield track
                        finally:
                            active = False
