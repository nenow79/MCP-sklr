"""Persistent configuration contract (P01/P02): tmp files, no MCP or subprocess.

Run with -k test_external_mcp_persistent_config. Legacy fixtures are constructed
independently of the new validator so RED exposes behavior, not missing imports.
"""
import copy
import hashlib
import json
from uuid import uuid4

import pytest

from config_store import ConfigStore
from external_mcp.profiles import ProfileConflict, ProfileError, connection_digest, validate_profile
from external_mcp.registry import ExternalMCPRegistry
from state import load_config


SCHEMA_SHA = 'a' * 64
LEGACY_FIELDS = ('transport', 'command', 'args', 'cwd', 'env_refs')


def stdio(**patch):
    row = dict(alias='local', description='', enabled=True, transport='stdio',
               command='node', args=['server with spaces.mjs'], cwd=None,
               env_refs={'API_TOKEN': 'TEST_TOKEN_REFERENCE'})
    row.update(patch)
    return row


def legacy_digest(row):
    encoded = json.dumps({key: row.get(key) for key in LEGACY_FIELDS},
                         sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def editable(row, **patch):
    result = {key: copy.deepcopy(value) for key, value in row.items()
              if key not in {'id', 'revision', 'trust'}}
    result.update(patch)
    return result


@pytest.fixture
def legacy(tmp_path):
    path = tmp_path / 'config.json'
    row = stdio(id=str(uuid4()), revision=str(uuid4()))
    row['trust'] = dict(tools={'lookup': SCHEMA_SHA}, connection_sha256=legacy_digest(row),
                        granted_at='2026-01-01T00:00:00+00:00')
    config = load_config(path)
    config['external_mcp'] = dict(version=1, generation=str(uuid4()), profiles=[row])
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    store = ConfigStore(load_config(path), path)
    return ExternalMCPRegistry(store), store, path, copy.deepcopy(row)


@pytest.mark.parametrize('stored', [False, True])
def test_missing_mode_defaults_to_one_shot_without_mutating_input(stored):
    proposal = stdio()
    if stored:
        proposal.update(id=str(uuid4()), revision=str(uuid4()))
    before = copy.deepcopy(proposal)
    parsed = validate_profile(proposal, stored=stored)
    assert parsed['stdio_mode'] == 'one_shot'
    assert proposal == before
    assert connection_digest(parsed) == legacy_digest(proposal)


@pytest.mark.parametrize('mode', ['one_shot', 'persistent'])
def test_explicit_stdio_modes_are_accepted_and_detached(mode):
    proposal = stdio(stdio_mode=mode)
    before = copy.deepcopy(proposal)
    parsed = validate_profile(proposal)
    assert parsed['stdio_mode'] == mode
    parsed['args'].append('changed')
    assert proposal == before


@pytest.mark.parametrize('mode', [None, True, 1, '', 'PERSISTENT', 'unknown', [], {}])
def test_invalid_modes_are_rejected_without_echo(mode):
    with pytest.raises(ProfileError) as raised:
        validate_profile(stdio(stdio_mode=mode))
    assert 'server with spaces' not in str(raised.value)


@pytest.mark.parametrize('transport', ['http', 'sse'])
@pytest.mark.parametrize('mode', ['one_shot', 'persistent'])
def test_network_transports_reject_stdio_mode(transport, mode):
    proposal = dict(alias='network', transport=transport, url='https://example.test/mcp',
                    stdio_mode=mode)
    with pytest.raises(ProfileError):
        validate_profile(proposal)


def test_digest_preserves_legacy_one_shot_and_distinguishes_persistent():
    old = stdio()
    explicit = stdio(stdio_mode='one_shot')
    persistent = stdio(stdio_mode='persistent')
    assert connection_digest(old) == legacy_digest(old)
    assert connection_digest(explicit) == legacy_digest(old)
    assert connection_digest(persistent) != legacy_digest(old)
    assert connection_digest(persistent) == connection_digest(
        stdio(stdio_mode='persistent', alias='renamed', description='new', enabled=False))


def test_v1_read_and_resolve_do_not_write_or_revoke_trust(legacy, monkeypatch):
    registry, store, path, old = legacy
    before, disk = store.snapshot(), path.read_bytes()
    def forbidden_save(*args, **kwargs):
        pytest.fail('Reading v1 must not save configuration')
    monkeypatch.setattr('config_store.save_config', forbidden_save)
    listing = registry.list()
    assert listing['configuration_valid']
    parsed = listing['profiles'][0]
    assert parsed['stdio_mode'] == 'one_shot'
    assert parsed['id'] == old['id'] and parsed['trust'] == old['trust']
    assert registry.resolve('local')['profile'] == parsed
    assert registry.trust_decision('local', 'lookup', SCHEMA_SHA)['trusted']
    assert store.snapshot() == before and path.read_bytes() == disk
    assert store.data['external_mcp']['version'] == 1


def test_explicit_one_shot_write_upgrades_v2_and_preserves_trust(legacy):
    registry, store, path, old = legacy
    before = registry.list()
    updated = registry.update(old['id'], editable(old, stdio_mode='one_shot'), before['revision'])
    parsed = updated['profiles'][0]
    assert parsed['stdio_mode'] == 'one_shot'
    assert parsed['id'] == old['id'] and parsed['trust'] == old['trust']
    assert parsed['revision'] != old['revision']
    assert updated['revision'] != before['revision']
    assert store.data['external_mcp']['version'] == 2
    assert json.loads(path.read_text(encoding='utf-8'))['external_mcp']['version'] == 2
    assert ExternalMCPRegistry(ConfigStore(load_config(path), path)).list() == updated


def test_persistent_write_and_return_to_one_shot_do_not_resurrect_trust(legacy):
    registry, store, path, old = legacy
    first = registry.list()
    changed = registry.update(old['id'], editable(old, stdio_mode='persistent'), first['revision'])
    row = changed['profiles'][0]
    assert row['stdio_mode'] == 'persistent' and row['id'] == old['id']
    assert row['enabled'] is True and 'trust' not in row
    assert store.data['external_mcp']['version'] == 2
    with pytest.raises(ProfileConflict):
        registry.update(old['id'], editable(old, stdio_mode='one_shot'), first['revision'])
    restored = registry.update(old['id'], editable(row, stdio_mode='one_shot'), changed['revision'])
    assert 'trust' not in restored['profiles'][0]
    assert restored['profiles'][0]['id'] == old['id']
    assert ExternalMCPRegistry(ConfigStore(load_config(path), path)).list() == restored


@pytest.mark.parametrize('mode', ['one_shot', 'persistent'])
def test_failed_v2_save_restores_memory_disk_revision_and_trust(legacy, monkeypatch, mode):
    registry, store, path, old = legacy
    listing, before, disk = registry.list(), store.snapshot(), path.read_bytes()
    writes = []
    def fail(config, target):
        writes.append(copy.deepcopy(config))
        raise OSError('simulated save failure')
    monkeypatch.setattr('config_store.save_config', fail)
    with pytest.raises(OSError, match='simulated save failure'):
        registry.update(old['id'], editable(old, stdio_mode=mode), listing['revision'])
    assert len(writes) == 1, 'Must reach the persistence boundary, not fail at validation'
    assert writes[0]['external_mcp']['version'] == 2
    assert store.snapshot() == before and path.read_bytes() == disk
    assert registry.list() == listing
    assert registry.trust_decision('local', 'lookup', SCHEMA_SHA)['trusted']


def test_existing_v2_persistent_read_is_detached_and_does_not_save(legacy, monkeypatch):
    registry, store, path, old = legacy
    row = editable(old, stdio_mode='persistent')
    row.update(id=old['id'], revision=old['revision'])
    store.data['external_mcp'].update(version=2, profiles=[row])
    store.save()
    before, disk = store.snapshot(), path.read_bytes()
    def forbidden_save(*args, **kwargs):
        pytest.fail('Reading v2 must not save configuration')
    monkeypatch.setattr('config_store.save_config', forbidden_save)
    listing = registry.list()
    assert listing['configuration_valid']
    resolved = registry.resolve('local')
    assert resolved['profile']['stdio_mode'] == 'persistent'
    resolved['profile']['args'].append('tampered')
    assert registry.list() == listing
    assert store.snapshot() == before and path.read_bytes() == disk


@pytest.mark.parametrize('patch', [
    {'version': 3}, {'version': True}, {'version': 2, 'extra': 'unsupported'},
    {'version': 2, 'profiles': [{'alias': 'broken'}]},
])
def test_unknown_or_corrupt_config_remains_unchanged(legacy, patch):
    registry, store, path, _ = legacy
    store.data['external_mcp'].update(copy.deepcopy(patch))
    store.save()
    before, disk = store.snapshot(), path.read_bytes()
    assert not registry.list()['configuration_valid']
    with pytest.raises(ProfileError):
        registry.add(stdio(stdio_mode='persistent'), 'invalid-revision')
    assert store.snapshot() == before and path.read_bytes() == disk
