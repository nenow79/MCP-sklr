"""Internal P3c-A dispatch boundary; not wired to the production MCP route.

Profile/schema/generation/limiter and supervisor lifecycle are separate checks.
A ticket is an authority snapshot, never a durable grant or retry capability.
"""
import threading
from contextlib import contextmanager

from autopilot.context import CURRENT
from external_mcp.limits import ExternalError


class DispatchAdmission:
    """One operation, explicit scope, one local admission before business I/O.

    Scope/trust callbacks run without manager/controller locks. The final
    task/session check and dispatch marker share manager -> controller locks.
    No transport, validator, slot wait or cleanup runs inside this boundary.
    """

    def __init__(self, manager, event, scope):
        self._manager = manager
        self._event = event
        self._scope = scope
        self._lock = threading.RLock()
        self._admitted = False
        try:
            self._ticket = manager.admit_external_dispatch(event)
        except Exception:
            raise ExternalError('dispatch_guard') from None

    @property
    def admitted(self):
        with self._lock:
            return self._admitted

    def guard(self, name, schema_sha, trust_check=None):
        """Admit once or raise a fixed refusal; never perform the business call.

        Explicit trust callback is mandatory for an authorized executor.
        This scaffold does not claim atomic profile/trust-generation checking;
        those resource checks and lock integration belong to the remaining P3c.
        """
        with self._lock:
            if self._admitted:
                raise ExternalError('dispatch_guard')
            marker = CURRENT.set(self._scope)
            try:
                if self._scope is not None:
                    self._scope.recheck()
                ticket = self._manager.admit_external_dispatch(self._event)
                if ticket != self._ticket:
                    raise ExternalError('dispatch_guard')
                if ticket.consent == 'authorized':
                    if trust_check is None or trust_check(name, schema_sha) is not True:
                        raise ExternalError('dispatch_guard')
                elif ticket.consent != 'approved':
                    raise ExternalError('dispatch_guard')

                # Pause takes the controller lock; end takes manager then
                # controller. Neither can cross this final check/marker gap.
                with self._manager._cond:
                    controller = self._manager._sessions
                    if controller is None:
                        raise ExternalError('dispatch_guard')
                    with controller._lock:
                        current = self._manager.admit_external_dispatch(self._event)
                        if current != self._ticket:
                            raise ExternalError('dispatch_guard')
                        if self._scope is not None:
                            self._scope.dispatched = True
                        self._admitted = True
            except Exception:
                raise ExternalError('dispatch_guard') from None
            finally:
                CURRENT.reset(marker)
        return None

    def check_binding(self, owner, event):
        """Internal supervisor binding; a snapshot is not dispatch authority."""
        if owner != self._ticket.session_id or event is not self._event:
            raise ExternalError('dispatch_guard')

    def _prepare_consent(self, ticket, name, schema_sha, trust_check):
        if ticket.consent == 'authorized':
            if trust_check is None or trust_check(name, schema_sha) is not True:
                raise ExternalError('dispatch_guard')
        elif ticket.consent != 'approved':
            raise ExternalError('dispatch_guard')

    @contextmanager
    def _resource_boundary(self, ticket, name, schema_sha):
        """Internal leaf extension; entered after manager/controller locks."""
        yield

    def _before_commit(self, ticket, name, schema_sha):
        """Internal no-I/O leaf extension, before any dispatch marker."""

    @contextmanager
    def dispatch_boundary(self, name, schema_sha, trust_check=None):
        """Yield a single-use commit only inside manager -> controller locks.

        Caller takes the supervisor lock inside this boundary, checks its
        runtime/request, then supplies a no-I/O request marker to commit.
        Exceptions from the protected caller body retain their safe error code.
        Resource atomicity and real delegation accounting are separate P3c work.
        """
        with self._lock:
            if self._admitted:
                raise ExternalError('dispatch_guard')
            marker = CURRENT.set(self._scope)
            try:
                try:
                    if self._scope is not None:
                        self._scope.recheck()
                    ticket = self._manager.admit_external_dispatch(self._event)
                    if ticket != self._ticket:
                        raise ExternalError('dispatch_guard')
                    self._prepare_consent(ticket, name, schema_sha, trust_check)
                except Exception:
                    raise ExternalError('dispatch_guard') from None

                with self._manager._cond:
                    controller = self._manager._sessions
                    if controller is None:
                        raise ExternalError('dispatch_guard')
                    with controller._lock:
                        try:
                            current = self._manager.admit_external_dispatch(self._event)
                            if current != self._ticket:
                                raise ExternalError('dispatch_guard')
                        except Exception:
                            raise ExternalError('dispatch_guard') from None
                        with self._resource_boundary(current, name, schema_sha):
                            active = True
                            def commit(mark_request_dispatched):
                                nonlocal active
                                if (not active or self._admitted
                                        or not callable(mark_request_dispatched)):
                                    raise ExternalError('dispatch_guard')
                                # A commit attempt is single-use, also if the
                                # resource/limiter leaf refuses before marking.
                                active = False
                                self._before_commit(current, name, schema_sha)
                                mark_request_dispatched()
                                self._admitted = True
                                if self._scope is not None:
                                    self._scope.dispatched = True
                            try:
                                yield commit
                            finally:
                                active = False
            finally:
                CURRENT.reset(marker)

    def mark_uncertain_if_admitted(self):
        """Pre-dispatch refusal has no business uncertainty; no replay or retry."""
        with self._lock:
            if self._admitted and self._scope is not None:
                self._scope.uncertain = True
