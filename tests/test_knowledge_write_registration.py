"""knowledge_save: rejestracja confidential i zależności bez importowania main."""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest


@pytest.mark.parametrize("args,kwargs,expected", [
    (("general", "note", "create", "body"), {},
     ("general", "note", "create", "body", None, None, "")),
    (("general", "note", "update", "body"),
     {"metadata_yaml": "type: Reference", "expected_revision": "r1.example", "description": "opis"},
     ("general", "note", "update", "body", "type: Reference", "r1.example", "opis")),
])
def test_knowledge_save_registration_and_forwarding(args, kwargs, expected):
    path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "knowledge_save")
    assert [ast.unparse(node) for node in function.decorator_list] == ["register_confidential_tool"]
    assert "wait" not in {arg.arg for arg in function.args.args}
    doc = ast.get_docstring(function)
    for text in ("task_id", "get_task", "list_tasks", "committed", "bez Ufaj", "expected_revision"):
        assert text in doc
    dependencies = [object() for _ in range(5)]
    calls = []
    response = {"success": True}

    def impl(*values):
        calls.append(values)
        return response

    namespace = {"Any": Any, "Dict": Dict, "Optional": Optional,
                 "register_confidential_tool": lambda fn: fn,
                 "KNOWLEDGE": dependencies[0], "ROOTS": dependencies[1],
                 "TASK_MANAGER": dependencies[2], "FILE_TRANSACTION": dependencies[3],
                 "CONFIG_STORE": SimpleNamespace(lock=dependencies[4]),
                 "PANEL_SERVER": SimpleNamespace(running=True),
                 "knowledge_write_tools": SimpleNamespace(knowledge_save_impl=impl)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    assert namespace["knowledge_save"](*args, **kwargs) is response
    assert calls[0][:5] == tuple(dependencies)
    assert calls[0][5]() is True
    namespace["PANEL_SERVER"] = None
    assert calls[0][5]() is False
    assert calls[0][6:] == expected
