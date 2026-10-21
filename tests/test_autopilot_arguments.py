"""Typed argument matching and bounded closed selectors; no network."""
import pytest

from autopilot.arguments import ArgumentRuleError, freeze_json, matches, parse_selector, thaw_json


def accepts(rule, value):
    return matches(parse_selector(rule), freeze_json(value))


@pytest.mark.parametrize('expected,actual', [(True, 1), (False, 0), (1, 1.0),
                                           ([True], [1]), ({'a': 1}, {'a': 1.0})])
def test_literal_and_enum_preserve_nested_types(expected, actual):
    assert not accepts({'type': 'literal', 'value': expected}, actual)
    assert not accepts({'type': 'enum', 'values': [expected]}, actual)


def test_literal_objects_ignore_key_order_and_freeze_input():
    raw = {'a': [1, {'secret': 'PRIVATE'}], 'b': None}
    frozen = freeze_json(raw)
    selector = parse_selector({'type': 'literal', 'value': raw})
    raw['a'][1]['secret'] = 'changed'
    assert matches(selector, frozen)
    assert thaw_json(frozen)['a'][1]['secret'] == 'PRIVATE'
    assert 'PRIVATE' not in repr(frozen) + repr(selector)
    assert accepts({'type': 'literal', 'value': {'b': 2, 'a': 1}}, {'a': 1, 'b': 2})
    assert not accepts({'type': 'literal', 'value': [1, 2]}, [2, 1])


def test_required_optional_null_and_additional_properties():
    rule = {'type': 'object', 'properties': {
        'name': {'type': 'string', 'max_length': 3},
        'optional': {'type': 'literal', 'value': None}}, 'required': ['name']}
    assert accepts(rule, {'name': ''})
    assert accepts(rule, {'name': 'abc', 'optional': None})
    assert not accepts(rule, {})
    assert not accepts(rule, {'name': None})
    assert not accepts(rule, {'name': 'abcd'})
    assert not accepts(rule, {'name': 'a', 'extra': True})
    assert not accepts(rule, {'name': 'a', 'optional': ''})


@pytest.mark.parametrize('kind,value,allowed', [
    ('integer', 0, True), ('integer', 2, True), ('integer', 3, False),
    ('integer', 1.0, False), ('integer', True, False), ('number', 1.5, True),
    ('number', 1, True), ('number', False, False), ('number', -1, False),
])
def test_numeric_ranges(kind, value, allowed):
    assert accepts({'type': kind, 'minimum': 0, 'maximum': 2}, value) is allowed


@pytest.mark.parametrize('value', [float('nan'), float('inf'), 2 ** 63, -(2 ** 63) - 1,
                                  {'bad': object()}, {1: 'bad'}, ('not', 'json'), '\ud800'])
def test_non_json_values_are_rejected_without_echo(value):
    with pytest.raises(ArgumentRuleError) as exc:
        freeze_json(value)
    assert str(exc.value) in {'invalid_json', 'json_limit'}


def test_json_limits_and_cycles():
    cycle = []
    cycle.append(cycle)
    for value in (cycle, 'x' * 4097, ['x' * 4096] * 40, list(range(10001))):
        with pytest.raises(ArgumentRuleError):
            freeze_json(value)
    deep = None
    for _ in range(17):
        deep = [deep]
    with pytest.raises(ArgumentRuleError):
        freeze_json(deep)


@pytest.mark.parametrize('rule', [
    {'type': 'string', 'max_length': True}, {'type': 'string', 'max_length': 0},
    {'type': 'literal', 'value': 'PRIVATE', 'extra': True},
    {'type': 'enum', 'values': []}, {'type': 'enum', 'values': [1, 1]},
    {'type': 'integer', 'minimum': 0.0, 'maximum': 1},
    {'type': 'number', 'minimum': True, 'maximum': 2},
    {'type': 'number', 'minimum': 3, 'maximum': 2}, {'type': 'array', 'items': {}},
    {'type': 'object', 'properties': {}, 'required': ['unknown']},
    {'type': 'object', 'properties': {'a': {'type': 'boolean'}}, 'required': ['a', 'a']},
    {'type': '$ref', 'value': 'PRIVATE'},
])
def test_selector_language_is_closed(rule):
    with pytest.raises(ValueError) as exc:
        parse_selector(rule)
    assert 'PRIVATE' not in str(exc.value)


def test_selector_depth_and_distinct_enum_types():
    rule = {'type': 'boolean'}
    for _ in range(8):
        rule = {'type': 'object', 'properties': {'a': rule}, 'required': []}
    with pytest.raises(ValueError):
        parse_selector(rule)
    assert accepts({'type': 'enum', 'values': [True, 1, 1.0]}, 1.0)
    assert accepts({'type': 'boolean'}, True)
    assert not accepts({'type': 'boolean'}, 1)


def url_rule():
    return {'type': 'url', 'schemes': ['https'], 'hosts': ['docs.example.test'],
            'ports': [443], 'path': {'kind': 'tree', 'path': '/docs'}}


@pytest.mark.parametrize('url,allowed', [
    ('https://docs.example.test/docs', True), ('https://DOCS.example.test:443/docs/a', True),
    ('https://docs.example.test/docs/', True), ('http://docs.example.test/docs', False),
    ('https://docs.example.test:444/docs', False), ('https://docs.example.test.evil/docs', False),
    ('https://docs.example.test/docs2', False), ('https://user@docs.example.test/docs', False),
    ('https://docs.example.test/docs?', False), ('https://docs.example.test/docs#', False),
    ('https://docs.example.test/docs/%2e%2e/x', False), ('https://docs.example.test/docs/../x', False),
    ('https://docs.example.test/docs//x', False), ('https://docs.example.test:/docs', False),
    ('https://docs.example.test/\\docs', False), (' https://docs.example.test/docs', False),
    ('https://docs.example.test/док', False),
])
def test_url_authority_path_and_ambiguous_forms(url, allowed):
    assert accepts(url_rule(), url) is allowed


def test_url_exact_root_and_ipv6():
    rule = {'type': 'url', 'schemes': ['https'], 'hosts': ['::1'], 'ports': [443],
            'path': {'kind': 'exact', 'path': '/'}}
    assert accepts(rule, 'https://[::1]')
    assert not accepts(rule, 'https://[::1]/docs')
    bad = url_rule()
    bad['hosts'] = ['*.example.test']
    with pytest.raises(ValueError):
        parse_selector(bad)
