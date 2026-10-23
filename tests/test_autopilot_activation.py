"""Opt-in composition, restart and bounded lifecycle without production main."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from roots import Zones
from roots_registry import RootsRegistry
from server.autopilot_activation import configured_autopilot
from server.session_activation import configured_work_session, stop_work_session
from state import load_config
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore


@pytest.mark.parametrize('config', [{}, {'autopilot': {}}, {'autopilot': {'enabled': False}}])
def test_disabled_creates_nothing(tmp_path, config):
    assert configured_autopilot(config, tmp_path / 'audit.db', None, None, None, None) is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('section', [None, [], True, {'enabled': 1}, {'enabled': 'true'}, {'unknown': False}])
def test_invalid_flag_is_not_legacy_fallback(tmp_path, section):
    with pytest.raises(ValueError):
        configured_autopilot({'autopilot': section}, tmp_path / 'audit.db', None, None, None, None)
    assert not (tmp_path / 'audit.db').exists()


def test_enabled_requires_sessions(tmp_path):
    with pytest.raises(ValueError, match='work_sessions'):
        configured_autopilot({'autopilot': {'enabled': True}}, tmp_path / 'audit.db', None, None, None, None)
    assert not (tmp_path / 'audit.db').exists()


@contextmanager
def composition(tmp_path):
    path = tmp_path / 'config.json'
    config = load_config(path)
    config.update(work_sessions={'enabled': True}, autopilot={'enabled': True})
    store = ConfigStore(config, path)
    folder = tmp_path / 'app'
    folder.mkdir(exist_ok=True)
    roots = RootsRegistry(store, zones=Zones(), central_dir=tmp_path / 'central')
    if not roots.list():
        roots.add(folder, 'rw', alias='app')
    work = configured_work_session(config, tmp_path / 'sessions.db', roots)
    tasks = TaskStore(str(tmp_path / 'tasks.db'))
    manager = TaskManager(tasks, ApprovalService(), session_controller=work.controller)
    admin = configured_autopilot(config, tmp_path / 'audit.db', work, store, roots, manager)
    e = SimpleNamespace(admin=admin, work=work, tasks=tasks, manager=manager, store=store)
    try:
        yield e
    finally:
        assert stop_work_session(manager, work, budget=5)
        tasks.close()


def prepare(e):
    e.work.admin.start(['app'])
    e.work.admin.pause(e.work.controller.snapshot()['session_id'], e.work.controller.snapshot()['revision'])
    state = e.admin.status()
    identity = {key: state[key] for key in ('session_id', 'revision', 'decision_revision')}
    preview = e.admin.preview(dict(identity, goal='Read app.', plan='Read approved files.', limits={},
        policy={'version': 1, 'rules': [dict(id='read', kind='files', root='root:app', tools=['read_file'],
                                             scope={'kind': 'tree', 'path': '.'}, overwrite=False)]}))
    e.admin.action('approve', dict(identity, confirmed=True, preview_id=preview['preview_id']))
    return identity


def test_real_bootstrap_is_detached_until_explicit_lifecycle_start(tmp_path):
    with composition(tmp_path) as e:
        assert e.admin.controller._watcher is None
        assert e.work.controller.snapshot()['session_id'] is None
        assert e.admin.status()['state'] == 'disabled'
        prepare(e)
        e.admin.controller.start_watchdog()
        assert e.admin.controller._watcher.is_alive()
        with pytest.raises(ValueError):
            e.admin.controller.start_watchdog()


def test_idle_expiry_watchdog_pauses_without_model_call(tmp_path):
    with composition(tmp_path) as e:
        prepare(e)
        e.manager.prepare_session_resume('', e.work.controller.snapshot()['revision'])
        context = e.work.controller.get_resume_context()
        e.work.controller.confirm_resume_plan('Read app.', context['revision'], context['preparation_token'])
        now = [100.0]
        controller = e.admin.controller
        # Both ledger and controller share the same controlled monotonic clock.
        ledger = controller._ledger
        deadline = ledger._deadline
        ledger._clock = lambda: now[0]
        now[0] = deadline + 1
        # Use the session store notification path, not an LLM polling loop.
        import threading
        expired = threading.Event()
        original = controller.tick
        def tick():
            original()
            if controller.snapshot()['state'] == 'expired':
                expired.set()
        controller.tick = tick
        controller.start_watchdog()
        assert expired.wait(3)
        assert e.work.controller.snapshot()['execution_blocked']


def test_restart_does_not_restore_authority_or_rules(tmp_path):
    with composition(tmp_path) as e:
        prepare(e)
        assert e.admin.status()['policy'] is not None
    with composition(tmp_path) as e:
        assert e.admin.status()['policy'] is None
        assert e.admin.status()['state'] == 'disabled'
        assert e.work.controller.snapshot()['session_id'] is None


def test_shutdown_retains_stores_until_direct_action_finishes(tmp_path):
    with composition(tmp_path) as e:
        e.work.admin.start(['app'])
        token = e.work.controller.begin_action()
        assert not stop_work_session(e.manager, e.work, budget=0)
        assert e.work.controller.snapshot()['session_id']
        e.work.controller.finish_action(token)


def test_bootstrap_failure_is_fixed_and_never_runs_watchdog(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError('PRIVATE-PATH')
    monkeypatch.setattr('server.autopilot_activation.AuditStore', fail)
    with pytest.raises(ValueError, match=r'^Autopilot bootstrap failed\.$'):
        configured_autopilot({'autopilot': {'enabled': True}}, tmp_path / 'audit.db',
                             SimpleNamespace(controller=None), None, None, None)
