"""Authenticated-panel decisions. Draft policy and goal/plan stay in RAM only."""
import copy
import json
import secrets
import threading
import time
from dataclasses import asdict

from autopilot.models import BudgetLimits
from autopilot.rules import parse_policy
from server.autopilot_resources import pin_rules, resource_choices, selected_resources
from server.autopilot_catalog import capture_catalog, catalog_choices
from server.panel_timing import measure
from server.text_validation import _clean, _has_secret
from tasks.session_models import SessionConflict

PREVIEW_PREPARATION_SECONDS = 15


class PreviewInProgress(SessionConflict):
    """Another preview still owns the single resource preparation slot."""


class PreviewTimedOut(SessionConflict):
    """Resource preparation finished too late to publish a draft."""


class AutopilotAdmin:
    def __init__(self, runtime):
        self.runtime = runtime
        self.controller = runtime.controller
        self._lock = threading.RLock()
        self._key = secrets.token_bytes(32)
        self._revision = 1
        self._draft = None
        self._approved = None
        self._catalog_lock = threading.Lock()
        self._preview_lock = threading.Lock()

    def _identity(self, body, *, paused=True):
        if self.runtime.manager._stopped:
            raise SessionConflict('Application is stopping.')
        state = self.controller.sessions.snapshot()
        if (type(body.get('revision')) is not int or body['revision'] != state['revision']
                or body.get('session_id') != state['session_id'] or state['session_id'] is None
                or type(body.get('decision_revision')) is not int
                or body['decision_revision'] != self._revision):
            raise SessionConflict('Panel decision changed.')
        if paused:
            self.controller._paused(body['revision'])
        return state

    def status(self):
        with self._lock, self.controller.coordination():
            session = self.controller.sessions.snapshot()
            state = self.controller.snapshot()
            try:
                unresolved = self.controller.audit.unresolved()
            except Exception:
                self.controller._close('storage_error', recovery=True)
                raise
            return dict(state, session_id=session['session_id'], revision=session['revision'],
                        session_state=session['state'], running_actions=session['running_actions'],
                        resume_pending=session.get('resume_pending', False), decision_revision=self._revision,
                        limits=copy.deepcopy(self._approved['limits']) if self._approved else None,
                        goal=self._approved['goal'] if self._approved else '',
                        plan=self._approved['plan'] if self._approved else '',
                        policy=copy.deepcopy(self._approved['document']) if self._approved else None,
                        bindings=copy.deepcopy(self._approved['bindings']) if self._approved else [],
                        deadline_utc=self._approved.get('deadline_utc') if self._approved else None,
                        unresolved=unresolved)

    def discard_session_internal(self):
        """Composition only: caller holds admin lock after a durable session end."""
        self._revision += 1
        self._draft = self._approved = None
        self.controller.resume_scope = None

    def resources(self, timings=None):
        with measure(timings, 'catalog_wait'):
            if not self._catalog_lock.acquire(blocking=False):
                raise SessionConflict('Resource catalogue scan is already running.')
        try:
            with measure(timings, 'catalog_snapshot'):
                with self._lock, self.controller.coordination():
                    state = self.controller.sessions.snapshot()
                    identity = (state['session_id'], state['revision'], self._revision)
                    epoch = self.runtime.generations.revision
                snapshot = capture_catalog(self.runtime)
            choices = catalog_choices(snapshot, timings)
            with measure(timings, 'catalog_validate'):
                with self._lock, self.controller.coordination():
                    state = self.controller.sessions.snapshot()
                    if (self.runtime.manager._stopped
                            or epoch != self.runtime.generations.revision
                            or identity != (state['session_id'], state['revision'], self._revision)):
                        raise SessionConflict('Resource catalogue changed during scan.')
            return choices
        finally:
            self._catalog_lock.release()

    def _prepare(self, body):
        document = copy.deepcopy(body['policy'])
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode('utf-8')) > 65536 or _has_secret(encoded) or self._contains_secret(document):
            raise ValueError('invalid_or_secret_policy')
        goal = _clean(body['goal'], 500)
        plan = _clean(body['plan'], 6000)
        limits = BudgetLimits.from_dict(body['limits'])
        refs, choices = resource_choices(self.runtime, selected_resources(document))
        pinned = pin_rules(document, refs, choices, self.runtime)
        policy = parse_policy(pinned, hmac_key=self._key)
        if not policy.rules:
            raise ValueError('empty_policy')
        for rule in policy.rules:
            if rule.kind == 'mcp_call' and not any(
                    other.kind == 'mcp_discovery' and other.resource == rule.resource
                    and other.max_timeout_s >= rule.max_timeout_s for other in policy.rules):
                raise ValueError('discovery_rule_required')
        bindings = [dict(rule_id=rule.id,
                         resources=[asdict(ref) for ref in ((rule.resource,) if rule.resource else rule.resources)],
                         schema_digest=rule.schema_digest or None)
                    for rule in policy.rules]
        return dict(document=document, policy=policy, limits=asdict(limits), goal=goal, plan=plan, bindings=bindings)

    def preview(self, body, timings=None):
        # A slow filesystem cannot reserve all workers waiting for this editor.
        if not self._preview_lock.acquire(blocking=False):
            raise PreviewInProgress('Preview is already being prepared.')
        try:
            deadline = time.monotonic() + PREVIEW_PREPARATION_SECONDS
            body = copy.deepcopy(body)
            with measure(timings, 'preview_snapshot'):
                with self._lock, self.controller.coordination():
                    self._identity(body)
                    self._draft = None
                    epoch = self.runtime.generations.revision
            # No admin, task manager, session or config lock spans resource I/O.
            with measure(timings, 'preview_resources'):
                prepared = self._prepare(body)
            with measure(timings, 'preview_validate'):
                with self._lock, self.controller.coordination():
                    self._identity(body)
                    if time.monotonic() >= deadline:
                        raise PreviewTimedOut('Preview preparation timed out; no decision was published.')
                    if epoch != self.runtime.generations.revision:
                        raise SessionConflict('Resources changed during preview.')
                    token = secrets.token_urlsafe(32)
                    self._draft = dict(prepared, token=token,
                                       identity=(body['session_id'], body['revision'], self._revision),
                                       expires=time.monotonic() + 300)
                    return dict(preview_id=token, goal=prepared['goal'], plan=prepared['plan'],
                                policy=copy.deepcopy(prepared['document']), bindings=copy.deepcopy(prepared['bindings']),
                                limits=dict(prepared['limits']), expires_in_s=300)
        finally:
            self._preview_lock.release()

    def action(self, action, body):
        with self._lock, self.runtime.manager._cond, self.controller.coordination():
            self._identity(body, paused=action != 'revoke')
            if body.get('confirmed') is not True:
                raise ValueError('confirmation_required')
            # Consume the decision version even if storage fails or is uncertain.
            previous_revision = self._revision
            self._revision += 1
            draft, self._draft = self._draft, None
            if action == 'approve':
                if (draft is None or body.get('preview_id') != draft['token']
                        or draft['identity'] != (body['session_id'], body['revision'], previous_revision)
                        or time.monotonic() >= draft['expires']):
                    raise SessionConflict('Preview expired or changed.')
                with self.runtime.store.lock:
                    current = self._prepare(dict(policy=draft['document'], limits=draft['limits'],
                                                 goal=draft['goal'], plan=draft['plan']))
                    if current['policy'] != draft['policy']:
                        raise SessionConflict('Resources changed; review again.')
                    self.controller.panel_approve(current['policy'], BudgetLimits(**current['limits']), body['revision'])
                    current['deadline_utc'] = time.time() + current['limits']['duration_s']
                    self._approved = current
                    self.controller.resume_scope = dict(
                        goal=current['goal'], plan=current['plan'], limits=dict(current['limits']),
                        rules=[dict(id=rule.id, kind=rule.kind, tools=list(rule.tools),
                                    scope=asdict(rule.scope) if rule.scope else None,
                                    resource=self._resource_label(raw), overwrite=rule.overwrite,
                                    max_timeout_s=rule.max_timeout_s)
                               for rule, raw in zip(current['policy'].rules, current['document']['rules'])])
            elif action == 'reconfirm':
                if self._approved is None:
                    raise SessionConflict('No approved contract.')
                with self.runtime.store.lock:
                    current = self._prepare(dict(policy=self._approved['document'], limits=self._approved['limits'],
                                                 goal=self._approved['goal'], plan=self._approved['plan']))
                    if current['policy'] != self._approved['policy']:
                        raise SessionConflict('Resources changed; review again.')
                    self.controller.panel_reconfirm(body['revision'])
            elif action == 'revoke':
                self.controller.revoke()
            elif action == 'manual':
                self.controller.panel_manual(body['revision'])
                self._approved = None
                self.controller.resume_scope = None
            elif action == 'resolve':
                self.controller.panel_resolve(body['operation_id'], body['revision'])
            elif action == 'acknowledge-recovery':
                self.controller.panel_acknowledge_recovery(body['revision'])
            else:
                raise ValueError('unknown_action')
            return self.status()

    @staticmethod
    def _resource_label(rule):
        return next((rule[key] for key in ('root', 'bundle', 'skill', 'profile', 'resources') if key in rule), None)

    @classmethod
    def _contains_secret(cls, value):
        if type(value) is str:
            return _has_secret(value)
        if type(value) is list:
            return any(cls._contains_secret(item) for item in value)
        if type(value) is dict:
            return any(cls._contains_secret(key) or cls._contains_secret(item)
                       or type(item) is str and _has_secret(key + '=' + item)
                       for key, item in value.items())
        return False
