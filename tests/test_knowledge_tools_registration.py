"""Kontrakt rejestracji MCP bez importu main i uruchamiania produkcyjnego serwera."""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest


@pytest.mark.parametrize("name,args,kwargs,expected", [
    ("knowledge_bundles", (), {}, ()),
    ("knowledge_search", (), {}, ("", None, "all", None, None, None, "terms", False, False, 10, 12000, "auto")),
    ("knowledge_search", ("abc",), {"bundle_id": "general", "refresh": "cached"},
     ("abc", "general", "all", None, None, None, "terms", False, False, 10, 12000, "cached")),
    ("knowledge_get", ("general", "note"), {}, ("general", "note", "outline", None, "subtree", None, 12000)),
    ("knowledge_get", ("general", "note", "section"),
     {"section_id": "s1", "expected_revision": "r1.example", "section_mode": "direct"},
     ("general", "note", "section", "s1", "direct", "r1.example", 12000)),
])
def test_registration_and_forwarding(name, args, kwargs, expected):
    path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    assert [ast.unparse(node) for node in function.decorator_list] == ["register_confidential_tool"]
    calls, dependency = [], object()
    response = {"success": True}

    def impl(*values):
        calls.append(values)
        return response

    namespace = {"Any": Any, "Dict": Dict, "List": List, "Optional": Optional,
                 "register_confidential_tool": lambda fn: fn, "KNOWLEDGE": dependency,
                 "knowledge_tools": SimpleNamespace(**{name + "_impl": impl})}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    assert namespace[name](*args, **kwargs) is response
    assert calls == [(dependency,) + expected]


def test_service_uses_process_dependencies_once():
    path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "KNOWLEDGE" for target in node.targets)]
    assert len(assignments) == 1
    call = assignments[0].value
    assert ast.unparse(call.func) == "knowledge_tools.create_service"
    assert [ast.unparse(arg) for arg in call.args] == ["CONFIG_STORE", "ROOTS", "TASK_MANAGER"]
