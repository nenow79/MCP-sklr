"""Load exact model function definitions without main's application bootstrap.

Real FastMCP and production WorkSession wrappers, no replacement admission.
Telemetry writer/bootstrap and unrelated tools are intentionally not imported.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

from fastmcp import FastMCP

from server import task_tools
from server.session_activation import session_registrars

MAIN = Path(__file__).resolve().parents[1] / 'main.py'
NAMES = {'list_external_mcp', 'list_external_mcp_tools', 'call_external_mcp_tool', 'get_task'}


def model_functions(registrar, tools, manager):
    source = ast.parse(MAIN.read_text(encoding='utf-8-sig'))
    selected = [node for node in source.body if isinstance(node, ast.FunctionDef)
                and node.name in NAMES]
    assert {node.name for node in selected} == NAMES
    namespace = {'Any': Any, 'Dict': Dict, 'Optional': Optional,
                 'register_confidential_tool': registrar,
                 'EXTERNAL_MCP_TOOLS': tools, 'TASK_MANAGER': manager, 'task_tools': task_tools}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(MAIN), 'exec'), namespace)
    return namespace


def model_server(tools, manager, controller):
    mcp = FastMCP('isolated-sklr-model-api')
    runtime = SimpleNamespace(controller=controller) if controller is not None else None
    _, register, _ = session_registrars(mcp.tool, mcp.tool,
                                      runtime)
    model_functions(register, tools, manager)
    return mcp
