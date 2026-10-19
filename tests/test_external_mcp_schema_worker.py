"""Worker policy/validation tests in-process; no subprocess, filesystem or network."""
import copy

import pytest

from external_mcp.schema_worker import DIALECT, check_policy, validate_payload


@pytest.mark.parametrize('arguments, code', [
    ({'q': 'polskie żółć'}, 'ok'),
    ({}, 'arguments_schema'),
    ({'q': 123}, 'arguments_schema'),
    ({'q': 'x', 'extra': True}, 'arguments_schema'),
])
def test_object_constraints(arguments, code):
    schema = {'type': 'object', 'required': ['q'], 'additionalProperties': False,
              'properties': {'q': {'type': 'string'}}}
    before = copy.deepcopy(arguments)
    assert validate_payload(schema, arguments) == code
    assert arguments == before


def test_local_definitions_and_no_default_insertion():
    schema = {'$schema': DIALECT, 'type': 'object',
              '$defs': {'value': {'type': 'integer'}},
              'properties': {'n': {'$ref': '#/$defs/value'},
                             'optional': {'type': 'string', 'default': 'not-inserted'}}}
    arguments = {'n': 3}
    assert validate_payload(schema, arguments) == 'ok'
    assert arguments == {'n': 3}
    assert validate_payload(schema, {'n': '3'}) == 'arguments_schema'


@pytest.mark.parametrize('ref', [
    'https://example.test/schema', 'file:///private/schema.json',
    'other.json#/$defs/value', '//example.test/schema', '#anchor', '', '#',
])
def test_external_and_non_pointer_refs_are_blocked(ref):
    assert validate_payload({'$ref': ref}, {}) == 'schema_reference_blocked'


@pytest.mark.parametrize('key', [
    '$id', '$anchor', '$dynamicAnchor', '$dynamicRef', '$recursiveRef', '$recursiveAnchor',
])
def test_resource_ids_and_dynamic_resolution_blocked(key):
    schema = {'type': 'object', '$defs': {'x': {key: 'external'}}}
    assert check_policy(schema) == 'schema_reference_blocked'


def test_dialect_is_explicit_and_invalid_schema_rejected():
    assert validate_payload({'$schema': DIALECT + '#'}, {}) == 'ok'
    assert validate_payload({'$schema': 'http://json-schema.org/draft-07/schema#'}, {}) == 'schema_dialect'
    assert validate_payload({'type': 'PRIVATE_INVALID_TYPE'}, {}) == 'schema_invalid'


def test_format_not_asserted_and_boolean_not_integer():
    schema = {'type': 'object', 'properties': {'n': {'type': 'integer'},
              'email': {'type': 'string', 'format': 'email'}}}
    assert validate_payload(schema, {'n': True}) == 'arguments_schema'
    assert validate_payload(schema, {'email': 'not-an-email'}) == 'ok'


def test_unresolved_pointer_fails_with_safe_code():
    code = validate_payload({'$ref': '#/$defs/PRIVATE_MISSING'}, {'secret': 'PRIVATE_VALUE'})
    assert code == 'schema_validation_failed'
    assert 'PRIVATE' not in code


def test_nested_constraints():
    schema = {'type': 'object', 'required': ['items'], 'properties': {
        'items': {'type': 'array', 'minItems': 1, 'items': {
            'anyOf': [{'type': 'null'}, {'type': 'number', 'minimum': 0}]}}}}
    assert validate_payload(schema, {'items': [None, 2]}) == 'ok'
    assert validate_payload(schema, {'items': [-1]}) == 'arguments_schema'
    assert validate_payload(schema, {'items': []}) == 'arguments_schema'
