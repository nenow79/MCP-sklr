"""Worker policy/validation tests in-process; no subprocess, filesystem or network."""
import copy

import pytest

from external_mcp.schema_worker import DIALECT, check_policy, validate_payload

DRAFT7 = 'http://json-schema.org/draft-07/schema'


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
    assert validate_payload({'$schema': 'http://json-schema.org/draft-04/schema#'}, {}) == 'schema_dialect'
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


@pytest.mark.parametrize('dialect', [DRAFT7, DRAFT7 + '#'])
def test_draft7_local_refs_tuple_items_and_no_mutation(dialect):
    schema = {'$schema': dialect, 'type': 'object',
              'definitions': {'number': {'type': 'integer'}},
              'properties': {
                  'items': {'type': 'array', 'items': [
                      {'$ref': '#/definitions/number'}, {'type': 'string'}],
                      'additionalItems': False},
                  'optional': {'default': 'not-inserted'}}}
    arguments = {'items': [3, 'x']}
    before = copy.deepcopy((schema, arguments))
    assert validate_payload(schema, arguments) == 'ok'
    assert (schema, arguments) == before
    assert validate_payload(schema, {'items': ['3', 'x']}) == 'arguments_schema'
    assert validate_payload(schema, {'items': [3, 'x', 4]}) == 'arguments_schema'


@pytest.mark.parametrize('dialect', [None, DIALECT, DIALECT + '#'])
def test_202012_and_implicit_dialect_keep_prefix_items_semantics(dialect):
    schema = {'type': 'object', 'properties': {
        'items': {'type': 'array', 'prefixItems': [{'type': 'integer'}], 'items': False}}}
    if dialect is not None:
        schema['$schema'] = dialect
    assert validate_payload(schema, {'items': [3]}) == 'ok'
    assert validate_payload(schema, {'items': ['3']}) == 'arguments_schema'
    assert validate_payload(schema, {'items': [3, 4]}) == 'arguments_schema'


@pytest.mark.parametrize('dialect', [DRAFT7, DIALECT])
@pytest.mark.parametrize('ref', ['https://example.test/schema', 'file:///private/schema.json'])
def test_both_dialects_deny_resource_retrieval(dialect, ref):
    assert validate_payload({'$schema': dialect, '$ref': ref}, {}) == 'schema_reference_blocked'


@pytest.mark.parametrize('dialect', [DRAFT7, DIALECT])
def test_both_dialects_check_schema_and_argument_types(dialect):
    assert validate_payload({'$schema': dialect, 'type': 'PRIVATE_BAD_TYPE'}, {}) == 'schema_invalid'
    schema = {'$schema': dialect, 'type': 'object', 'required': ['n'],
              'additionalProperties': False,
              'properties': {'n': {'type': 'integer'}, 'email': {'type': 'string', 'format': 'email'}}}
    assert validate_payload(schema, {}) == 'arguments_schema'
    assert validate_payload(schema, {'n': True}) == 'arguments_schema'
    assert validate_payload(schema, {'n': 1, 'extra': True}) == 'arguments_schema'
    assert validate_payload(schema, {'n': 1, 'email': 'not-an-email'}) == 'ok'


@pytest.mark.parametrize('root, nested', [(DRAFT7, DIALECT), (DIALECT, DRAFT7), (None, DRAFT7)])
def test_mixed_dialects_are_rejected(root, nested):
    schema = {'type': 'object', 'properties': {'q': {'$schema': nested, 'type': 'string'}}}
    if root is not None:
        schema['$schema'] = root
    assert validate_payload(schema, {}) == 'schema_dialect'


@pytest.mark.parametrize('dialect', [DRAFT7, DIALECT])
def test_matching_nested_dialect_with_optional_fragment_is_accepted(dialect):
    schema = {'$schema': dialect, 'type': 'object', 'properties': {
        'q': {'$schema': dialect + '#', 'type': 'string'}}}
    assert validate_payload(schema, {'q': 'x'}) == 'ok'
    assert validate_payload(schema, {'q': 3}) == 'arguments_schema'


@pytest.mark.parametrize('declaration', [None, [], {}, 'https://example.test/schema'])
def test_invalid_or_unknown_dialect_is_rejected(declaration):
    assert validate_payload({'$schema': declaration}, {}) == 'schema_dialect'


def test_nested_constraints():
    schema = {'type': 'object', 'required': ['items'], 'properties': {
        'items': {'type': 'array', 'minItems': 1, 'items': {
            'anyOf': [{'type': 'null'}, {'type': 'number', 'minimum': 0}]}}}}
    assert validate_payload(schema, {'items': [None, 2]}) == 'ok'
    assert validate_payload(schema, {'items': [-1]}) == 'arguments_schema'
    assert validate_payload(schema, {'items': []}) == 'arguments_schema'
