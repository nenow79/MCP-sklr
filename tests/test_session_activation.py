"""Activation composition without importing main or touching local databases.

Registrar stubs do not certify the real FastMCP transport/catalogue. That needs
an explicit runtime smoke after the user enables the feature and restarts.
"""
import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from server.session_activation import (
    configured_work_session, session_registrars, verify_session_registration, stop_work_session,
)
from server.session_bootstrap import SessionBootstrapError
from server.session_tools import CONTROL_TOOLS, WORK_TOOLS
from tasks.approval import ApprovalService
from tasks.manager import TaskManager, TaskStateError
from tasks.store import TaskStore
from state import DEFAULT_CONFIG

MAIN = Path(__file__).resolve().parents[1] / 'main.py'
ROOTS = SimpleNamespace(list_status=lambda: [{'alias': 'sklr', 'valid': True}])


@pytest.fixture
def runtime(tmp_path):
    built = configured_work_session({'work_sessions': {'enabled': True}},
                                    tmp_path / 'sessions.db', ROOTS)
    yield built
    built.close()


@pytest.mark.parametrize('config', [{}, {'work_sessions': {}}, {'work_sessions': {'enabled': False}}])
def test_disabled_no_file_no_runtime(tmp_path, config):
    path = tmp_path / 'sessions.db'
    assert configured_work_session(config, path, ROOTS) is None
    assert not path.exists()
    assert DEFAULT_CONFIG['work_sessions'] == {'enabled': False}


@pytest.mark.parametrize('section', [None, [], True, {'enabled': 1}, {'enabled': 'true'},
                                     {'enabled': None}, {'enabled': True, 'bypass': True}])
def test_invalid_configuration_does_not_fall_back(tmp_path, section):
    path = tmp_path / 'sessions.db'
    with pytest.raises(ValueError):
        configured_work_session({'work_sessions': section}, path, ROOTS)
    assert not path.exists()


def test_bootstrap_failure_does_not_fall_back(tmp_path):
    with pytest.raises(SessionBootstrapError):
        configured_work_session({'work_sessions': {'enabled': True}}, tmp_path, ROOTS)


def registrars(runtime):
    regular, confidential = {}, {}
    def reg(function):
        regular[function.__name__] = function
        return function
    def conf(function):
        confidential[function.__name__] = function
        return function
    wrapped, wrapped_conf, names = session_registrars(reg, conf, runtime)
    return wrapped, wrapped_conf, names, regular, confidential


def test_control_registration_confidentiality_and_single_wrapper(runtime):
    reg, conf, names, regular, confidential = registrars(runtime)
    assert set(regular) == {'get_session_state'}
    assert set(confidential) == {'get_resume_context', 'confirm_resume_plan'}
    assert 'save_session_checkpoint' not in names
    for function in list(regular.values()) + list(confidential.values()):
        assert hasattr(function, '__wrapped__')
        assert not hasattr(function.__wrapped__, '__wrapped__')
    assert 'session_id' not in inspect.signature(confidential['confirm_resume_plan']).parameters
    calls = []
    def read_file(filepath: str) -> Dict[str, Any]:
        calls.append(filepath)
        return {'success': True}
    tool = reg(read_file)
    assert inspect.signature(tool) == inspect.signature(read_file)
    control = tool('a')['session_control']
    assert control['code'] == 'session_not_selected'
    assert not any(key.startswith('checkpoint') for key in control)
    assert 'Start' in control['instruction'] and 'panelu' in control['instruction']
    assert 'nie jest jeszcze dostępne' not in control['instruction']
    assert calls == []
    assert regular['get_session_state']()['success']
    active = runtime.admin.start(['sklr'])
    assert tool('a')['success'] and calls == ['a']
    assert runtime.controller.snapshot()['running_actions'] == 0
    paused = runtime.admin.pause(active['session_id'], active['revision'])
    assert tool('b')['session_control']['code'] == 'session_paused'
    assert 'checkpoint' not in tool('b')['session_control']['instruction']
    assert calls == ['a']


def test_actual_main_decorator_names_match_full_classification(runtime):
    reg, conf, names, regular, confidential = registrars(runtime)
    tree = ast.parse(MAIN.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id in ('register_tool', 'register_confidential_tool'):
                def sample():
                    return {'success': True}
                sample.__name__ = node.name
                (conf if decorator.id == 'register_confidential_tool' else reg)(sample)
    assert verify_session_registration(runtime, names) is None
    assert names == set(CONTROL_TOOLS) | set(WORK_TOOLS)
    with pytest.raises(ValueError):
        verify_session_registration(runtime, names - {'read_file'})
    with pytest.raises(ValueError):
        verify_session_registration(runtime, names | {'unclassified_tool'})


def test_disabled_registrars_preserve_legacy_behavior():
    reg, conf, names, regular, confidential = registrars(None)
    assert not regular and not confidential
    def read_file():
        return {'success': True}
    assert reg(read_file) is read_file
    assert regular['read_file']()['success']
    assert names == {'read_file'}
    verify_session_registration(None, names)


def test_shared_manager_admin_controller_and_panel(tmp_path, runtime):
    from panel.app import create_panel_app
    from panel.auth import PanelAuth
    store = TaskStore(str(tmp_path / 'tasks.db'))
    approval = ApprovalService()
    manager = TaskManager(store, approval, session_controller=runtime.controller)
    try:
        app = create_panel_app(manager, approval, PanelAuth(port=8102), work_sessions=runtime.admin)
        assert runtime.admin._manager is manager
        assert manager._sessions is runtime.controller
        assert runtime.admin._controller is runtime.controller
        assert any(route.path == '/api/work-session/prepare-resume' for route in app.routes)
        before = tuple(store._conn.iterdump())
        calls = []
        def executor(event):
            calls.append(True)
            return {'exit_code': 0}
        with pytest.raises(TaskStateError, match=r'^Sesja wstrzymana lub przypisanie zadania niedostępne\.$'):
            manager.submit({}, executor, True)
        assert tuple(store._conn.iterdump()) == before
        assert manager._ctx == {}
        assert manager._queue.empty()
        assert manager._workers == []
        assert calls == []
    finally:
        manager.shutdown()
        for worker in manager._workers:
            worker.join(timeout=5)
        store.close()


def fake_shutdown_runtime(running=False, tokens=False):
    import threading
    calls = []
    worker = SimpleNamespace(join=lambda timeout: calls.append(('join', timeout)),
                             is_alive=lambda: running)
    manager = SimpleNamespace(shutdown=lambda: calls.append(('shutdown',)), _workers=[worker])
    controller = SimpleNamespace(_lock=threading.RLock(), _tokens={object()} if tokens else set(),
                                 _blocked=False)
    runtime = SimpleNamespace(controller=controller, close=lambda: calls.append(('close',)))
    return manager, runtime, calls


@pytest.mark.parametrize('running,tokens', [(True, False), (False, True)])
def test_shutdown_with_execution_does_not_close_store(running, tokens):
    manager, runtime, calls = fake_shutdown_runtime(running, tokens)
    assert stop_work_session(manager, runtime, budget=0) is False
    assert calls[0] == ('shutdown',)
    assert ('close',) not in calls


def test_shutdown_closes_only_after_workers_and_actions_end():
    manager, runtime, calls = fake_shutdown_runtime()
    assert stop_work_session(manager, runtime, budget=0) is True
    assert calls == [('shutdown',), ('join', 0), ('close',)]
    assert runtime.controller._blocked


def test_main_integration_ast_and_registration_order():
    source = MAIN.read_text(encoding='utf-8-sig')
    ast.parse(source)
    assert 'WORK_SESSION = configured_work_session(CONFIG, WORK_SESSIONS_DB, ROOTS)' in source
    assert 'session_controller=WORK_SESSION.controller if WORK_SESSION is not None else None' in source
    assert 'work_sessions=WORK_SESSION.admin if WORK_SESSION is not None else None' in source
    assert source.index('= session_registrars(') < source.index('@register_tool')
    assert source.index('verify_session_registration(WORK_SESSION, SESSION_TOOL_NAMES)') > source.index('def knowledge_convert(')
    assert source.count('stop_work_session(TASK_MANAGER, WORK_SESSION, budget=5)') == 3
    assert 'work_sessions.db*' in MAIN.with_name('.gitignore').read_text(encoding='utf-8')
