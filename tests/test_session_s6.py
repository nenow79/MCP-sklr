"""Checkpoint removal: existing databases, closed admission and current catalog."""
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from server.session_activation import session_registrars
from server.session_bootstrap import build_work_session
from server.session_tools import session_control, wrap_session_tool
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.session_controller import SessionBlocked, SessionController
from tasks.session_models import SessionConflict
from tasks.session_store import SessionStore, _SCHEMA
from tasks.store import TaskStore


@pytest.mark.parametrize('version', [1, 2])
@pytest.mark.parametrize('legacy_state', ['pending', 'saved', 'unavailable'])
def test_legacy_database_preserves_identity_links_and_requires_new_handshake(tmp_path, version, legacy_state):
    path = tmp_path / 'sessions.db'
    sid = 'a' * 32
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        if version == 2:
            conn.execute("ALTER TABLE work_sessions ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'open'")
        conn.execute(f'PRAGMA user_version={version}')
        conn.execute('INSERT INTO work_sessions '
                     '(id,roots,state,checkpoint_state,revision,reason,created_at,updated_at) '
                     'VALUES (?,?,?,?,?,?,?,?)',
                     (sid, '["app"]', 'paused', legacy_state, 7, 'user_requested_pause', 'old', 'old'))
        conn.execute('INSERT INTO session_task_links VALUES (?,?,?)', (42, sid, 1))
    roots = SimpleNamespace(list_status=lambda: [{'alias': 'app', 'valid': True}])
    # Before bootstrap, old values do not leak to the public state or authorize work.
    store = SessionStore(path)
    try:
        controller = SessionController(store)
        controller.bind(sid, 7)
        assert controller.snapshot()['execution_blocked']
        assert not any(k.startswith('checkpoint') for k in controller.snapshot())
        assert store.get(sid).checkpoint_state.value == legacy_state
    finally:
        store.close()
    runtime = build_work_session(path, roots, enabled=True)
    try:
        assert runtime.controller.snapshot()['session_id'] is None
        item = runtime.store.get(sid)
        assert item.revision == 8 and item.lifecycle == 'open'
        assert item.checkpoint_state.value == 'unavailable'
        assert runtime.store.get_task_link(42).session_revision == 1
        state = runtime.admin.recover(sid, 8, True)
        assert state['execution_blocked'] and state['session_id'] == sid
        assert not any(k.startswith('checkpoint') for k in state)
        with pytest.raises(SessionBlocked):
            runtime.controller.begin_action()
        runtime.controller.prepare_resume_internal('', state['revision'])
        context = runtime.controller.get_resume_context()
        assert not {'checkpoint', 'checkpoint_missing'} & context.keys()
        runtime.controller.confirm_resume_plan('Read project state before changing files',
                                               context['revision'], context['preparation_token'])
        token = runtime.controller.begin_action()
        runtime.controller.finish_action(token)
        assert runtime.store.get_task_link(42).session_id == sid
    finally:
        runtime.close()


def test_pause_and_pending_resume_block_work_without_creating_checkpoint(tmp_path):
    roots = SimpleNamespace(list_status=lambda: [{'alias': 'app', 'valid': True}])
    runtime = build_work_session(tmp_path / 'sessions.db', roots, enabled=True)
    calls = []
    def read_file():
        calls.append(True)
        return {'success': True}
    tool = wrap_session_tool(read_file, runtime.controller)
    try:
        active = runtime.admin.start(['app'])
        state = runtime.admin.pause(active['session_id'], active['revision'])
        result = tool()
        assert not result['success'] and calls == []
        control = result['session_control']
        assert control['code'] == 'session_paused'
        assert 'Zakończ odpowiedź' in control['instruction']
        assert 'checkpoint' not in control['instruction']
        assert runtime.store.get(active['session_id']).revision == state['revision']
        runtime.controller.prepare_resume_internal('Review changed requirements', state['revision'])
        context = runtime.controller.get_resume_context()
        pending = tool()
        assert not pending['success'] and calls == []
        instruction = session_control(runtime.controller)['instruction']
        assert 'zapytaj użytkownika' in instruction and 'nie zgaduj' in instruction
        with pytest.raises(SessionConflict):
            runtime.controller.confirm_resume_plan('Plan', context['revision'], 'stale-token')
        assert runtime.controller.snapshot()['execution_blocked']
        runtime.controller.confirm_resume_plan('Read current requirements first',
                                               context['revision'], context['preparation_token'])
        assert tool()['success'] and calls == [True]
    finally:
        runtime.close()


def test_removed_tool_and_panel_endpoint_are_unavailable(tmp_path):
    roots = SimpleNamespace(list_status=lambda: [{'alias': 'app', 'valid': True}])
    runtime = build_work_session(tmp_path / 'sessions.db', roots, enabled=True)
    store = TaskStore(str(tmp_path / 'tasks.db'))
    approval = ApprovalService()
    manager = TaskManager(store, approval, session_controller=runtime.controller)
    try:
        registered = {}
        def register(fn):
            registered[fn.__name__] = fn
            return fn
        session_registrars(register, register, runtime)
        assert set(registered) == {'get_session_state', 'get_resume_context', 'confirm_resume_plan'}
        auth = PanelAuth(port=8102)
        cookie = auth.exchange_token(auth.token)
        headers = {'Origin': 'http://127.0.0.1:8102', 'Cookie': f'{COOKIE_NAME}={cookie}'}
        app = create_panel_app(manager, approval, auth, work_sessions=runtime.admin)
        with TestClient(app, base_url='http://127.0.0.1:8102') as client:
            assert client.get('/api/work-session/checkpoint', headers=headers).status_code == 404
            assert client.post('/api/work-session/checkpoint', headers=headers, json={}).status_code == 404
        assert store.list() == []
    finally:
        manager.shutdown()
        store.close()
        runtime.close()
