"""Closed policy schemas, stable keyed fingerprints and private immutable data."""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from autopilot.rules import PolicyError, parse_policy

KEY = b'k' * 32


def root():
    return {'platform': 'posix', 'path': '/work/demo', 'mode': 'rw', 'epoch': 'a' * 32}


def document():
    return {'version': 1, 'rules': [{'id': 'code', 'kind': 'files',
            'tools': ['read_file', 'edit_file'], 'root': root(),
            'scope': {'kind': 'tree', 'path': 'src'}, 'overwrite': False}]}


def test_policy_is_detached_and_fingerprints_canonical_json():
    raw = document()
    policy = parse_policy(raw, hmac_key=KEY)
    reordered = dict(reversed(list(deepcopy(raw).items())))
    reordered['rules'][0] = dict(reversed(list(reordered['rules'][0].items())))
    assert parse_policy(reordered, hmac_key=KEY).digest == policy.digest
    raw['rules'][0]['scope']['path'] = 'PRIVATE-CHANGED'
    assert policy.rules[0].scope.path == 'src'
    assert parse_policy(raw, hmac_key=KEY).digest != policy.digest
    assert parse_policy(document(), hmac_key=b'z' * 32).digest != policy.digest
    assert '/work' not in repr(policy) + repr(policy.rules[0])
    with pytest.raises(FrozenInstanceError):
        policy.rules[0].overwrite = True


@pytest.mark.parametrize('version', [True, 0, 2, '1', 1.0, None])
def test_unknown_version_is_rejected(version):
    raw = document()
    raw['version'] = version
    with pytest.raises(PolicyError):
        parse_policy(raw, hmac_key=KEY)


@pytest.mark.parametrize('location,key,value', [
    ('document', 'allow_all', True), ('rule', 'sensitive', False),
    ('root', 'alias', 'PRIVATE'), ('scope', 'glob', '*'),
])
def test_unknown_fields_are_rejected_without_echo(location, key, value):
    raw = document()
    target = {'document': raw, 'rule': raw['rules'][0], 'root': raw['rules'][0]['root'],
              'scope': raw['rules'][0]['scope']}[location]
    target[key] = value
    with pytest.raises(PolicyError) as exc:
        parse_policy(raw, hmac_key=KEY)
    assert 'PRIVATE' not in str(exc.value)


@pytest.mark.parametrize('key,value', [('kind', 'shell'), ('tools', []),
                                     ('tools', ['secret_reveal']), ('tools', ['read_file', 'read_file']),
                                     ('overwrite', 1), ('id', '*')])
def test_invalid_rule_kind_tools_or_types_are_rejected(key, value):
    raw = document()
    raw['rules'][0][key] = value
    with pytest.raises(PolicyError):
        parse_policy(raw, hmac_key=KEY)


def test_rule_ids_and_resource_generations_cannot_be_ambiguous():
    raw = document()
    raw['rules'].append(deepcopy(raw['rules'][0]))
    with pytest.raises(PolicyError, match='^duplicate_rule$'):
        parse_policy(raw, hmac_key=KEY)
    raw['rules'][1]['id'] = 'other'
    raw['rules'][1]['root']['epoch'] = 'b' * 32
    with pytest.raises(PolicyError, match='^inconsistent_resource$'):
        parse_policy(raw, hmac_key=KEY)


def test_empty_policy_delegates_nothing_and_rule_limit_is_bounded():
    assert parse_policy({'version': 1, 'rules': []}, hmac_key=KEY).rules == ()
    raw = document()
    raw['rules'] = [dict(raw['rules'][0], id=f'rule-{i}') for i in range(129)]
    with pytest.raises(PolicyError):
        parse_policy(raw, hmac_key=KEY)


@pytest.mark.parametrize('key', [None, 'PRIVATE', b'a' * 31, b'a' * 4097, bytearray(b'a' * 32)])
def test_key_is_explicit_bounded_bytes(key):
    with pytest.raises(PolicyError, match='^invalid_key$'):
        parse_policy(document(), hmac_key=key)


def skill_rule():
    return {'id': 'run', 'kind': 'skill_run', 'tools': ['run_skill_script'],
            'skill': {'name': 'demo', 'source': 'central', 'skill_hash': 'a' * 64,
                      'execution_digest': 'b' * 64, 'epoch': 'c' * 32},
            'script': 'scripts/read.py', 'args': ['--limit', '5'], 'max_timeout_s': 300}


@pytest.mark.parametrize('change', ['source', 'script', 'args', 'timeout'])
def test_skill_scope_does_not_delegate_other_sources_shell_or_unbounded_runtime(change):
    raw = skill_rule()
    if change == 'source':
        raw['skill']['source'] = 'project'
    elif change == 'script':
        raw['script'] = 'scripts/../outside.py'
    elif change == 'args':
        raw['args'] = ['x'] * 101
    else:
        raw['max_timeout_s'] = 301
    with pytest.raises(PolicyError):
        parse_policy({'version': 1, 'rules': [raw]}, hmac_key=KEY)


def test_catalog_is_typed_and_cannot_mix_tools_or_duplicate_resources():
    rule = {'id': 'roots', 'kind': 'catalog', 'tools': ['list_roots'], 'resources': [root()]}
    assert parse_policy({'version': 1, 'rules': [rule]}, hmac_key=KEY).rules[0].kind == 'catalog'
    for invalid in (dict(rule, tools=['list_roots', 'list_skills']),
                    dict(rule, resources=[root(), root()]),
                    dict(rule, resources=[skill_rule()['skill']])):
        with pytest.raises(PolicyError):
            parse_policy({'version': 1, 'rules': [invalid]}, hmac_key=KEY)


def test_private_literals_are_not_in_policy_repr_and_input_is_copied():
    selector = {'type': 'object', 'properties': {'query': {'type': 'literal', 'value': 'PRIVATE'}},
                'required': ['query']}
    rule = {'id': 'call', 'kind': 'mcp_call', 'tools': ['call_external_mcp_tool'],
            'profile': {'profile_id': 'a' * 32, 'connection_digest': 'b' * 64, 'epoch': 'c' * 32},
            'tool_name': 'find', 'schema_digest': 'd' * 64, 'arguments': selector, 'max_timeout_s': 300}
    policy = parse_policy({'version': 1, 'rules': [rule]}, hmac_key=KEY)
    assert 'PRIVATE' not in repr(policy) + repr(policy.rules[0]) + repr(policy.rules[0].arguments)
    selector['properties']['query']['value'] = 'CHANGED'
    assert policy.rules[0].arguments.data[0][0][1].data[0].value == 'PRIVATE'
