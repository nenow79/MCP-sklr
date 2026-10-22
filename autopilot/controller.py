"""Server-only delegation coordinator; no panel API, config opt-in or model authority.

Order: manager -> session -> coordinator -> write -> config -> resources ->
budget/journal. Never call manager from here. No control lock spans an effect.
"""
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

from autopilot.audit_models import AuditEvent, RESULTS
from autopilot.budget import BudgetError, BudgetLedger
from autopilot.matcher import evaluate
from autopilot.models import BudgetLimits, ContractBinding
from autopilot.rules import Policy
from tasks.session_models import SessionConflict


class AutopilotBlocked(RuntimeError):
    def __init__(self, code='recovery_required'):
        self.code = code if code in RESULTS else 'recovery_required'
        super().__init__(self.code)


class AutopilotBusy(AutopilotBlocked):
    pass


@dataclass(eq=False, repr=False)
class Ticket:
    binding: ContractBinding
    operation_id: str
    tool: str
    token: object = field(repr=False)
    refresh: object = field(repr=False)
    ledger: object = field(repr=False)
    rule_ids: tuple = ()
    task_id: int | None = None
    phase: str = 'reserved'


class AutopilotController:
    def __init__(self, sessions, audit, *, clock=time.monotonic):
        if sessions is None or getattr(sessions, '_autopilot', None) is not None:
            raise ValueError('autopilot_requires_unique_session_controller')
        self.sessions, self.audit, self.clock = sessions, audit, clock
        self._lock = threading.RLock()
        self._ledger = self._policy = self._binding = None
        self._tickets = set()
        self._engaged = False
        self._approved = False
        self._reason = ''
        self._recovery = bool(audit.unresolved())
        self.runtime = None
        self.resume_scope = None
        self._stop = threading.Event()
        self._watcher = None
        self._closing = False
        self._ticks = 0
        sessions._autopilot = self
        if self._recovery:
            sessions._blocked = True

    @contextmanager
    def coordination(self):
        with self.sessions._lock:
            with self._lock:
                yield

    @property
    def engaged(self):
        with self.coordination():
            return self._engaged or self._recovery

    @property
    def binding(self):
        with self.coordination():
            return self._binding

    def _record(self, event, code='none', ticket=None):
        if self._binding is None:
            return
        try:
            state = (ticket.ledger if ticket else self._ledger).snapshot()
            self.audit.append(AuditEvent(
                ticket.binding if ticket else self._binding, event, state['state'], code,
                operation_id=ticket.operation_id if ticket else None,
                task_id=ticket.task_id if ticket else None,
                tool=ticket.tool if ticket else None,
                rule_ids=ticket.rule_ids if ticket else (),
                used=state['used'], reserved=state['reserved'], running=state['running']))
        except Exception:
            self._close('storage_error', recovery=True)
            raise AutopilotBlocked('storage_error') from None

    def _close(self, code, *, recovery=False):
        # Caller holds session -> coordinator. No manager callback.
        # Close the latch before inspecting potentially inconsistent stores.
        self.sessions._blocked = True
        self.sessions._invalidate_resume()
        if not recovery and self._ledger is not None:
            try:
                terminal = self._ledger.snapshot()['state']
                if terminal in {'revoked', 'expired', 'exhausted', 'recovery_required'}:
                    code = terminal
            except Exception:
                code, recovery = 'recovery_required', True
        recovery = recovery or code == 'recovery_required'
        self._reason, self._approved = code, False
        if recovery:
            self._recovery = True
            if self._ledger is not None:
                self._ledger.block()
        elif self._ledger is not None:
            try:
                self._ledger.suspend(self._binding)
            except Exception:
                self._recovery = True
                self._ledger.block()
        try:
            state = self.sessions.snapshot()
        except Exception:
            self._recovery = True
            if self._ledger is not None:
                self._ledger.block()
            return
        if state['session_id'] is not None and state['state'] == 'active':
            try:
                self._closing = True
                self.sessions.request_pause(state['revision'])
            except Exception:
                self._recovery = True
                if self._ledger is not None:
                    self._ledger.block()
            finally:
                self._closing = False

    def stop(self, code='recovery_required', *, uncertain=False):
        with self.coordination():
            self._close(code, recovery=uncertain)
            self._record('blocked', code)

    def _paused(self, expected_revision):
        state = self.sessions.snapshot()
        if (state['revision'] != expected_revision or state['state'] != 'paused'
                or state['running_actions'] or state.get('resume_pending')):
            raise SessionConflict('Autopilot decision requires a current fully paused session.')
        return state

    def panel_approve(self, policy, limits, expected_revision):
        """Internal authenticated-panel seam, intentionally not registered in A2b3."""
        with self.coordination():
            state = self._paused(expected_revision)
            if self._recovery or self.audit.unresolved() or any(t.phase == 'started' for t in self._tickets):
                raise AutopilotBlocked('recovery_required')
            if type(policy) is not Policy or type(limits) is not BudgetLimits:
                raise ValueError('invalid_contract')
            binding = ContractBinding(secrets.token_hex(16), state['session_id'],
                                      1, expected_revision + 2, policy.digest)
            ledger = BudgetLedger(binding, limits, approved_at=self.clock(), clock=self.clock)
            self._binding, self._ledger = binding, ledger
            self._policy, self._engaged, self._approved = policy, True, True
            self._reason = ''
            self.sessions._invalidate_resume()
            self._record('prepared')
            return self._binding

    def panel_reconfirm(self, expected_revision):
        with self.coordination():
            self._paused(expected_revision)
            if self._recovery or self._ledger is None or self._ledger.snapshot()['state'] not in {'prepared', 'suspended'}:
                raise AutopilotBlocked('recovery_required')
            self._approved = True
            self._record('prepared')

    def panel_manual(self, expected_revision):
        with self.coordination():
            self._paused(expected_revision)
            if self._recovery or self.audit.unresolved() or any(t.phase == 'started' for t in self._tickets):
                raise AutopilotBlocked('recovery_required')
            if self._ledger is not None:
                self._ledger.revoke(self._binding)
                self._record('manual')
            self._engaged = self._approved = False
            self.sessions._invalidate_resume()

    def panel_acknowledge_recovery(self, expected_revision):
        """Explicit resolution of a storage/RAM fault without an operation row."""
        with self.coordination():
            self._paused(expected_revision)
            if self._tickets or self.audit.unresolved():
                raise AutopilotBlocked('recovery_required')
            # Verify that storage is usable. This never restores the old ledger.
            self.audit.purge()
            self._recovery = False
            self._approved = False

    def panel_resolve(self, operation_id, expected_revision):
        """Acknowledge an uncertain effect, never execute/retry it. A2b4 owns auth."""
        with self.coordination():
            self._paused(expected_revision)
            if self._tickets:
                raise AutopilotBlocked('busy')
            row = next((row for row in self.audit.unresolved() if row['operation_id'] == operation_id), None)
            if row is None:
                raise ValueError('unknown_uncertain_operation')
            data = dict(row)
            data.update(binding=ContractBinding.from_dict(data['binding']), event='resolved', code='ok',
                        rule_ids=tuple(data['rule_ids']))
            self.audit.append(AuditEvent(**data))
            self._recovery = bool(self.audit.unresolved())
            # Remain blocked; fresh explicit delegation or manual decision required.

    def revoke(self):
        with self.coordination():
            if self._ledger is None:
                return
            self._ledger.revoke(self._binding)
            self._close('revoked')
            self._record('revoked', 'revoked')

    def session_ended_internal(self):
        """Discard delegation without creating a pause/checkpoint cycle."""
        with self._lock:
            self._engaged = self._approved = False
            if self._ledger is not None:
                self._ledger.revoke(self._binding)
            # Keep tickets/journal and uncertainty until started effects finish.
            self._reason = 'revoked'

    def session_paused_internal(self):
        # Called with session lock. Do not recurse into request_pause.
        with self._lock:
            if self._engaged and not self._closing:
                self._approved = False
                if self._ledger is not None:
                    self._ledger.suspend(self._binding)
                self._reason = self._reason or 'suspended'
                self._record('suspended', 'suspended')

    def resume_prepared_internal(self, revision):
        with self._lock:
            if self._recovery:
                raise AutopilotBlocked('recovery_required')
            if not self._engaged:
                return None
            if not self._approved:
                raise AutopilotBlocked('suspended')
            binding = replace(self._binding, run_revision=revision + 1)
            if binding != self._binding:
                self._ledger.bind_run(binding)
                self._binding = binding
            return binding

    def confirm_internal(self, binding):
        with self._lock:
            if self._recovery or (self._engaged and (not self._approved or binding != self._binding)):
                raise AutopilotBlocked('binding_mismatch')
            if not self._engaged:
                if binding is not None:
                    raise AutopilotBlocked('binding_mismatch')
                return
            self._ledger.activate(binding)
            self._record('activated')
            self._reason = ''

    def _active(self):
        if self._recovery or self._ledger is None:
            raise AutopilotBlocked('recovery_required')
        try:
            state = self._ledger.snapshot()
        except Exception:
            self._close('recovery_required', recovery=True)
            raise AutopilotBlocked('recovery_required') from None
        if state['state'] != 'active':
            self._close(state['state'], recovery=state['state'] == 'recovery_required')
            raise AutopilotBlocked(state['state'])
        try:
            session = self.sessions.snapshot()
        except Exception:
            self._close('storage_error', recovery=True)
            raise AutopilotBlocked('storage_error') from None
        if (session['execution_blocked'] or session['session_id'] != self._binding.session_id
                or session['revision'] != self._binding.run_revision):
            raise AutopilotBlocked('session_blocked')

    def _evaluate(self, refresh):
        try:
            operation, snapshot = refresh(self._binding)
            decision = evaluate(self._policy, operation, snapshot, self._binding)
        except AutopilotBlocked as exc:
            self._close(exc.code)
            self._record('blocked', exc.code)
            raise
        except Exception:
            decision = None
        if decision is None or not decision.allowed:
            code = decision.code if decision else 'invalid_operation'
            self._close(code)
            self._record('blocked', code)
            raise AutopilotBlocked(code)
        return operation, decision

    def reserve(self, refresh):
        with self.coordination():
            self._active()
            operation, decision = self._evaluate(refresh)
            try:
                token = self._ledger.reserve(self._binding)
            except BudgetError as exc:
                self._close(str(exc))
                raise AutopilotBlocked(str(exc)) from None
            ticket = Ticket(self._binding, secrets.token_hex(16), operation.tool, token, refresh,
                            self._ledger, decision.matched_rule_ids)
            self._tickets.add(ticket)
            try:
                self._record('reserved', ticket=ticket)
            except Exception:
                try:
                    self._ledger.release(self._binding, ticket.token)
                finally:
                    ticket.phase = 'released'
                    self._tickets.discard(ticket)
                raise
            return ticket

    def associate(self, ticket, task_id):
        with self.coordination():
            self._require(ticket, 'reserved')
            if ticket.task_id is not None:
                raise AutopilotBlocked('binding_mismatch')
            ticket.task_id = task_id
            self._record('reserved', ticket=ticket)

    def _require(self, ticket, phase, *, current=True):
        if (type(ticket) is not Ticket or ticket not in self._tickets
                or current and ticket.binding != self._binding or ticket.phase != phase):
            raise AutopilotBlocked('binding_mismatch')

    def start_internal(self, ticket):
        """Caller holds manager/session/coordinator and applicable resource locks."""
        self._require(ticket, 'reserved')
        self._active()
        _, decision = self._evaluate(ticket.refresh)
        try:
            self._ledger.start(self._binding, ticket.token)
        except BudgetError as exc:
            if str(exc) == 'busy':
                raise AutopilotBusy('busy') from None
            self._close(str(exc))
            raise AutopilotBlocked(str(exc)) from None
        ticket.phase, ticket.rule_ids = 'started', decision.matched_rule_ids
        self._record('started', 'allowed', ticket)
        # Last operation is admitted before closing the next-operation gate.
        if self._ledger.snapshot()['state'] == 'exhausted':
            self._close('exhausted')
            self._record('exhausted', 'exhausted')

    def recheck_started(self, ticket):
        """After MCP discovery: preserve admission, repeat safety/scope/trust checks."""
        with self.coordination():
            self._require(ticket, 'started')
            self._evaluate(ticket.refresh)

    def release(self, ticket):
        with self.coordination():
            self._require(ticket, 'reserved', current=False)
            try:
                ticket.ledger.release(ticket.binding, ticket.token)
                ticket.phase = 'released'
                self._tickets.remove(ticket)
                self._record('released', ticket=ticket)
            except Exception:
                self._close('storage_error', recovery=True)
                raise AutopilotBlocked('storage_error') from None

    def finish(self, ticket, code='ok'):
        with self.coordination():
            self._require(ticket, 'started')
            try:
                self._ledger.finish(self._binding, ticket.token)
                self._record('finished', code, ticket)
                ticket.phase = 'finished'
                self._tickets.remove(ticket)
            except Exception:
                # The executor is finished even when journal publication is
                # uncertain. Durable unresolved metadata remains protected.
                ticket.phase = 'finished'
                self._tickets.discard(ticket)
                self._close('storage_error', recovery=True)
                raise AutopilotBlocked('storage_error') from None
            if code == 'uncertain':
                self._close('recovery_required', recovery=True)

    def snapshot(self):
        with self.coordination():
            if self._ledger is None:
                return {'state': 'recovery_required' if self._recovery else 'disabled',
                        'reason': self._reason, 'execution_blocked': self._recovery,
                        'contract_id': None, 'contract_revision': None,
                        'used': 0, 'reserved': 0, 'running': 0,
                        'remaining_operations': 0, 'remaining_s': 0}
            try:
                state = self._ledger.snapshot()
            except Exception:
                self._close('recovery_required', recovery=True)
                state = {'state': 'recovery_required', 'execution_blocked': True,
                         'used': 0, 'reserved': 0, 'running': 0,
                         'remaining_operations': 0, 'remaining_s': 0}
            return {key: value for key, value in state.items() if key != 'binding'} | {
                'state': 'recovery_required' if self._recovery else state['state'] if self._engaged else 'disabled',
                'execution_blocked': self._recovery or (self._engaged and state['execution_blocked']),
                'reason': self._reason, 'contract_id': self._binding.contract_id,
                'contract_revision': self._binding.contract_revision}

    def tick(self):
        with self.coordination():
            if self._engaged and self._ledger is not None:
                state = self._ledger.snapshot()['state']
                if state in {'expired', 'exhausted', 'recovery_required'} and self._reason != state:
                    self._close(state, recovery=state == 'recovery_required')
                    self._record(state if state != 'recovery_required' else 'blocked', state)
            try:
                # Appends enforce retention themselves; idle cleanup need not
                # start a SQLite transaction on every 250 ms expiry tick.
                if self._ticks % 240 == 0:
                    self.audit.purge()
                self._ticks += 1
            except Exception:
                self._close('storage_error', recovery=True)
                raise AutopilotBlocked('storage_error') from None

    def start_watchdog(self):
        with self.coordination():
            if self._watcher is not None or self._stop.is_set():
                raise ValueError('watchdog_already_started')
            def watch():
                while not self._stop.wait(0.25):
                    try:
                        self.tick()
                    except Exception:
                        with self.coordination():
                            self._close('storage_error', recovery=True)
            self._watcher = threading.Thread(target=watch, daemon=True, name='autopilot-expiry')
            self._watcher.start()

    def stop_watchdog(self, timeout=1):
        """Bounded lifecycle stop; leave storage alive for started operations."""
        self._stop.set()
        if self._watcher is not None:
            self._watcher.join(timeout=timeout)
            return not self._watcher.is_alive()
        return True

    def close(self):
        if not self.stop_watchdog():
            return False
        with self.coordination():
            if self._engaged:
                self._close('shutdown')
            if self._tickets or self.sessions._tokens:
                return False
            self.audit.close()
            return True
