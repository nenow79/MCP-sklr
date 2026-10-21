"""M5b registration tested without importing production main.py."""
import ast
import queue
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict


MAIN = Path(__file__).resolve().parents[1] / 'main.py'


def test_three_confidential_wrappers_forward_to_shared_adapter():
    tree = ast.parse(MAIN.read_text(encoding='utf-8-sig'))
    names = {'list_external_mcp', 'list_external_mcp_tools', 'call_external_mcp_tool'}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(functions) == 3
    calls = []
    def record(name):
        def impl(*args):
            calls.append((name, args))
            return {'success': True}
        return impl
    adapter = SimpleNamespace(list_profiles=record('profiles'), list_tools=record('tools'),
                              call_tool=record('call'))
    for function in functions:
        assert [ast.unparse(item) for item in function.decorator_list] == ['register_confidential_tool']
    namespace = {'Any': Any, 'Dict': Dict, 'EXTERNAL_MCP_TOOLS': adapter,
                 'register_confidential_tool': lambda function: function}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(MAIN), 'exec'), namespace)
    namespace['list_external_mcp']()
    namespace['list_external_mcp_tools']('browser')
    namespace['call_external_mcp_tool']('browser', 'list_open_tabs', {})
    assert calls == [('profiles', ()), ('tools', ('browser', 60)),
                     ('call', ('browser', 'list_open_tabs', {}, 60))]
    assert 'get_task' in ast.get_docstring(next(item for item in functions if item.name == 'call_external_mcp_tool'))


def test_panel_and_model_share_registry_and_manager():
    tree = ast.parse(MAIN.read_text(encoding='utf-8-sig'))
    adapters = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == 'ExternalMCPTools']
    admins = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name) and node.func.id == 'ExternalMCPAdmin']
    assert len(adapters) == len(admins) == 1
    for call in adapters + admins:
        assert [ast.unparse(arg) for arg in call.args[:2]] == ['EXTERNAL_MCP_REGISTRY', 'TASK_MANAGER']


def test_external_get_task_telemetry_never_serializes_payload(monkeypatch):
    import telemetry
    from server.task_tools import get_task_impl
    from tasks.models import Task, TaskStatus
    def forbidden(*args):
        raise AssertionError('confidential payload must not be inspected')
    monkeypatch.setattr(telemetry, '_json_size_bytes', forbidden)
    monkeypatch.setattr(telemetry, '_summarize_error', forbidden)
    records = []
    class CaptureQueue(queue.Queue):
        # Full reader API for the live telemetry worker; no test records in logs.
        def put_nowait(self, record):
            records.append(record)
    monkeypatch.setattr(telemetry, '_RECORD_QUEUE', CaptureQueue())
    task = Task(id=1, created_at='now', kind='external_mcp', sensitive=True, status=TaskStatus.DONE)
    manager = SimpleNamespace(get=lambda task_id: task,
        get_external_result=lambda task_id: {'state': 'available', 'data': {'content': 'PRIVATE'}})
    def get_task(task_id):
        return get_task_impl(manager, task_id)
    wrapped = telemetry.track_mcp_tool(get_task, lambda: 'sklr', confidential=True)
    assert wrapped(1)['data']['external_result']['data']['content'] == 'PRIVATE'
    assert records[0]['input_bytes'] == records[0]['output_bytes'] == ''
    assert 'PRIVATE' not in repr(records)
