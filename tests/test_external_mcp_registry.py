"""Registry persistence, fail-closed parsing and concurrent revisions."""
import copy
import json
import threading

import pytest

from config_store import ConfigStore
from external_mcp.profiles import ProfileConflict, ProfileError, ProfileNotFound
from external_mcp.registry import ExternalMCPRegistry
from state import load_config


@pytest.fixture
def env(tmp_path):
    path = tmp_path / 'config.json'
    store = ConfigStore(load_config(path), path)
    return ExternalMCPRegistry(store), store, path


def proposal(alias='remote'):
    return dict(alias=alias, transport='http', url='https://example.test/mcp',
                bearer_env='REMOTE_TOKEN')


def add(registry, alias='remote'):
    return registry.add(proposal(alias), registry.list()['revision'])


def test_empty_start_does_not_write(env):
    registry, store, path = env
    assert registry.list()['count'] == 0
    assert registry.list()['configuration_valid']
    assert store.data['external_mcp'] == {} and not path.exists()


def test_restart_disable_and_remove(env):
    registry, store, path = env
    first = add(registry)
    row = first['profiles'][0]
    assert not row['enabled']
    enabled = registry.set_enabled(row['id'], True, first['revision'])
    restarted = ExternalMCPRegistry(ConfigStore(load_config(path), path))
    assert restarted.list() == enabled
    assert restarted.resolve('remote')['profile']['enabled']
    disabled = restarted.set_enabled(row['id'], False, enabled['revision'])
    assert disabled['profiles'][0]['bearer_env'] == 'REMOTE_TOKEN'
    with pytest.raises(ProfileError, match='profile_disabled'):
        restarted.resolve('remote')
    assert restarted.resolve('remote', require_enabled=False)['profile']['id'] == row['id']
    removed = restarted.remove(row['id'], disabled['revision'])
    assert removed['count'] == 0
    assert ExternalMCPRegistry(ConfigStore(load_config(path), path)).list() == removed


def test_alias_update_preserves_id_and_changes_revision(env):
    registry, _, _ = env
    first = add(registry)
    row = first['profiles'][0]
    updated = registry.update(row['id'], proposal('renamed'), first['revision'])
    assert updated['profiles'][0]['id'] == row['id']
    assert updated['profiles'][0]['revision'] != row['revision']
    with pytest.raises(ProfileNotFound):
        registry.resolve('remote', require_enabled=False)
    with pytest.raises(ProfileConflict):
        registry.resolve('renamed', expected_revision=first['revision'], require_enabled=False)


def test_stale_add_and_duplicate_alias_do_not_write(env):
    registry, store, path = env
    stale = registry.list()['revision']
    first = add(registry)
    before, disk = store.snapshot(), path.read_bytes()
    with pytest.raises(ProfileConflict):
        registry.add(proposal('other'), stale)
    with pytest.raises(ProfileError, match='duplicate_profile'):
        registry.add(proposal(), first['revision'])
    assert store.snapshot() == before and path.read_bytes() == disk


@pytest.mark.parametrize('operation', ['add', 'update', 'enable', 'remove'])
def test_failed_save_rolls_back_memory_disk_and_revision(env, monkeypatch, operation):
    registry, store, path = env
    first = add(registry)
    row = first['profiles'][0]
    before, disk = store.snapshot(), path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr('config_store.save_config', fail)
    actions = {
        'add': lambda: registry.add(proposal('other'), first['revision']),
        'update': lambda: registry.update(row['id'], proposal('renamed'), first['revision']),
        'enable': lambda: registry.set_enabled(row['id'], True, first['revision']),
        'remove': lambda: registry.remove(row['id'], first['revision']),
    }
    with pytest.raises(OSError):
        actions[operation]()
    assert store.snapshot() == before and path.read_bytes() == disk
    assert registry.list() == first


@pytest.mark.parametrize('raw', [None, [], {'old': {'target': 'legacy'}},
    {'version': 2, 'generation': 'unsupported', 'profiles': []}])
def test_unknown_config_is_preserved_and_blocked(env, raw):
    registry, store, path = env
    store.update(lambda config: config.update(external_mcp=copy.deepcopy(raw)))
    disk = path.read_bytes()
    assert not registry.list()['configuration_valid']
    with pytest.raises(ProfileError):
        registry.add(proposal(), 'any')
    with pytest.raises(ProfileError):
        registry.resolve('remote')
    assert store.data['external_mcp'] == raw and path.read_bytes() == disk


def test_corrupt_or_duplicate_stored_profiles_fail_closed(env):
    registry, store, _ = env
    add(registry)
    store.data['external_mcp']['profiles'][0]['enabled'] = 'yes'
    assert not registry.list()['configuration_valid']
    store.data['external_mcp']['profiles'][0]['enabled'] = False
    store.data['external_mcp']['profiles'].append(copy.deepcopy(store.data['external_mcp']['profiles'][0]))
    assert not registry.list()['configuration_valid']


def test_unrelated_config_changes_do_not_invalidate_profiles(env):
    registry, store, _ = env
    first = add(registry)
    store.update(lambda config: config.update(roots=[], workspace={'path': 'other'}))
    assert registry.list() == first


def test_out_of_band_profile_change_invalidates_revision(env):
    registry, store, _ = env
    first = add(registry)
    store.data['external_mcp']['profiles'][0]['description'] = 'changed outside registry'
    with pytest.raises(ProfileConflict):
        registry.remove(first['profiles'][0]['id'], first['revision'])


def test_port_changes_revalidate_self_connection(env):
    registry, store, _ = env
    local = proposal()
    local['url'] = 'http://localhost:9000/mcp'
    first = registry.add(local, registry.list()['revision'])
    store.update(lambda config: config['server'].update(port=9000))
    assert not registry.list()['configuration_valid']
    with pytest.raises(ProfileError):
        registry.resolve('remote', require_enabled=False)


def test_returned_profiles_are_detached(env):
    registry, _, _ = env
    first = add(registry)
    first['profiles'][0]['alias'] = 'tampered'
    assert registry.list()['profiles'][0]['alias'] == 'remote'
    snapshot = registry.resolve('remote', require_enabled=False)
    snapshot['profile']['alias'] = 'tampered'
    assert registry.list()['profiles'][0]['alias'] == 'remote'


def test_remove_and_recreate_does_not_reuse_revision(env):
    registry, _, _ = env
    empty = registry.list()['revision']
    first = add(registry)
    removed = registry.remove(first['profiles'][0]['id'], first['revision'])
    assert removed['revision'] != empty


def test_simultaneous_writers_only_one_commits(env):
    registry, _, path = env
    revision = registry.list()['revision']
    barrier = threading.Barrier(2)
    outcomes = []
    def worker(alias):
        barrier.wait(timeout=5)
        try:
            registry.add(proposal(alias), revision)
            outcomes.append('saved')
        except ProfileConflict:
            outcomes.append('conflict')
    threads = [threading.Thread(target=worker, args=(alias,)) for alias in ('one', 'two')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ['conflict', 'saved']
    assert len(json.loads(path.read_text(encoding='utf-8'))['external_mcp']['profiles']) == 1


def test_secret_values_are_never_resolved(env, monkeypatch):
    registry, _, path = env
    monkeypatch.setenv('REMOTE_TOKEN', 'SENTINEL_NOT_FOR_CONFIG')
    first = add(registry)
    assert 'SENTINEL_NOT_FOR_CONFIG' not in path.read_text(encoding='utf-8')
    assert 'SENTINEL_NOT_FOR_CONFIG' not in json.dumps(first)
