"""Immutable identity and budget parameters; no operation authorization or I/O."""
import re
from dataclasses import dataclass, fields


def _integer(value, maximum=None):
    if type(value) is not int or value < 1 or maximum is not None and value > maximum:
        raise ValueError('invalid_budget_integer')


def _hex(value, length):
    if type(value) is not str or re.fullmatch('[0-9a-f]{%d}' % length, value) is None:
        raise ValueError('invalid_binding_identifier')


def _from_dict(model, value):
    if type(value) is not dict or set(value) - {item.name for item in fields(model)}:
        raise ValueError('invalid_model_fields')
    try:
        return model(**value)
    except TypeError:
        raise ValueError('invalid_model_fields') from None


@dataclass(frozen=True, slots=True)
class ContractBinding:
    contract_id: str
    session_id: str
    contract_revision: int
    run_revision: int
    rules_digest: str

    def __post_init__(self):
        _hex(self.contract_id, 32)
        _hex(self.session_id, 32)
        _hex(self.rules_digest, 64)
        _integer(self.contract_revision)
        _integer(self.run_revision)

    @classmethod
    def from_dict(cls, value):
        return _from_dict(cls, value)


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    duration_s: int = 1800
    max_operations: int = 100
    max_running: int = 1

    def __post_init__(self):
        _integer(self.duration_s, 7200)
        _integer(self.max_operations, 500)
        _integer(self.max_running, 1)

    @classmethod
    def from_dict(cls, value):
        return _from_dict(cls, value)
