"""Rejestracja poufnych narzędzi bez importowania produkcyjnego main.py."""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

CONFIDENTIAL = {"get_task", "secret_reveal"}


def main_tree():
    path = Path(__file__).resolve().parents[1] / "main.py"
    return path, ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def functions_by_name(tree):
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def decorator_name(function):
    assert len(function.decorator_list) == 1
    decorator = function.decorator_list[0]
    assert isinstance(decorator, ast.Name)
    return decorator.id


def test_only_get_task_and_secret_reveal_use_confidential_decorator():
    _, tree = main_tree()
    functions = functions_by_name(tree)
    assert {"register_tool", "register_confidential_tool", "get_task", "secret_reveal"} <= set(functions)
    decorators = {
        name: decorator_name(function)
        for name, function in functions.items()
        if function.decorator_list and name not in {"register_tool", "register_confidential_tool"}
    }
    confidential = {name for name, value in decorators.items() if value == "register_confidential_tool"}
    assert confidential == CONFIDENTIAL
    assert all(value == "register_tool" for name, value in decorators.items() if name not in CONFIDENTIAL)
    # 18 dotychczasowych narzędzi + nowe secret_reveal.
    assert len(decorators) == 19


def test_confidential_registration_enables_confidential_telemetry():
    path, tree = main_tree()
    ordinary = functions_by_name(tree)["register_tool"]
    confidential = functions_by_name(tree)["register_confidential_tool"]
    namespace = {"_default_root_alias": lambda: "workspace", "tracked": [], "registered": []}

    def track(function, getter, **kwargs):
        namespace["tracked"].append((function, getter, kwargs))
        return ("tracked", function)

    class FakeMcp:
        @staticmethod
        def tool(function):
            namespace["registered"].append(function)
            return function

    namespace.update(track_mcp_tool=track, mcp=FakeMcp())
    exec(compile(ast.Module(body=[ordinary, confidential], type_ignores=[]), str(path), "exec"), namespace)

    def sample():
        return None

    assert namespace["register_tool"](sample) == ("tracked", sample)
    assert namespace["register_confidential_tool"](sample) == ("tracked", sample)
    assert namespace["tracked"][0][2] == {}
    assert namespace["tracked"][1][2] == {"confidential": True}
    assert namespace["registered"] == [("tracked", sample), ("tracked", sample)]


@pytest.fixture
def wrapper():
    path, tree = main_tree()
    function = functions_by_name(tree)["secret_reveal"]
    imported = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "server")
    assert "secret_tools" in {alias.name for alias in imported.names}
    assert "wait" not in {arg.arg for arg in function.args.args}
    doc = ast.get_docstring(function)
    required = ("panelu", "task_id", "wait_task", "get_task", "secret_result", "TTL",
                "list_tasks", ": ping", "ALBO", "nigdy cały plik", "RAM" if "RAM" in doc else "pamięci")
    assert doc and all(text in doc for text in required)
    calls = []
    roots, manager, lock = object(), object(), object()
    response = {"success": True, "data": {"sentinel": True}}

    def impl(*args):
        calls.append(args)
        return response

    namespace = {
        "Any": Any, "Dict": Dict, "List": List, "Optional": Optional,
        "ROOTS": roots, "TASK_MANAGER": manager, "CONFIG_STORE": SimpleNamespace(lock=lock),
        "register_confidential_tool": lambda function: function,
        "secret_tools": SimpleNamespace(secret_reveal_impl=impl),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["secret_reveal"], calls, roots, manager, lock, response


def test_secret_reveal_defaults_forwarded(wrapper):
    function, calls, roots, manager, lock, response = wrapper
    assert function(".env", key="TOKEN") is response
    assert calls.pop() == (roots, manager, lock, ".env", "TOKEN", None, None, 12000, None, "")


def test_secret_reveal_explicit_range_forwarded(wrapper):
    function, calls, roots, manager, lock, response = wrapper
    assert function("id.pem", None, 2, 4, 80000, "docs", "opis") is response
    assert calls.pop() == (roots, manager, lock, "id.pem", None, 2, 4, 80000, "docs", "opis")


def test_get_task_description_documents_separate_result_without_values():
    _, tree = main_tree()
    function = functions_by_name(tree)["get_task"]
    doc = ast.get_docstring(function)
    for text in ("secret_result", "available/unavailable/denied", "TTL", "nie konsumuje",
                 "nowe ujawnienie wymaga nowej zgody"):
        assert text in doc
    assert "SENTINEL" not in doc


def test_secret_reveal_shares_task_manager_and_config_lock_dependencies():
    _, tree = main_tree()
    function = functions_by_name(tree)["secret_reveal"]
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "secret_reveal_impl"]
    assert len(calls) == 1
    names = [ast.unparse(arg) for arg in calls[0].args[:3]]
    assert names == ["ROOTS", "TASK_MANAGER", "CONFIG_STORE.lock"]
