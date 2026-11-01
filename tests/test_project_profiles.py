"""Project profiles without server startup, GUI, local config or real execution."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from roots import RootError, Zones
from roots_registry import RootsRegistry
from server.project_profiles import ProfileConflict, ProjectProfiles
from tasks.session_controller import SessionController
from tasks.session_store import SessionStore
from server.session_admin import SessionAdmin
from tasks.session_models import SessionConflict


@pytest.fixture
def profiles(tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    central = tmp_path / 'central'
    for path in (first, second, central):
        path.mkdir()
    config = ConfigStore({'roots': []}, tmp_path / 'config.json')
    roots = RootsRegistry(config, zones=Zones(), central_dir=central)
    roots.add(str(first), 'rw', 'src', 'Source')
    roots.add(str(second), 'ro', 'data', 'Reference')
    return ProjectProfiles(config, roots)


def change(service, action, **fields):
    return service.change(action, dict(revision=service.list()['revision'], confirmed=True, **fields))


def test_create_and_backup_preserve_full_roots(profiles):
    before = profiles._config._path.read_bytes()
    result = change(profiles, 'create', name='Projekt sklr')
    item = result['profiles'][0]
    assert item['name'] == 'Projekt sklr' and item['version'] == 1
    assert item['roots'] == profiles._config.snapshot()['roots']
    assert item['matches_current'] and item['unavailable'] == []
    assert result['selected'] == item['id']
    backup = profiles._config._path.with_name('config.json.pre-project-profiles.bak')
    assert backup.read_bytes() == before
    change(profiles, 'rename', id=item['id'], name='sklr')
    assert backup.read_bytes() == before


def test_select_restores_roots_default_and_not_other_configuration(profiles, tmp_path):
    saved = change(profiles, 'create', name='QAP')['profiles'][0]
    profiles._config.update(lambda cfg: cfg.update(autopilot={'enabled': False}, custom='keep'))
    profiles._roots.remove('data')
    profiles._roots.set_default('src')
    assert not profiles.list()['profiles'][0]['matches_current']
    result = change(profiles, 'select', id=saved['id'])
    assert result['profiles'][0]['matches_current']
    cfg = profiles._config.snapshot()
    assert cfg['roots'] == saved['roots'] and cfg['custom'] == 'keep'
    assert cfg['autopilot'] == {'enabled': False}


def test_stale_root_view_cannot_select_or_save(profiles):
    revision = profiles.list()['revision']
    profiles._roots.update('src', description='changed')
    with pytest.raises(ProfileConflict):
        profiles.change('create', {'revision': revision, 'confirmed': True, 'name': 'QAP'})
    assert profiles.list()['profiles'] == []


def test_rename_keeps_id_and_scope_version_update_changes_version(profiles):
    item = change(profiles, 'create', name='QAP')['profiles'][0]
    renamed = change(profiles, 'rename', id=item['id'], name='QAP 2')['profiles'][0]
    assert renamed['id'] == item['id'] and renamed['version'] == 1
    profiles._roots.update('src', mode='ro')
    updated = change(profiles, 'update', id=item['id'])['profiles'][0]
    assert updated['version'] == 2
    assert change(profiles, 'update', id=item['id'])['profiles'][0]['version'] == 2


def test_delete_does_not_delete_roots_files_or_live_configuration(profiles):
    item = change(profiles, 'create', name='QAP')['profiles'][0]
    before = profiles._config.snapshot()['roots']
    result = change(profiles, 'delete', id=item['id'])
    assert result['profiles'] == [] and result['selected'] is None
    assert profiles._config.snapshot()['roots'] == before
    assert all(Path(root['path']).is_dir() for root in before)


def test_missing_directory_prevents_selection_without_config_change(profiles):
    item = change(profiles, 'create', name='QAP')['profiles'][0]
    path = profiles._roots.get('data').path_obj
    path.rmdir()
    before = profiles._config.snapshot()
    assert profiles.list()['profiles'][0]['unavailable'] == ['data']
    with pytest.raises(RootError):
        change(profiles, 'select', id=item['id'])
    assert profiles._config.snapshot() == before


@pytest.mark.parametrize('name', ['', ' ', 'x' * 101, 'QAP\n', 'QAP\u202e'])
def test_invalid_names_rejected(profiles, name):
    with pytest.raises(ValueError):
        change(profiles, 'create', name=name)
    assert profiles.list()['profiles'] == []


def test_confirmation_and_duplicate_name(profiles):
    change(profiles, 'create', name='QAP')
    with pytest.raises(ProfileConflict):
        change(profiles, 'create', name='qap')
    with pytest.raises(ValueError):
        profiles.change('create', {'revision': profiles.list()['revision'], 'confirmed': False, 'name': 'Other'})


def test_save_failure_rolls_back_memory_and_preserves_backup(profiles, monkeypatch):
    before = profiles._config.snapshot()
    def fail(*args):
        raise OSError('simulated write failure')
    monkeypatch.setattr('config_store.save_config', fail)
    with pytest.raises(OSError):
        change(profiles, 'create', name='QAP')
    assert profiles._config.snapshot() == before
    assert json.loads(profiles._config._path.with_name('config.json.pre-project-profiles.bak').read_text()) == before


def test_active_and_paused_sessions_prevent_profile_changes(profiles):
    store = SessionStore(':memory:')
    controller = SessionController(store)
    admin = SessionAdmin(store, controller, profiles._roots)
    profiles._sessions = admin
    try:
        state = admin.start(['src', 'data'])
        with pytest.raises(SessionConflict):
            change(profiles, 'create', name='QAP')
        admin.pause(state['session_id'], state['revision'])
        with pytest.raises(SessionConflict):
            change(profiles, 'create', name='QAP')
        assert profiles.list()['profiles'] == []
    finally:
        store.close()


def test_restart_retains_profiles_but_does_not_select_work_session(profiles):
    item = change(profiles, 'create', name='QAP')['profiles'][0]
    config = ConfigStore(json.loads(profiles._config._path.read_text()), profiles._config._path)
    registry = RootsRegistry(config, zones=Zones(), central_dir=profiles._roots._central())
    reopened = ProjectProfiles(config, registry)
    assert reopened.list()['selected'] == item['id']
    assert reopened.list()['profiles'][0]['matches_current']
    assert reopened._sessions is None


def test_no_session_mode_with_outstanding_tasks_cannot_change_project(profiles):
    profiles._manager = SimpleNamespace(_cond=threading.RLock(), _stopped=False,
                                       list=lambda **kwargs: [object()])
    with pytest.raises(SessionConflict):
        change(profiles, 'create', name='QAP')
    assert profiles.list()['profiles'] == []


def test_default_listener_runs_after_selection(profiles):
    item = change(profiles, 'create', name='QAP')['profiles'][0]
    profiles._roots.set_default('data')
    calls = []
    profiles._roots.set_default_listener(lambda old, new: calls.append((old.alias, new.alias)))
    change(profiles, 'select', id=item['id'])
    assert calls == [('data', 'src')]


@pytest.fixture
def startable(profiles):
    from tasks.approval import ApprovalService
    from tasks.manager import TaskManager
    from tasks.store import TaskStore
    sessions = SessionStore(':memory:')
    tasks = TaskStore(':memory:')
    controller = SessionController(sessions)
    admin = SessionAdmin(sessions, controller, profiles._roots)
    manager = TaskManager(tasks, ApprovalService(), session_controller=controller)
    admin.attach_manager(manager)
    profiles._sessions, profiles._manager = admin, manager
    try:
        yield profiles
    finally:
        manager.shutdown()
        for worker in manager._workers:
            worker.join(timeout=6)
        assert not any(worker.is_alive() for worker in manager._workers)
        tasks.close()
        sessions.close()


def test_fresh_start_pins_current_revision_and_all_roots(startable):
    view = startable.list()
    assert view['current_roots'] == startable._config.snapshot()['roots']
    result = startable.start(['src', 'data'], view['revision'])
    assert result['session_id'] and not result['execution_blocked']
    assert result['roots'] == ['src', 'data']
    with pytest.raises(SessionConflict):
        startable.start(['src', 'data'], view['revision'])


@pytest.mark.parametrize('changed', ['description', 'mode', 'default', 'profile'])
def test_stale_start_rejected_before_session_creation(startable, changed):
    revision = startable.list()['revision']
    if changed == 'description':
        startable._roots.update('src', description='new')
    elif changed == 'mode':
        startable._roots.update('src', mode='ro')
    elif changed == 'default':
        startable._roots.set_default('data')
    else:
        change(startable, 'create', name='new project')
    with pytest.raises(ProfileConflict):
        startable.start(['src', 'data'], revision)
    assert startable._sessions.status()['session_id'] is None
    assert startable._sessions._store.list_saved_sessions() == ([], False)


@pytest.mark.parametrize('aliases', [['src'], ['data', 'src'], ['unknown'], None])
def test_start_rejects_wrong_membership_without_creating_session(startable, aliases):
    with pytest.raises(ProfileConflict):
        startable.start(aliases, startable.list()['revision'])
    assert startable._sessions.status()['session_id'] is None


def test_malformed_profile_config_is_not_overwritten(profiles):
    profiles._config.update(lambda cfg: cfg.update(project_profiles={'schema': 999}))
    before = profiles._config.snapshot()
    with pytest.raises(ValueError):
        profiles.list()
    assert profiles._config.snapshot() == before
