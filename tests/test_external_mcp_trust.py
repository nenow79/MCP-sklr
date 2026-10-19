"""Profile trust: pinned tools, connection digest, registry methods, rate limiter. No I/O."""
import copy
from uuid import uuid4

import pytest

from config_store import ConfigStore
from external_mcp.profiles import (
    ProfileConflict, ProfileError, ProfileNotFound, connection_digest, validate_profile,
)
from external_mcp.registry import ExternalMCPRegistry
from external_mcp.runtime_config import prepare_runtime
from external_mcp.trust import TrustRateLimiter, trusted_decision
from state import load_config

SHA_A, SHA_B = 'a' * 64, 'b' * 64
TOOLS = {'read_page': SHA_A, 'list_tabs': SHA_B}
GRANTED = '2026-10-19T10:00:00+00:00'


def http(**values):
    base = dict(alias='browser', transport='http', url='https://example.test/mcp')
    base.update(values)
    return base


def stdio(**values):
    base = dict(alias='local', transport='stdio', command='python')
    base.update(values)
    return base


def stored(proposal):
    row = validate_profile(proposal)
    row.update(id=str(uuid4()), revision=str(uuid4()))
    return row


def with_trust(row, **patch):
    trust = {'tools': dict(TOOLS), 'connection_sha256': connection_digest(row),
             'granted_at': GRANTED}
    trust.update(patch)
    row = copy.deepcopy(row)
    row['trust'] = trust
    return row


# --- connection digest ---

def test_digest_ignores_alias_description_and_enabled():
    base = connection_digest(validate_profile(http()))
    assert connection_digest(validate_profile(http(alias='other'))) == base
    assert connection_digest(validate_profile(http(description='changed'))) == base
    assert connection_digest(validate_profile(http(enabled=True))) == base


@pytest.mark.parametrize('patch', [
    {'url': 'https://example.test/other'}, {'bearer_env': 'REMOTE_TOKEN'},
    {'headers_env': {'X-Key': 'REMOTE_KEY'}}, {'transport': 'sse'},
    {'ca_file': r'C:\certs\corp.pem'},
])
def test_digest_changes_with_network_connection(patch):
    assert connection_digest(validate_profile(http(**patch))) != connection_digest(
        validate_profile(http()))


@pytest.mark.parametrize('patch', [
    {'command': 'node'}, {'args': ['server.py']}, {'cwd': r'C:\work'},
    {'env_refs': {'API_TOKEN': 'REMOTE_TOKEN'}},
])
def test_digest_changes_with_stdio_connection(patch):
    assert connection_digest(validate_profile(stdio(**patch))) != connection_digest(
        validate_profile(stdio()))


# --- stored trust record validation ---

def test_trust_cannot_be_part_of_a_proposal():
    row = with_trust(stored(http()))
    proposal = {key: row[key] for key in ('alias', 'transport', 'url', 'trust')}
    with pytest.raises(ProfileError, match='unknown_fields'):
        validate_profile(proposal)


def test_valid_stored_trust_is_kept_and_detached():
    row = with_trust(stored(http()))
    result = validate_profile(row, stored=True)
    assert result['trust'] == row['trust']
    result['trust']['tools']['read_page'] = SHA_B
    assert row['trust']['tools']['read_page'] == SHA_A


def test_trust_for_changed_connection_is_dropped_not_kept():
    row = with_trust(stored(http()), connection_sha256='f' * 64)
    assert 'trust' not in validate_profile(row, stored=True)


@pytest.mark.parametrize('patch', [
    {'tools': {}}, {'tools': 'x'}, {'tools': {'x': 'short'}}, {'tools': {'x': 'A' * 64}},
    {'tools': {'': SHA_A}}, {'tools': {'x': 5}},
    {'tools': {str(i): SHA_A for i in range(201)}},
    {'connection_sha256': 'not-hex'}, {'granted_at': 'yesterday'}, {'granted_at': 5},
    {'extra': 'field'},
])
def test_malformed_trust_record_is_rejected(patch):
    row = with_trust(stored(http()))
    row['trust'].update(patch)
    with pytest.raises(ProfileError):
        validate_profile(row, stored=True)


def test_trust_record_with_missing_key_is_rejected():
    row = with_trust(stored(http()))
    del row['trust']['granted_at']
    with pytest.raises(ProfileError):
        validate_profile(row, stored=True)


def test_stored_row_with_trust_still_prepares_runtime():
    # Regression: the client path re-validates stored rows, so trust must be accepted there.
    runtime = prepare_runtime(with_trust(stored(http())), environ={})
    assert runtime.url == 'https://example.test/mcp'
    runtime.clear()


# --- trust decision ---

def test_decision_reasons():
    row = stored(http(enabled=True))
    assert trusted_decision(row, 'read_page', SHA_A) == {'trusted': False, 'reason': 'no_trust'}
    trusted = with_trust(row)
    assert trusted_decision(trusted, 'read_page', SHA_A) == {'trusted': True, 'reason': ''}
    assert trusted_decision(trusted, 'open_url', SHA_A)['reason'] == 'tool_not_trusted'
    assert trusted_decision(trusted, 'read_page', SHA_B)['reason'] == 'schema_changed'
    assert trusted_decision(trusted, 'read_page', 'bad')['reason'] == 'invalid_request'
    assert trusted_decision(trusted, '', SHA_A)['reason'] == 'invalid_request'
    off = with_trust(stored(http(enabled=False)))
    assert trusted_decision(off, 'read_page', SHA_A)['reason'] == 'profile_disabled'


def test_annotations_never_grant_trust():
    row = stored(http(enabled=True))
    assert not trusted_decision(row, 'read_only_tool', SHA_A)['trusted']


# --- registry ---

@pytest.fixture
def env(tmp_path):
    path = tmp_path / 'config.json'
    store = ConfigStore(load_config(path), path)
    return ExternalMCPRegistry(store), store, path


def add(registry, **values):
    return registry.add(http(**values), registry.list()['revision'])


def granted(registry, **values):
    first = add(registry, **values)
    row = first['profiles'][0]
    return registry.set_trust(row['id'], TOOLS, first['revision']), row['id']


def only(listing):
    return listing['profiles'][0]


def test_trust_is_stored_pinned_and_survives_restart(env):
    registry, _, path = env
    listing, _ = granted(registry)
    trust = only(listing)['trust']
    assert trust['tools'] == TOOLS
    assert trust['connection_sha256'] == connection_digest(only(listing))
    restarted = ExternalMCPRegistry(ConfigStore(load_config(path), path))
    assert restarted.list() == listing


def test_trust_cannot_be_injected_through_add_or_update(env):
    registry, _, _ = env
    injected = http(trust={'tools': TOOLS, 'connection_sha256': 'f' * 64, 'granted_at': GRANTED})
    with pytest.raises(ProfileError, match='unknown_fields'):
        registry.add(injected, registry.list()['revision'])
    listing = add(registry)
    with pytest.raises(ProfileError, match='unknown_fields'):
        registry.update(only(listing)['id'], injected, listing['revision'])


def test_enable_and_disable_keep_trust(env):
    registry, _, _ = env
    listing, profile_id = granted(registry)
    enabled = registry.set_enabled(profile_id, True, listing['revision'])
    disabled = registry.set_enabled(profile_id, False, enabled['revision'])
    assert only(disabled)['trust'] == only(listing)['trust']


def test_alias_and_description_edit_keep_trust_but_connection_edit_drops_it(env):
    registry, _, _ = env
    listing, profile_id = granted(registry)
    renamed = registry.update(profile_id, http(alias='renamed', description='new'),
                              listing['revision'])
    assert only(renamed)['trust'] == only(listing)['trust']
    moved = registry.update(profile_id, http(alias='renamed', url='https://example.test/other'),
                            renamed['revision'])
    assert 'trust' not in only(moved)
    again = registry.update(profile_id, http(alias='renamed'), moved['revision'])
    assert 'trust' not in only(again)  # returning to the old URL does not revive trust


def test_revoke_removes_trust_and_changes_revision(env):
    registry, _, _ = env
    listing, profile_id = granted(registry)
    revoked = registry.revoke_trust(profile_id, listing['revision'])
    assert 'trust' not in only(revoked) and revoked['revision'] != listing['revision']
    assert not registry.trust_decision('browser', 'read_page', SHA_A)['trusted']


def test_remove_profile_removes_trust(env):
    registry, _, _ = env
    listing, profile_id = granted(registry)
    assert registry.remove(profile_id, listing['revision'])['count'] == 0


def test_stale_revision_does_not_write_trust(env):
    registry, store, path = env
    first = add(registry)
    stale = first['revision']
    registry.set_enabled(only(first)['id'], True, stale)
    before, disk = store.snapshot(), path.read_bytes()
    with pytest.raises(ProfileConflict):
        registry.set_trust(only(first)['id'], TOOLS, stale)
    assert store.snapshot() == before and path.read_bytes() == disk


@pytest.mark.parametrize('tools', [
    {}, 'x', {'x': 'bad'}, {'': SHA_A}, {'x': SHA_A.upper()}, {'x': 5},
    {str(i): SHA_A for i in range(201)},
])
def test_bad_tool_sets_are_rejected_without_writing(env, tools):
    registry, store, path = env
    first = add(registry)
    before, disk = store.snapshot(), path.read_bytes()
    with pytest.raises(ProfileError):
        registry.set_trust(only(first)['id'], tools, first['revision'])
    assert store.snapshot() == before and path.read_bytes() == disk


def test_unknown_profile_for_trust_operations(env):
    registry, _, _ = env
    first = add(registry)
    with pytest.raises(ProfileNotFound):
        registry.set_trust(str(uuid4()), TOOLS, first['revision'])
    with pytest.raises(ProfileNotFound):
        registry.revoke_trust(str(uuid4()), first['revision'])
    with pytest.raises(ProfileNotFound):
        registry.trust_decision('missing', 'read_page', SHA_A)


@pytest.mark.parametrize('operation', ['grant', 'revoke'])
def test_failed_save_rolls_back_trust(env, monkeypatch, operation):
    registry, store, path = env
    if operation == 'grant':
        first = add(registry)
    else:
        first, _ = granted(registry)
    row = only(first)
    before, disk = store.snapshot(), path.read_bytes()
    monkeypatch.setattr('config_store.save_config',
                        lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        if operation == 'grant':
            registry.set_trust(row['id'], TOOLS, first['revision'])
        else:
            registry.revoke_trust(row['id'], first['revision'])
    assert store.snapshot() == before and path.read_bytes() == disk
    assert registry.list() == first


def test_out_of_band_connection_edit_drops_trust(env):
    registry, store, _ = env
    listing, _ = granted(registry)
    store.data['external_mcp']['profiles'][0]['url'] = 'https://elsewhere.test/mcp'
    after = registry.list()
    assert after['configuration_valid'] and 'trust' not in only(after)
    assert after['revision'] != listing['revision']


def test_corrupt_trust_record_fails_closed_without_rewriting(env):
    registry, store, path = env
    granted(registry)
    store.data['external_mcp']['profiles'][0]['trust']['extra'] = 'x'
    disk = path.read_bytes()
    assert not registry.list()['configuration_valid']
    assert path.read_bytes() == disk


def test_trust_decision_uses_fresh_state_and_requires_enabled(env):
    registry, _, _ = env
    listing, profile_id = granted(registry)
    off = registry.trust_decision('browser', 'read_page', SHA_A)
    assert off['trusted'] is False and off['reason'] == 'profile_disabled'
    enabled = registry.set_enabled(profile_id, True, listing['revision'])
    on = registry.trust_decision('browser', 'read_page', SHA_A)
    assert on['trusted'] is True and on['profile_id'] == profile_id
    assert on['revision'] == enabled['revision']
    registry.revoke_trust(profile_id, enabled['revision'])
    assert registry.trust_decision('browser', 'read_page', SHA_A)['reason'] == 'no_trust'


# --- rate limiter ---

class Clock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now


def test_limiter_allows_thirty_per_minute_then_denies():
    clock = Clock()
    limiter = TrustRateLimiter(clock=clock)
    assert all(limiter.allow('p1') for _ in range(30))
    assert not limiter.allow('p1')
    clock.now += 59.9
    assert not limiter.allow('p1')
    clock.now += 0.2
    assert limiter.allow('p1')


def test_limiter_is_per_profile_and_resettable():
    limiter = TrustRateLimiter(limit=2, clock=Clock())
    assert limiter.allow('a') and limiter.allow('a') and not limiter.allow('a')
    assert limiter.allow('b')
    limiter.reset('a')
    assert limiter.allow('a')


@pytest.mark.parametrize('profile_id', [None, '', 5])
def test_limiter_denies_invalid_ids(profile_id):
    assert not TrustRateLimiter().allow(profile_id)


@pytest.mark.parametrize('kwargs', [{'limit': 0}, {'limit': True}, {'window_s': 0},
                                    {'window_s': 'x'}, {'clock': 5}])
def test_limiter_rejects_bad_configuration(kwargs):
    with pytest.raises(ValueError):
        TrustRateLimiter(**kwargs)


def test_limiter_forgets_idle_profiles():
    clock = Clock()
    limiter = TrustRateLimiter(limit=1, clock=clock)
    for index in range(300):
        limiter.allow(f'p{index}')
    clock.now += 61
    limiter.allow('fresh')
    assert len(limiter._hits) < 300
