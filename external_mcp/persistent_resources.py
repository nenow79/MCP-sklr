"""Persistent resource admission; manual M1 adds its own control/lifecycle gate.

ConfigStore is retained only across final checks and local dispatch commit.
No transport, schema worker, slot waiting or cleanup under this resource lock.
"""
import copy
from contextlib import contextmanager

from external_mcp.limits import ExternalError
from external_mcp.persistent_admission import DispatchAdmission
from external_mcp.trust import trusted_decision


class ResourceDispatchAdmission(DispatchAdmission):
    def __init__(self, manager, event, scope, *, registry, snapshot,
                 tool_name, schema_digest, limiter, panel_available):
        # Only server-owned inputs: never infer a grant from a model snapshot.
        self._registry = registry
        self._snapshot = copy.deepcopy(snapshot)
        self._tool_name = tool_name
        self._schema_digest = schema_digest
        self._limiter = limiter
        self._panel_available = panel_available
        self._resource_active = False
        if not callable(panel_available) or not callable(getattr(limiter, 'allow', None)):
            raise ExternalError('dispatch_guard')
        super().__init__(manager, event, scope)

    def guard(self, name, schema_sha, trust_check=None):
        # The legacy guard does not retain resources through supervisor commit.
        # Resource-aware callers must use the coordinated dispatch boundary.
        raise ExternalError('dispatch_guard')

    def _prepare_consent(self, ticket, name, schema_sha, trust_check):
        # No earlier trust snapshot is accepted as the final grant. Actual trust
        # is checked below under ConfigStore and retained through the commit.
        if ticket.consent not in ('approved', 'authorized'):
            raise ExternalError('dispatch_guard')

    def _check_resources(self, ticket, name, schema_sha):
        """Local leaf checks only; caller owns ConfigStore after control locks."""
        try:
            if self._panel_available() is not True:
                raise ExternalError('panel_unavailable')
            if name != self._tool_name or schema_sha != self._schema_digest:
                raise ExternalError('schema_changed')
            pinned = self._snapshot['profile']
            current = self._registry.resolve(
                pinned['alias'], expected_revision=self._snapshot['revision'])['profile']
            if (current['id'] != pinned['id'] or current['revision'] != pinned['revision']
                    or current['transport'] != 'stdio'
                    or current.get('stdio_mode') != 'persistent'):
                raise ExternalError('profile_changed')
            if ticket.consent == 'authorized':
                if not trusted_decision(current, name, schema_sha)['trusted']:
                    raise ExternalError('trust_revoked')
            elif ticket.consent != 'approved':
                raise ExternalError('dispatch_guard')
            return current['id']
        except ExternalError:
            raise
        except Exception:
            # Registry/storage failures never leak configuration or exceptions.
            raise ExternalError('dispatch_guard') from None

    @contextmanager
    def _resource_boundary(self, ticket, name, schema_sha):
        # Order: manager -> controller -> config -> supervisor. Do not call
        # back into manager/controller or install lifecycle listeners here.
        with self._registry.store.lock:
            self._check_resources(ticket, name, schema_sha)
            self._resource_active = True
            try:
                yield
            finally:
                self._resource_active = False

    def _before_commit(self, ticket, name, schema_sha):
        if not self._resource_active:
            raise ExternalError('dispatch_guard')
        # Recheck also catches same-thread reentrant configuration changes. No
        # control callback and no resource checks after a successful rate charge.
        profile_id = self._check_resources(ticket, name, schema_sha)
        if ticket.consent == 'authorized':
            try:
                allowed = self._limiter.allow(profile_id)
            except Exception:
                raise ExternalError('dispatch_guard') from None
            if allowed is not True:
                # Proven pre-dispatch refusal. Adapter may return this exact
                # task to approval; this class never resubmits or retries calls.
                raise ExternalError('approval_required')
