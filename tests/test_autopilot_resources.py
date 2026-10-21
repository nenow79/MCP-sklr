"""Generations: config/registry ABA and actual access observations, no dispatch."""
import pytest

from autopilot.resources import ResourceGenerations
from config_store import ConfigStore
from skills_engine.registry import SkillRegistry


def test_observation_is_stable_until_change_and_never_revives_after_aba():
    generations = ResourceGenerations()
    key = ('root', '/project')
    initial = generations.observe(key, 'a')
    assert generations.observe(key, 'a') == initial
    changed = generations.observe(key, 'b')
    restored = generations.observe(key, 'a')
    assert len({initial, changed, restored}) == 3


def test_unavailable_then_restored_has_new_epoch():
    generations = ResourceGenerations()
    before = generations.observe(('root', '/p'), 'same')
    generations.observe(('root', '/p'), 'same', available=False)
    assert generations.observe(('root', '/p'), 'same') != before


def test_restart_changes_epochs_and_private_digests():
    first, second = ResourceGenerations(), ResourceGenerations()
    assert first.observe(('root', '/p'), 'same') != second.observe(('root', '/p'), 'same')
    assert first.digest({'secret': 'private'}) != second.digest({'secret': 'private'})


def test_config_mutation_and_restore_between_observations_invalidates(tmp_path):
    store = ConfigStore({'enabled': True}, tmp_path / 'config.json')
    generations = ResourceGenerations(store)
    before = generations.observe(('profile', 'x'), 'same')
    store.update(lambda data: data.update(enabled=False))
    store.update(lambda data: data.update(enabled=True))
    assert generations.observe(('profile', 'x'), 'same') != before


def test_uncertain_config_save_invalidates_even_after_memory_rollback(tmp_path, monkeypatch):
    store = ConfigStore({'enabled': True}, tmp_path / 'config.json')
    generations = ResourceGenerations(store)
    before = generations.observe(('profile', 'x'), 'same')
    def fail(*args):
        raise OSError('PRIVATE')
    monkeypatch.setattr('config_store.save_config', fail)
    with pytest.raises(OSError):
        store.update(lambda data: data.update(enabled=False))
    assert store.snapshot()['enabled'] is True
    assert generations.observe(('profile', 'x'), 'same') != before


@pytest.mark.parametrize('setter,first,second', [
    ('set_disabled', [], ['demo']), ('set_dev_enabled', [], ['demo']),
    ('set_project_root', None, '/tmp/project'), ('set_central_dir', None, '/tmp/central'),
    ('set_dev_dirs', [], ['/tmp/dev']),
])
def test_registry_changes_invalidate_without_an_intermediate_model_call(setter, first, second):
    registry = SkillRegistry()
    generations = ResourceGenerations()
    registry.add_change_listener(generations.invalidate)
    getattr(registry, setter)(first)
    epoch = generations.observe(('skill', 'demo'), 'same')
    getattr(registry, setter)(second)
    getattr(registry, setter)(first)
    assert generations.observe(('skill', 'demo'), 'same') != epoch
