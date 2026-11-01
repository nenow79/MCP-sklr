"""Detached P2e tests: bootstrap and registration helpers. No server, no main wiring."""
import ast
import inspect
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.session_bootstrap import SessionBootstrapError, build_work_session
from server.session_registration import (
    make_get_session_state, make_session_registrars, verify_classification)
from server.session_tools import CONTROL_TOOLS, WORK_TOOLS
from tasks.session_controller import SessionBlocked
from tasks.session_models import SessionState
from tasks.session_store import SessionStore
from tasks.store import TaskStore

ROOTS = SimpleNamespace(list_status=lambda: [{'alias': 'sklr', 'valid': True}])
MAIN = Path(__file__).resolve().parents[1] / 'main.py'


@pytest.fixture
def runtime(tmp_path):
    built = build_work_session(tmp_path / 'work_sessions.db', ROOTS, enabled=True)
    yield built
    built.close()


def test_disabled_builds_nothing_and_creates_no_file(tmp_path):
    path = tmp_path / 'work_sessions.db'
    assert build_work_session(path, ROOTS, enabled=False) is None
    assert not path.exists()


@pytest.mark.parametrize('value', ['true', 1, None])
def test_enabled_must_be_boolean(tmp_path, value):
    with pytest.raises(ValueError):
        build_work_session(tmp_path / 'work_sessions.db', ROOTS, enabled=value)
    assert not (tmp_path / 'work_sessions.db').exists()


def test_fresh_start_is_blocked_until_user_selects(runtime):
    assert runtime.recovered == 0
    state = runtime.controller.snapshot()
    assert state['session_id'] is None and state['execution_blocked']
    with pytest.raises(SessionBlocked):
        runtime.controller.begin_action()
    assert runtime.admin.status()['session_id'] is None


def test_restart_retires_stored_sessions_after_backup_and_does_not_select(tmp_path):
    path = tmp_path / 'work_sessions.db'
    seed = SessionStore(path)
    sid = seed.create(['sklr']).id
    seed.link_task(sid, 123, 1)
    seed.close()
    first = build_work_session(path, ROOTS, enabled=True)
    try:
        item = first.store.get(sid)
        assert first.recovered == 1
        assert item.state == SessionState.PAUSED and item.reason == 'server_restarted'
        assert item.checkpoint_state.value == 'unavailable' and item.revision == 2
        assert item.lifecycle == 'ended'
        assert first.store.get_task_link(123).session_id == sid
        assert first.store.list_saved_sessions() == ([], False)
        import sqlite3
        with closing(sqlite3.connect(str(path) + '.pre-project-profiles.bak')) as snapshot:
            assert snapshot.execute('SELECT lifecycle FROM work_sessions WHERE id=?', (sid,)).fetchone()[0] == 'open'
            assert snapshot.execute('SELECT session_id FROM session_task_links WHERE task_id=123').fetchone()[0] == sid
        state = first.controller.snapshot()
        assert state['session_id'] is None and state['execution_blocked']
        with pytest.raises(SessionBlocked):
            first.controller.begin_action()
    finally:
        first.close()
    # Direct call exposes any backup failure before bootstrap's safe error mapping.
    from server.session_bootstrap import backup_before_start
    backup_before_start(path)
    with closing(sqlite3.connect(str(path) + '.pre-project-profiles.bak')) as snapshot:
        assert snapshot.execute('SELECT lifecycle FROM work_sessions WHERE id=?', (sid,)).fetchone()[0] == 'ended'
        assert snapshot.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    second = build_work_session(path, ROOTS, enabled=True)
    try:
        assert second.recovered == 0
        assert second.store.get(sid).revision == 2
        assert second.store.get(sid).lifecycle == 'ended'
    finally:
        second.close()


def test_backup_includes_committed_wal_and_does_not_modify_source(tmp_path):
    from server.session_bootstrap import backup_before_start
    import sqlite3
    path = tmp_path / 'sessions.db'
    seed = SessionStore(path)
    try:
        item = seed.create(['sklr'])
        seed.link_task(item.id, 99, item.revision)
        backup_before_start(path)
        assert seed.get(item.id) == item
        with closing(sqlite3.connect(str(path) + '.pre-project-profiles.bak')) as snapshot:
            assert snapshot.execute('SELECT id FROM work_sessions').fetchone()[0] == item.id
            assert snapshot.execute('SELECT task_id FROM session_task_links').fetchone()[0] == 99
    finally:
        seed.close()


def test_backup_fsync_uses_writable_temporary_before_replace(tmp_path, monkeypatch):
    import server.session_bootstrap as bootstrap
    path = tmp_path / 'sessions.db'
    seed = SessionStore(path)
    seed.create(['sklr'])
    seed.close()
    events = []
    opened = {}
    original_open = Path.open
    original_fsync = bootstrap.os.fsync
    original_replace = bootstrap.os.replace

    def tracked_open(self, mode='r', *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if mode == 'r+b':
            assert self != path
            assert self.name.startswith('sessions.db.pre-project-profiles.bak.')
            opened[handle.fileno()] = handle
        return handle

    def tracked_fsync(fd):
        assert fd in opened and opened[fd].writable()
        events.append('sync')
        return original_fsync(fd)

    def tracked_replace(source, target):
        assert events == ['sync']
        events.append('replace')
        return original_replace(source, target)

    monkeypatch.setattr(Path, 'open', tracked_open)
    monkeypatch.setattr(bootstrap.os, 'fsync', tracked_fsync)
    monkeypatch.setattr(bootstrap.os, 'replace', tracked_replace)
    bootstrap.backup_before_start(path)
    assert events == ['sync', 'replace']


def test_backup_failure_prevents_opening_and_retirement(tmp_path, monkeypatch):
    import server.session_bootstrap as bootstrap
    path = tmp_path / 'sessions.db'
    seed = SessionStore(path)
    item = seed.create(['sklr'])
    seed.close()
    def fail(*args):
        raise OSError('PRIVATE-BACKUP')
    monkeypatch.setattr(bootstrap.os, 'replace', fail)
    with pytest.raises(SessionBootstrapError) as info:
        build_work_session(path, ROOTS, enabled=True)
    assert 'PRIVATE' not in str(info.value)
    reopened = SessionStore(path)
    try:
        assert reopened.get(item.id) == item
    finally:
        reopened.close()
    assert not list(tmp_path.glob('*.bak.*'))


def test_task_database_is_rejected_without_leaking_path(tmp_path):
    path = tmp_path / 'tasks.db'
    store = TaskStore(str(path))
    if hasattr(store, 'close'):
        store.close()
    with pytest.raises(SessionBootstrapError) as info:
        build_work_session(path, ROOTS, enabled=True)
    assert str(tmp_path) not in str(info.value) and 'tasks' not in str(info.value)


def test_storage_failure_is_fail_closed_with_fixed_message(tmp_path):
    with pytest.raises(SessionBootstrapError) as info:
        build_work_session(tmp_path, ROOTS, enabled=True)   # a directory is not a database
    assert str(info.value) == 'Work-session bootstrap failed.'
    assert str(tmp_path) not in str(info.value)


def _registrars(controller):
    regular, confidential = [], []
    def register_tool(function):
        regular.append(function)
        return function
    def register_confidential_tool(function):
        confidential.append(function)
        return function
    reg, conf = make_session_registrars(register_tool, register_confidential_tool, controller)
    return reg, conf, regular, confidential


def test_registrars_keep_signature_split_and_block_work_tools(runtime):
    reg, conf, regular, confidential = _registrars(runtime.controller)

    def read_file(filepath: str, max_chars: int = 10) -> dict:
        return {'success': True, 'content': filepath}

    def ping() -> dict:
        return {'success': True}

    def secret_reveal(key: str) -> dict:
        return {'success': True, 'value': key}

    original = inspect.signature(read_file)
    wrapped_read, wrapped_ping, wrapped_secret = reg(read_file), reg(ping), conf(secret_reveal)
    assert regular == [wrapped_read, wrapped_ping] and confidential == [wrapped_secret]
    assert inspect.signature(wrapped_read) == original and wrapped_read.__name__ == 'read_file'
    blocked = wrapped_read('a')
    assert blocked['success'] is False and blocked['session_control']['code'] == 'session_not_selected'
    assert wrapped_secret('k')['success'] is False         # poufne narzedzie tez zablokowane
    assert wrapped_ping()['success'] is True               # sterujace dostepne

    runtime.admin.start(['sklr'])
    result = wrapped_read('a')
    assert result['content'] == 'a' and result['session_control']['code'] == 'session_active'
    assert runtime.controller.snapshot()['running_actions'] == 0


def test_get_session_state_is_read_only_and_reflects_pause(runtime):
    tool = make_get_session_state(runtime.controller)
    assert tool.__name__ == 'get_session_state' and not inspect.signature(tool).parameters
    assert tool()['session_control']['code'] == 'session_not_selected'
    started = runtime.admin.start(['sklr'])
    assert tool()['session_control']['code'] == 'session_active'
    runtime.admin.pause(started['session_id'], started['revision'])
    control = tool()['session_control']
    assert tool()['success'] is True
    assert control['code'] == 'session_paused' and control['reason'] == 'user_requested_pause'
    assert control['instruction'] and 'checkpoint' not in control['instruction']
    assert 'checkpoint_content_available' not in control


def test_verify_classification_detects_missing_and_unknown():
    names = set(CONTROL_TOOLS) | set(WORK_TOOLS)
    assert verify_classification(names) is True
    with pytest.raises(ValueError):
        verify_classification(names - {'read_file'})
    with pytest.raises(ValueError):
        verify_classification(names | {'new_tool'})


def _main_tool_names():
    tree = ast.parse(MAIN.read_text(encoding='utf-8'))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Name) and deco.id in (
                        'register_tool', 'register_confidential_tool'):
                    names.add(node.name)
    return names


def test_all_main_tools_are_classified():
    names = _main_tool_names()
    assert 'read_file' in names and 'get_session_state' not in names
    assert verify_classification(names | {
        'get_session_state', 'get_resume_context', 'confirm_resume_plan'}) is True


def test_main_integrates_sessions_only_through_explicit_opt_in():
    # Integration explicitly agreed in P3c; no production main import here.
    from state import DEFAULT_CONFIG
    source = MAIN.read_text(encoding='utf-8')
    assert DEFAULT_CONFIG['work_sessions'] == {'enabled': False}
    assert 'configured_work_session(CONFIG, WORK_SESSIONS_DB, ROOTS)' in source
    assert 'session_controller=WORK_SESSION.controller if WORK_SESSION is not None else None' in source
    assert 'work_sessions=WORK_SESSION.admin if WORK_SESSION is not None else None' in source
    assert 'verify_session_registration(WORK_SESSION, SESSION_TOOL_NAMES)' in source
