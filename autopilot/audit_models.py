"""Closed, content-free audit records. Never serialize operation descriptors."""
import re
from dataclasses import dataclass

from autopilot.models import ContractBinding
from autopilot.rules import TOOLS

STATES = frozenset({'prepared', 'active', 'suspended', 'revoked', 'expired',
                    'exhausted', 'recovery_required', 'disabled'})
EVENTS = frozenset({'prepared', 'activated', 'suspended', 'revoked', 'expired',
                    'exhausted', 'blocked', 'reserved', 'released', 'started',
                    'finished', 'recovered', 'resolved', 'manual'})
RESULTS = frozenset({'ok', 'failed', 'cancelled', 'uncertain', 'none', 'allowed',
                     'invalid_operation', 'binding_mismatch', 'policy_mismatch',
                     'resource_changed', 'access_denied', 'sensitive_operation',
                     'manual_required', 'tool_not_allowed', 'scope_mismatch',
                     'arguments_mismatch', 'timeout_exceeded', 'trust_required',
                     'storage_error', 'recovery_required', 'busy', 'expired',
                     'exhausted', 'revoked', 'suspended', 'budget_reserved',
                     'session_blocked', 'limiter', 'shutdown'})
WORK_TOOLS = frozenset().union(*TOOLS.values())


@dataclass(frozen=True, slots=True)
class AuditEvent:
    binding: ContractBinding
    event: str
    state: str
    code: str = 'none'
    operation_id: str | None = None
    task_id: int | None = None
    tool: str | None = None
    rule_ids: tuple = ()
    used: int = 0
    reserved: int = 0
    running: int = 0

    def __post_init__(self):
        if (type(self.binding) is not ContractBinding
                or type(self.event) is not str or self.event not in EVENTS
                or type(self.state) is not str or self.state not in STATES
                or type(self.code) is not str or self.code not in RESULTS):
            raise ValueError('invalid_audit_event')
        if self.operation_id is not None and (type(self.operation_id) is not str
                or re.fullmatch('[0-9a-f]{32}', self.operation_id) is None):
            raise ValueError('invalid_audit_event')
        if self.task_id is not None and (type(self.task_id) is not int or self.task_id < 1):
            raise ValueError('invalid_audit_event')
        if self.tool is not None and (type(self.tool) is not str or self.tool not in WORK_TOOLS):
            raise ValueError('invalid_audit_event')
        if (type(self.rule_ids) is not tuple or len(self.rule_ids) > 128
                or any(type(item) is not str or re.fullmatch('[a-z0-9_-]{1,64}', item) is None
                       for item in self.rule_ids)):
            raise ValueError('invalid_audit_event')
        if any(type(value) is not int or value < 0 for value in (self.used, self.reserved, self.running)):
            raise ValueError('invalid_audit_event')
        if self.used + self.reserved > 500 or self.running > 1 or self.running > self.used:
            raise ValueError('invalid_audit_event')
        if self.event in {'started', 'finished', 'resolved'} and self.operation_id is None:
            raise ValueError('invalid_audit_event')
