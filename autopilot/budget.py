"""Detached RAM accounting. Never grants file/tool access or dispatches work.

Integration must provide panel authorization, validated operation scope and a
durable audit before any real effect. No watchdog, session callbacks or I/O.
Tokens belong to one instance and must never be exposed as model authority.
"""
import math
import threading
import time
from dataclasses import asdict

from autopilot.models import BudgetLimits, ContractBinding


class BudgetError(ValueError):
    """Fixed codes only; no input, token or underlying exception text."""


def _timestamp(value):
    try:
        return type(value) in (int, float) and value >= 0 and math.isfinite(value)
    except (OverflowError, ValueError):
        return False


class BudgetLedger:
    def __init__(self, binding, limits, *, approved_at, clock=time.monotonic):
        if type(binding) is not ContractBinding or type(limits) is not BudgetLimits:
            raise BudgetError('invalid_model')
        if not callable(clock) or not _timestamp(approved_at):
            raise BudgetError('invalid_clock')
        try:
            now = clock()
        except Exception:
            raise BudgetError('clock_unavailable') from None
        if not _timestamp(now) or now < approved_at:
            raise BudgetError('invalid_clock')
        deadline = approved_at + limits.duration_s
        if not _timestamp(deadline) or deadline <= approved_at:
            raise BudgetError('invalid_deadline')
        self._binding, self._limits = binding, limits
        self._clock, self._deadline, self._last_now = clock, deadline, now
        self._state = 'prepared'
        self._used = 0
        self._reserved = set()
        self._running = set()
        self._lock = threading.RLock()

    def _check_binding(self, binding):
        if type(binding) is not ContractBinding or binding != self._binding:
            raise BudgetError('binding_mismatch')

    def _refresh(self):
        valid = (
            type(self._used) is int and 0 <= self._used <= self._limits.max_operations
            and type(self._reserved) is set and type(self._running) is set
            and all(type(token) is object for token in self._reserved | self._running)
            and not self._reserved & self._running
            and self._used + len(self._reserved) <= self._limits.max_operations
            and len(self._running) <= self._limits.max_running
            and len(self._running) <= self._used
            and type(self._state) is str
            and self._state in {'prepared', 'active', 'suspended', 'revoked',
                                'expired', 'exhausted', 'recovery_required'}
        )
        if not valid:
            self._state = 'recovery_required'
            raise BudgetError('recovery_required')
        try:
            now = self._clock()
            if not _timestamp(now) or now < self._last_now:
                raise ValueError
        except Exception:
            self._state = 'recovery_required'
            return
        self._last_now = now
        if self._state in {'prepared', 'active', 'suspended'}:
            if now >= self._deadline:
                self._state = 'expired'
            elif self._used == self._limits.max_operations:
                self._state = 'exhausted'

    def _require_active(self):
        self._refresh()
        if self._state != 'active':
            raise BudgetError(self._state)

    @staticmethod
    def _require_token(token, collection):
        if type(token) is not object or token not in collection:
            raise BudgetError('invalid_token')

    def activate(self, binding):
        with self._lock:
            self._check_binding(binding)
            self._refresh()
            if self._state not in {'prepared', 'suspended', 'active'}:
                raise BudgetError(self._state)
            self._state = 'active'

    def suspend(self, binding):
        with self._lock:
            self._check_binding(binding)
            self._refresh()
            if self._state in {'prepared', 'active'}:
                self._state = 'suspended'

    def revoke(self, binding):
        with self._lock:
            self._check_binding(binding)
            self._refresh()
            if self._state != 'recovery_required':
                self._state = 'revoked'

    def reserve(self, binding):
        with self._lock:
            self._check_binding(binding)
            self._require_active()
            if self._used + len(self._reserved) >= self._limits.max_operations:
                raise BudgetError('budget_reserved')
            token = object()
            self._reserved.add(token)
            return token

    def start(self, binding, token):
        with self._lock:
            self._check_binding(binding)
            self._require_active()
            self._require_token(token, self._reserved)
            if len(self._running) >= self._limits.max_running:
                raise BudgetError('busy')
            self._reserved.remove(token)
            self._running.add(token)
            self._used += 1
            if self._used == self._limits.max_operations:
                self._state = 'exhausted'

    def finish(self, binding, token):
        with self._lock:
            self._check_binding(binding)
            self._refresh()
            self._require_token(token, self._running)
            self._running.remove(token)

    def release(self, binding, token):
        with self._lock:
            self._check_binding(binding)
            self._refresh()
            self._require_token(token, self._reserved)
            self._reserved.remove(token)

    def snapshot(self):
        with self._lock:
            self._refresh()
            return {
                'binding': asdict(self._binding),
                'state': self._state,
                'execution_blocked': self._state != 'active',
                'used': self._used,
                'reserved': len(self._reserved),
                'running': len(self._running),
                'remaining_operations': self._limits.max_operations - self._used - len(self._reserved),
                'remaining_s': (0 if self._state == 'recovery_required'
                                else max(0, self._deadline - self._last_now)),
            }
