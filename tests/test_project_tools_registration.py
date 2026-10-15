"""Publiczne narzędzia projektowe: bezpieczne defaults i wspólny właściciel sesji."""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest


@pytest.mark.parametrize("name,args,expected", [
    ("prepare_project", (), (None, "", 12000, False)),
    ("knowledge_audit", (), (None, True, 500, 12000)),
    ("knowledge_convert", ([{"filepath": "docs/a.md"}],), ([{"filepath": "docs/a.md"}], None, "")),
])
def test_project_tools_forward_to_process_owner_as_confidential(name, args, expected):
    path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    assert [ast.unparse(node) for node in function.decorator_list] == ["register_confidential_tool"]
    calls = []
    response = {"success": True}

    def operation(*values):
        calls.append(values)
        return response

    namespace = {"Any": Any, "Dict": Dict, "List": List, "Optional": Optional,
                 "register_confidential_tool": lambda fn: fn,
                 "call_project": lambda fn, *values: fn(*values),
                 "PROJECTS": SimpleNamespace(prepare=operation, audit=operation, convert=operation)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    assert namespace[name](*args) is response
    assert calls == [expected]
