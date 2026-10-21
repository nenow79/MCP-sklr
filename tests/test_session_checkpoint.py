"""Detached P3a validation tests. No server, store or main wiring."""
import pytest

from server.session_checkpoint import (
    MAX_FINDINGS, MAX_GOAL, CheckpointRejected, validate_checkpoint)


def owns(*ids):
    return lambda task_id: task_id in ids


def good(**extra):
    payload = {'goal': 'Dodac UI sesji', 'next_step': 'Uruchomic testy'}
    payload.update(extra)
    return payload


def reject(payload, code, owner=None):
    with pytest.raises(CheckpointRejected) as info:
        validate_checkpoint(payload, owner or owns())
    assert info.value.code == code and str(info.value) == code
    return info.value


def test_minimal_and_full_payload_are_normalized():
    minimal = validate_checkpoint(good(), owns())
    assert minimal.stage == '' and minimal.findings == () and minimal.done_task_ids == ()
    full = validate_checkpoint(good(
        goal='  cel  ', stage='P3a',
        findings=[{'text': 'Pauza dziala', 'source': 'docs/specs/work-session-pause.md'}],
        done_task_ids=[818, 819], open_questions=['Czy flaga?'], uncertain=['wynik MCP #7']),
        owns(818, 819))
    assert full.goal == 'cel' and full.done_task_ids == (818, 819)
    assert full.findings[0].source == 'docs/specs/work-session-pause.md'
    assert full.size == len('cel') + len('P3a') + len(full.next_step) + len('Pauza dziala') \
        + len('docs/specs/work-session-pause.md') + len('Czy flaga?') + len('wynik MCP #7')


@pytest.mark.parametrize('payload', [None, [], 'x', {1: 'a'}])
def test_payload_must_be_dict_with_string_keys(payload):
    reject(payload, 'invalid_shape')


def test_unknown_and_missing_fields():
    reject(good(session_id='x' * 32), 'unknown_field')
    reject({'goal': 'a'}, 'missing_field')
    reject({'next_step': 'a'}, 'missing_field')


@pytest.mark.parametrize('payload', [
    good(goal=1), good(stage=None), good(findings='x'), good(findings=['x']),
    good(findings=[{'text': 'a'}]), good(findings=[{'text': 'a', 'source': 'b', 'x': 1}]),
    good(open_questions=[1]), good(uncertain='x'), good(done_task_ids='1'),
    good(done_task_ids=[True]), good(done_task_ids=[0]), good(done_task_ids=[-1]),
    good(done_task_ids=[1.0]), good(done_task_ids=[5, 5]),
])
def test_wrong_types_are_invalid_shape(payload):
    reject(payload, 'invalid_shape', owns(1, 5))


@pytest.mark.parametrize('payload', [good(goal=' '), good(next_step=''),
                                     good(findings=[{'text': '', 'source': 's'}]),
                                     good(open_questions=[' '])])
def test_required_text_cannot_be_empty(payload):
    reject(payload, 'empty')


def test_item_and_list_limits():
    reject(good(goal='a' * (MAX_GOAL + 1)), 'too_long')
    validate_checkpoint(good(goal='a' * MAX_GOAL), owns())
    many = [{'text': 't', 'source': 's'}] * (MAX_FINDINGS + 1)
    reject(good(findings=many), 'too_many')
    reject(good(open_questions=['q'] * 11), 'too_many')
    reject(good(uncertain=['u'] * 11), 'too_many')
    reject(good(done_task_ids=list(range(1, 102))), 'too_many', lambda i: True)


def test_total_size_limit_even_when_every_item_fits():
    findings = [{'text': 't' * 400, 'source': 's' * 100}] * 20     # 10000 chars
    reject(good(findings=findings), 'too_long')


@pytest.mark.parametrize('bad', ['a\x00b', 'a\rb', 'a\u202eb', 'a\u200bb', 'a\u2028b'])
def test_control_and_directional_characters_rejected(bad):
    reject(good(goal=bad), 'control_chars')
    reject(good(findings=[{'text': bad, 'source': 's'}]), 'control_chars')


def test_newline_is_allowed():
    assert validate_checkpoint(good(goal='a\nb'), owns()).goal == 'a\nb'


@pytest.mark.parametrize('secret', [
    'API_TOKEN=abc123', 'export DB_PASSWORD="hunter2"', 'wazne\nSECRET_KEY=zzz',
    'postgres://user:hunter2@host/db',
    '-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq',
    '-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----',
])
def test_secret_rejects_whole_payload_in_every_text_field(secret):
    for payload in (good(goal=secret), good(stage=secret), good(next_step=secret),
                    good(findings=[{'text': secret, 'source': 's'}]),
                    good(findings=[{'text': 't', 'source': secret}]),
                    good(open_questions=[secret]), good(uncertain=[secret])):
        error = reject(payload, 'secret_detected')
        assert 'hunter2' not in str(error) and 'abc123' not in str(error)
        assert not error.args[1:] and error.__cause__ is None


def test_masked_placeholder_is_not_a_secret():
    validate_checkpoint(good(goal='API_TOKEN=***'), owns())


def test_foreign_or_invented_task_ids_rejected():
    reject(good(done_task_ids=[1, 2]), 'foreign_task', owns(1))
    reject(good(done_task_ids=[3]), 'foreign_task', lambda task_id: 'yes')   # only True accepted


def test_failing_ownership_lookup_rejects_without_details():
    def broken(task_id):
        raise RuntimeError('C:/secret/path.db locked')
    error = reject(good(done_task_ids=[1]), 'link_check_failed', broken)
    assert 'secret' not in str(error) and error.__cause__ is None


def test_repr_and_public_view_do_not_leak_or_alias():
    checkpoint = validate_checkpoint(good(goal='tajny-cel', findings=[
        {'text': 'tajne-ustalenie', 'source': 'zrodlo'}]), owns())
    assert 'tajny-cel' not in repr(checkpoint) and 'tajne-ustalenie' not in repr(checkpoint)
    assert 'tajne-ustalenie' not in repr(checkpoint.findings[0])
    view = checkpoint.to_public()
    view['findings'].append('x')
    view['done_task_ids'].append(9)
    assert len(checkpoint.findings) == 1 and checkpoint.done_task_ids == ()
    with pytest.raises(Exception):
        checkpoint.goal = 'zmiana'
