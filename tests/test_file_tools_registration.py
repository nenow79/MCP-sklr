"""Wrappery MCP testujemy bez importu main.py i jego skutków ubocznych."""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest


NAMES = {"list_directory", "read_file", "read_files_batch", "search_code"}


@pytest.fixture
def wrappers():
    path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in NAMES]
    assert {node.name for node in functions} == NAMES and len(functions) == 4
    imported = next(node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "server")
    assert {"file_tools", "search_tools"} <= {alias.name for alias in imported.names}
    for function in functions:
        assert len(function.decorator_list) == 1
        assert isinstance(function.decorator_list[0], ast.Name)
        assert function.decorator_list[0].id == "register_tool"
        assert ast.get_docstring(function)
    calls, registered = [], []
    root = object()
    response = {"success": True, "data": {"sentinel": True}}

    def stub(name):
        def invoke(*args):
            calls.append((name, args))
            return response
        return invoke

    def register(function):
        registered.append(function.__name__)
        return function

    namespace = {
        "Any": Any, "Dict": Dict, "List": List, "Optional": Optional,
        "ROOTS": root, "register_tool": register,
        "file_tools": SimpleNamespace(**{name + "_impl": stub(name) for name in NAMES - {"search_code"}}),
        "search_tools": SimpleNamespace(search_code_impl=stub("search_code")),
    }
    isolated = ast.Module(body=functions, type_ignores=[])
    exec(compile(isolated, str(path), "exec"), namespace)
    assert set(registered) == NAMES
    return namespace, calls, root, response


def test_defaults_are_forwarded(wrappers):
    functions, calls, root, response = wrappers
    assert functions["list_directory"]() is response
    assert calls.pop() == ("list_directory", (root, "", None))
    assert functions["read_file"]("a.txt") is response
    assert calls.pop() == ("read_file", (root, "a.txt", None, None, 12000, None, False))
    requests = [{"filepath": "a.txt"}]
    assert functions["read_files_batch"](requests) is response
    assert calls.pop() == ("read_files_batch", (root, requests, 24000))
    assert functions["search_code"]("query") is response
    assert calls.pop() == ("search_code", (root, "query", "", False, 0, 30, 24000, None))


def test_explicit_parameters_are_forwarded(wrappers):
    functions, calls, root, response = wrappers
    assert functions["list_directory"]("src", False) is response
    assert calls.pop() == ("list_directory", (root, "src", False))
    assert functions["read_file"]("a.txt", 2, 4, 1000, "docs", True) is response
    assert calls.pop() == ("read_file", (root, "a.txt", 2, 4, 1000, "docs", True))
    requests = [{"filepath": "a.txt", "root": "docs"}]
    assert functions["read_files_batch"](requests, 1000) is response
    assert calls.pop() == ("read_files_batch", (root, requests, 1000))
    assert functions["search_code"]("query", "a.txt", True, 2, 10, 1000, "*") is response
    assert calls.pop() == ("search_code", (root, "query", "a.txt", True, 2, 10, 1000, "*"))
