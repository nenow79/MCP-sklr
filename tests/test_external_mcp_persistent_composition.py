"""L3 composition/API contract; no main bootstrap, processes or sockets."""
import ast
import importlib
import inspect
from types import SimpleNamespace

import anyio
import pytest
from fastmcp import Client

from external_mcp_model_api_helpers import MAIN, model_functions, model_server
from external_mcp_persistent_manual_helpers import ManualRig


def test_main_model_function_forwards_expected_generation():
    calls = []
    tools = SimpleNamespace(call_tool=lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    functions = model_functions(lambda fn: fn, tools, None)
    call = functions['call_external_mcp_tool']
    assert 'expected_runtime_generation' in inspect.signature(call).parameters
    call('fake', 'lookup', {}, expected_runtime_generation='pinned-generation')
    assert calls == [(('fake', 'lookup', {}, 60),
                      {'expected_runtime_generation': 'pinned-generation'})]


def test_real_model_schema_has_optional_nullable_generation(tmp_path):
    rig = ManualRig(tmp_path / 'schema')
    async def scenario():
        async with Client(model_server(rig.tools, rig.manager, rig.controller)) as client:
            tools = {t.name: t for t in await client.list_tools()}
            schema = tools['call_external_mcp_tool'].input_schema
            assert 'expected_runtime_generation' in schema['properties']
            assert 'expected_runtime_generation' not in schema.get('required', [])
            field = schema['properties']['expected_runtime_generation']
            assert {p['type'] for p in field['anyOf']} == {'string', 'null'}
    try:
        anyio.run(scenario)
    finally:
        rig.close()


def test_main_uses_lazy_composition_factory_and_all_shutdown_paths():
    tree = ast.parse(MAIN.read_text(encoding='utf-8-sig'))
    construction = next(node.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'EXTERNAL_MCP_TOOLS' for t in node.targets))
    assert isinstance(construction.func, ast.Name)
    assert construction.func.id == 'create_external_mcp_tools'
    stops = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'stop_work_session']
    assert len(stops) == 3
    for stop in stops:
        keywords = {kw.arg: kw.value for kw in stop.keywords}
        assert isinstance(keywords.get('external_mcp'), ast.Name)
        assert keywords['external_mcp'].id == 'EXTERNAL_MCP_TOOLS'
        assert isinstance(keywords['budget'], ast.Name)
        assert keywords['budget'].id == 'PERSISTENT_SHUTDOWN_BUDGET_S'


def test_production_composition_is_lazy_and_closes_without_runtime(tmp_path):
    try:
        module = importlib.import_module('server.external_mcp_runtime')
    except ModuleNotFoundError as exc:
        if exc.name != 'server.external_mcp_runtime':
            raise
        pytest.fail('L3 RED: production composition factory missing')
    rig = ManualRig(tmp_path / 'composition')
    tools = module.create_external_mcp_tools(rig.registry, rig.manager, panel_available=lambda: True)
    try:
        assert tools.persistent_supervisor.thread is None
        assert tools._lifecycle.thread is None
        assert tools.list_profiles()['success']
        assert tools.persistent_supervisor.thread is None
        assert module.PERSISTENT_SHUTDOWN_BUDGET_S >= 305
        assert tools.close_persistent(timeout=0) is True
    finally:
        tools.close_persistent()
        rig.close()


def test_disabled_work_session_refuses_persistent_without_starting_thread(tmp_path):
    from server.external_mcp_runtime import create_external_mcp_tools
    rig = ManualRig(tmp_path / 'disabled')
    rig.manager._sessions = None
    tools = create_external_mcp_tools(rig.registry, rig.manager, panel_available=lambda: True)
    try:
        response = tools.list_tools('fake')
        assert response['success'] is False
        assert response['code'] == 'persistent_runtime_required'
        assert tools.persistent_supervisor.thread is None
        assert rig.manager._workers == []
    finally:
        tools.close_persistent()
        rig.close()
