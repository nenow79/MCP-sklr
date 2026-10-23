"""OKF lifecycle with real temporary stores/queue; no main, server or network."""
import threading
from types import SimpleNamespace

import pytest

from autopilot.context import CENTRAL_KNOWLEDGE, MAINTENANCE
from config_store import ConfigStore
from okf_engine.service import KnowledgeError
from roots_registry import RootsRegistry
from server.knowledge_admin import KnowledgeAdmin
from server.knowledge_tools import create_service, start_background_refresh
from server.session_admin import SessionAdmin
from server.session_tools import wrap_session_tool
from tasks.approval import ApprovalService
from tasks.manager import TaskManager, TaskStateError
from tasks.resume_store import ResumeStore
from tasks.session_controller import SessionController
from tasks.session_store import SessionStore
from tasks.store import TaskStore


@pytest.fixture
def env(tmp_path):
    folders = {name: tmp_path / name for name in ('central', 'a', 'b', 'outside')}
    for name, folder in folders.items():
        target = folder if name == 'central' else folder / 'docs'
        target.mkdir(parents=True)
        (target / 'note.md').write_text('---\ntype: Reference\n---\n# Shared phrase\n' + name,
                                      encoding='utf-8')
    config = {'skills': {'central_dir': str(tmp_path / 'skills')},
              'roots': [{'alias': name, 'path': str(folder), 'mode': 'rw', 'default': name == 'a'}
                        for name, folder in folders.items()],
              'knowledge': {'bundles': [{'id': 'general', 'label': 'General', 'scope': 'central',
                                        'root': 'central', 'root_path': str(folders['central']),
                                        'subpath': '', 'enabled': True}]}}
    store = ConfigStore(config, tmp_path / 'config.json')
    roots = RootsRegistry(store)
    sessions = SessionStore(tmp_path / 'sessions.db')
    controller = SessionController(sessions, resumes=ResumeStore())
    tasks = TaskStore(str(tmp_path / 'tasks.db'))
    manager = TaskManager(tasks, ApprovalService(), max_parallel=2, session_controller=controller)
    admin = SessionAdmin(sessions, controller, roots)
    admin.attach_manager(manager)
    service = create_service(store, roots, manager, lambda: True)
    e = SimpleNamespace(store=store, roots=roots, sessions=sessions, controller=controller,
                        tasks=tasks, manager=manager, admin=admin, service=service, folders=folders,
                        release=[])
    try:
        yield e
    finally:
        for release in e.release:
            release.set()
        service.close()
        manager.shutdown()
        for worker in manager._workers:
            worker.join(5)
        assert not any(worker.is_alive() for worker in manager._workers)
        tasks.close()
        sessions.close()


def settle(e):
    for task in e.manager.list(limit=100):
        assert e.manager.wait(task.id, 5).is_terminal
    with e.manager._cond:
        assert e.manager._cond.wait_for(lambda: not e.controller.snapshot()['running_actions'], timeout=5)


def ids(e):
    return {item['root']: item['id'] for item in KnowledgeAdmin(e.store, e.roots, e.service).list()['bundles']}


def resume(e):
    state = e.controller.snapshot()
    e.manager.prepare_session_resume('', state['revision'], allow_missing_checkpoint=True)
    context = e.controller.get_resume_context()
    return e.controller.confirm_resume_plan('Continue work.', context['revision'], context['preparation_token'])


def test_startup_refreshes_only_central_without_selecting_session(env):
    result = start_background_refresh(env.service, env.manager)
    assert set(result) == {'general'}
    settle(env)
    assert env.controller.snapshot()['session_id'] is None
    assert env.service.index.state('general').state == 'ready'
    assert env.sessions.get_task_link(result['general']) is None
    rows = KnowledgeAdmin(env.store, env.roots, env.service).list()['bundles']
    assert all(row['reason'] == 'session_not_selected' for row in rows if row['scope'] == 'project')
    assert env.service.search(query='shared', scope='project', refresh='cached')['results'] == []


def test_start_indexes_all_session_projects_and_excludes_other_registered_projects(env):
    start_background_refresh(env.service, env.manager)
    state = env.admin.start(['a', 'b'])
    settle(env)
    result = env.service.search(query='shared', refresh='cached')
    assert {hit['bundle_id'] for hit in result['results']} == {'general', ids(env)['a'], ids(env)['b']}
    assert len(env.manager.list()) == 3
    for task in env.manager.list():
        link = env.sessions.get_task_link(task.id)
        assert (link is None) == (task.payload['bundle_id'] == 'general')
        if link:
            assert link.session_id == state['session_id']
    with pytest.raises(KnowledgeError, match='BUNDLE_UNAVAILABLE'):
        env.service.get(ids(env)['outside'], 'note')


def test_central_is_preserved_and_can_refresh_while_paused_but_model_is_blocked(env):
    start_background_refresh(env.service, env.manager)
    state = env.admin.start(['a'])
    settle(env)
    previous = env.service.index.snapshot('general')
    env.controller.request_pause(state['revision'])
    env.store.update(lambda config: config['roots'].append(
        {'alias': 'unrelated', 'path': str(env.folders['a'].parent / 'unrelated'), 'mode': 'ro'}))
    env.service.bundles()
    assert env.service.index.snapshot('general') is previous
    task = env.service.refresh_bundle('general')['task_id']
    assert env.manager.wait(task, 5).status.value == 'done'
    assert env.sessions.get_task_link(task) is None

    def knowledge_search():
        return {'success': True, 'data': env.service.search(query='shared', scope='central')}

    blocked = wrap_session_tool(knowledge_search, env.controller)()
    assert not blocked['success'] and blocked['session_control']['code'] == 'session_paused'


def test_folder_replacement_refreshes_only_after_confirmed_resume(env):
    start_background_refresh(env.service, env.manager)
    state = env.admin.start(['a'])
    settle(env)
    paused = env.controller.request_pause(state['revision'])
    previous = env.service.index.snapshot('general')
    removed_id = ids(env)['a']
    original_count = len(env.manager.list())
    # Same panel integration path: config mutation + durable membership update.
    env.admin.change_folders(lambda: env.roots.remove('a'), paused, removing='a')
    env.service.bundles()
    assert env.service.index.snapshot('general') is previous
    assert len(env.manager.list()) == original_count
    assert env.service.index.snapshot(removed_id) is None
    resume(env)
    settle(env)
    assert env.service.index.snapshot('general') is previous
    assert {hit['bundle_id'] for hit in env.service.search(query='shared', scope='project',
                                                         refresh='cached')['results']} == {
        ids(env)['b'], ids(env)['outside']}


def test_end_discards_projects_but_retains_central_for_new_session(env):
    start_background_refresh(env.service, env.manager)
    state = env.admin.start(['a'])
    settle(env)
    previous = env.service.index.snapshot('general')
    env.admin.end(state['session_id'], state['revision'])
    assert {row['id'] for row in env.service.bundles()['bundles']} == {'general'}
    assert env.service.index.snapshot('general') is previous
    assert env.service.index.snapshot(ids(env)['a']) is None
    env.admin.start(['b'])
    settle(env)
    assert {row['id'] for row in env.service.bundles()['bundles']} == {'general', ids(env)['b']}


def test_early_panel_start_before_cache_initialization_is_caught_by_bootstrap(env):
    env.admin.start(['a'])
    assert env.manager.list() == []
    assert set(start_background_refresh(env.service, env.manager)) == {'general', ids(env)['a']}
    settle(env)


def test_recovery_does_not_refresh_projects_before_resume_confirmation(env):
    item = env.sessions.create(['a'])
    env.sessions.recover_after_restart()
    saved = env.sessions.get(item.id)
    env.admin.recover(saved.id, saved.revision, True)
    assert set(start_background_refresh(env.service, env.manager)) == {'general'}
    settle(env)
    resume(env)
    settle(env)
    assert env.service.index.state(ids(env)['a']).state == 'ready'


def test_repeated_bind_does_not_schedule_more_refreshes(env):
    start_background_refresh(env.service, env.manager)
    state = env.admin.start(['a'])
    settle(env)
    count = len(env.manager.list())
    env.controller.bind(state['session_id'], state['revision'])
    assert len(env.manager.list()) == count


@pytest.mark.parametrize('change', ['disable', 'root', 'limits'])
def test_change_then_restore_cannot_publish_old_central_refresh(env, monkeypatch, change):
    from okf_engine import service as module
    entered, release = threading.Event(), threading.Event()
    env.release.append(release)
    original = module.scan_bundle

    def blocked(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(module, 'scan_bundle', blocked)
    task_id = start_background_refresh(env.service, env.manager)['general']
    assert entered.wait(5)
    baseline = env.store.snapshot()

    def mutate(config):
        if change == 'disable':
            config['knowledge']['bundles'][0]['enabled'] = False
        elif change == 'root':
            config['roots'][0]['mode'] = 'ro'
        else:
            config['knowledge']['limits'] = {'max_documents_per_bundle': 1}

    env.store.update(mutate)
    env.store.update(lambda config: (config.clear(), config.update(baseline)))
    release.set()
    assert env.manager.wait(task_id, 5).status.value == 'cancelled'
    assert env.service.index.snapshot('general') is None


def test_unrelated_folder_change_during_central_scan_does_not_cancel_it(env, monkeypatch):
    from okf_engine import service as module
    entered, release = threading.Event(), threading.Event()
    env.release.append(release)
    original = module.scan_bundle

    def blocked(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(module, 'scan_bundle', blocked)
    task_id = start_background_refresh(env.service, env.manager)['general']
    assert entered.wait(5)
    env.store.update(lambda config: config['roots'][1].update(mode='ro'))
    env.service.bundles()
    release.set()
    assert env.manager.wait(task_id, 5).status.value == 'done'


def test_pending_project_scan_cannot_publish_after_session_end(env, monkeypatch):
    from okf_engine import service as module
    entered, release = threading.Event(), threading.Event()
    env.release.append(release)
    original = module.scan_bundle

    def blocked(reader, bundle_id, *args, **kwargs):
        result = original(reader, bundle_id, *args, **kwargs)
        if bundle_id != 'general':
            entered.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(module, 'scan_bundle', blocked)
    start_background_refresh(env.service, env.manager)
    state = env.admin.start(['a'])
    assert entered.wait(5)
    env.admin.end(state['session_id'], state['revision'])
    release.set()
    settle(env)
    assert env.controller.snapshot()['session_id'] is None
    assert env.service.index.snapshot(ids(env)['a']) is None
    assert env.service.index.state('general').state == 'ready'


def test_only_explicit_central_maintenance_can_skip_session_admission(env):
    fields = {'kind': 'knowledge_refresh', 'sensitive': True, 'offer_trust': False}
    for capability in (None, MAINTENANCE, 'central'):
        with pytest.raises(TaskStateError):
            env.manager.submit(fields, lambda cancel: {}, False, internal_maintenance=capability)
    with pytest.raises(TaskStateError):
        env.manager.submit(dict(fields, kind='create_file'), lambda cancel: {}, False,
                           internal_maintenance=CENTRAL_KNOWLEDGE)
    assert env.manager.list() == []


def test_sessionless_queue_still_obeys_shutdown(env):
    env.manager.shutdown()
    with pytest.raises(TaskStateError):
        env.manager.submit({'kind': 'knowledge_refresh', 'sensitive': True, 'offer_trust': False},
                           lambda cancel: {}, False, internal_maintenance=CENTRAL_KNOWLEDGE)
