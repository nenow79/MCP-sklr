"""Panel-only external argument preview: authenticated TestClient, no network/MCP."""
import json
import re

from starlette.testclient import TestClient

from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from server.task_tools import get_task_impl
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore
from tasks.volatile_results import VolatileResult


ORIGIN = 'http://127.0.0.1:8102'


def test_preview_only_authenticated_pending_details(tmp_path):
    store = TaskStore(str(tmp_path / 'tasks.db'))
    approval = ApprovalService()
    manager = TaskManager(store, approval)
    auth = PanelAuth(port=8102)
    app = create_panel_app(manager, approval, auth)
    preview = {'text': '{"q":"PRIVATE_PREVIEW"}', 'warning': 'heuristic', 'truncated': False}
    task = manager.submit(dict(kind='external_mcp', title='browser / lookup',
        sensitive=True, offer_trust=False), lambda event: VolatileResult({'content': 'PRIVATE_RESULT'}),
        True, approval_preview=preview)
    session = auth.exchange_token(auth.token)
    headers = {'Cookie': f'{COOKIE_NAME}={session}'}
    try:
        with TestClient(app, base_url=ORIGIN) as client:
            assert client.get(f'/api/tasks/{task.id}').status_code == 401
            details = client.get(f'/api/tasks/{task.id}', headers=headers).json()['data']
            assert details['approval_preview'] == preview
            listing = client.get('/api/tasks', headers=headers).json()
            assert 'PRIVATE_PREVIEW' not in json.dumps(listing)
            assert 'PRIVATE_PREVIEW' not in json.dumps(get_task_impl(manager, task.id))
            decision = client.post(f'/api/tasks/{task.id}/decision', json={'decision': 'allow'},
                                  headers={**headers, 'Origin': ORIGIN})
            assert decision.status_code == 200
            assert 'approval_preview' not in decision.json()['data']
            manager.wait(task.id, 5)
            details = client.get(f'/api/tasks/{task.id}', headers=headers).json()['data']
            assert 'approval_preview' not in details
            assert 'external_result' not in details
            assert 'PRIVATE_RESULT' not in json.dumps(details)
    finally:
        manager.shutdown()
        store.close()


def test_external_view_uses_text_and_not_result_channel():
    source = ( __import__('pathlib').Path(__file__).resolve().parents[1]
              / 'panel/static/app.js').read_text(encoding='utf-8')
    view = source.split('if (task.kind === "external_mcp") {', 1)[1].split('var isSkill', 1)[0]
    assert 'task.status === "awaiting_approval"' in view
    assert 'visibleText(task.approval_preview.text, true)' in view
    assert 'innerHTML' not in view
    # Ignore JS string literals: the explanatory text names get_task.external_result.
    # This is a narrow static check, complemented by the panel API test above.
    code = re.sub(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', '', view, flags=re.X)
    assert re.search(r'(?<![\w$])task\s*\.\s*external_result\b', code) is None
