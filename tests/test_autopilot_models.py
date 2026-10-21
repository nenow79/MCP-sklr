"""Detached A2 identity/limit validation; no application startup or I/O."""
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from autopilot.models import BudgetLimits, ContractBinding


def binding():
    return ContractBinding('a' * 32, 'b' * 32, 1, 1, 'c' * 64)


@pytest.mark.parametrize('field,length', [
    ('contract_id', 32), ('session_id', 32), ('rules_digest', 64),
])
@pytest.mark.parametrize('bad', [None, True, 1, [], {}, '', 'PRIVATE', 'A', 'g', '\n'])
def test_binding_rejects_invalid_identifier(field, length, bad):
    value = bad * length if type(bad) is str and len(bad) == 1 else bad
    with pytest.raises(ValueError) as exc:
        replace(binding(), **{field: value})
    assert str(exc.value) == 'invalid_binding_identifier'


@pytest.mark.parametrize('field', ['contract_revision', 'run_revision'])
@pytest.mark.parametrize('bad', [True, False, 0, -1, 1.0, float('nan'), float('inf'), '1', None])
def test_binding_rejects_non_positive_integer_revision(field, bad):
    with pytest.raises(ValueError):
        replace(binding(), **{field: bad})


@pytest.mark.parametrize('field,maximum', [('duration_s', 7200), ('max_operations', 500), ('max_running', 1)])
@pytest.mark.parametrize('bad', [True, False, 0, -1, 1.0, float('nan'), float('inf'), '1', None])
def test_limits_reject_invalid_types_and_values(field, maximum, bad):
    with pytest.raises(ValueError):
        BudgetLimits(**{field: bad})


@pytest.mark.parametrize('field,maximum', [('duration_s', 7200), ('max_operations', 500), ('max_running', 1)])
def test_limit_boundaries(field, maximum):
    assert getattr(BudgetLimits(**{field: 1}), field) == 1
    assert getattr(BudgetLimits(**{field: maximum}), field) == maximum
    with pytest.raises(ValueError):
        BudgetLimits(**{field: maximum + 1})


def test_models_roundtrip_and_are_immutable():
    value = binding()
    assert ContractBinding.from_dict(asdict(value)) == value
    assert BudgetLimits.from_dict({}) == BudgetLimits(1800, 100, 1)
    for model, field, replacement in [(value, 'run_revision', 2), (BudgetLimits(), 'max_operations', 2)]:
        with pytest.raises(FrozenInstanceError):
            setattr(model, field, replacement)


@pytest.mark.parametrize('model,value', [
    (ContractBinding, {}),
    (ContractBinding, {**asdict(binding()), 'PRIVATE': 'SECRET'}),
    (BudgetLimits, {'timeout': 3600}),
    (BudgetLimits, {'PRIVATE': 'SECRET'}),
    (BudgetLimits, []),
    (BudgetLimits, None),
])
def test_closed_mapping_validation_has_fixed_errors(model, value):
    with pytest.raises(ValueError) as exc:
        model.from_dict(value)
    assert str(exc.value) == 'invalid_model_fields'
    assert 'SECRET' not in str(exc.value)


def test_constructor_rejects_unknown_fields():
    with pytest.raises(TypeError):
        BudgetLimits(timeout=3600)
