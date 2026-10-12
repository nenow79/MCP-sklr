"""Rejestracja zapisów D1 bez importowania produkcyjnego main.py."""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

NAMES = {"edit_file", "create_file", "manage_folder"}


def main_tree():
    path = Path(__file__).resolve().parents[1] / "main.py"
    return path, ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


@pytest.fixture
def wrappers():
    path, tree = main_tree()
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
    assert len(functions) == 3 and {n.name for n in functions} == NAMES
    imported = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "server")
    assert {"edit_tools", "folder_tools"} <= {a.name for a in imported.names}
    for function in functions:
        assert len(function.decorator_list) == 1
        assert isinstance(function.decorator_list[0], ast.Name)
        assert function.decorator_list[0].id == "register_tool"
        assert "wait" not in {a.arg for a in function.args.args}
        doc = ast.get_docstring(function)
        assert doc and all(word in doc for word in ("panelu", "task_id", "list_tasks", ": ping", "poczekaj"))
    calls, registered = [], []
    roots, manager, transaction, lock = object(), object(), object(), object()
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
        "ROOTS": roots, "TASK_MANAGER": manager, "FILE_TRANSACTION": transaction,
        "CONFIG_STORE": SimpleNamespace(lock=lock), "register_tool": register,
        "edit_tools": SimpleNamespace(edit_file_impl=stub("edit_file"), create_file_impl=stub("create_file")),
        "folder_tools": SimpleNamespace(manage_folder_impl=stub("manage_folder")),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    assert set(registered) == NAMES
    return namespace, calls, roots, manager, transaction, lock, response


def test_defaults_forwarded(wrappers):
    functions, calls, roots, manager, transaction, lock, response = wrappers
    changes = [{"filepath": "a.txt", "old_text": "old", "new_text": "new"}]
    assert functions["edit_file"](changes) is response
    assert calls.pop() == ("edit_file", (roots, manager, transaction, changes, None, ""))
    assert functions["create_file"]("a.txt", "new") is response
    assert calls.pop() == ("create_file", (roots, manager, transaction, "a.txt", "new", False, None, ""))
    assert functions["manage_folder"]("create", "sub") is response
    assert calls.pop() == ("manage_folder", (roots, manager, lock, "create", "sub", None, None, ""))


def test_explicit_arguments_forwarded(wrappers):
    functions, calls, roots, manager, transaction, lock, response = wrappers
    changes = [{"filepath": "a.txt", "old_text": "old", "new_text": "new", "root": "other"}]
    assert functions["edit_file"](changes, "app", "powód") is response
    assert calls.pop() == ("edit_file", (roots, manager, transaction, changes, "app", "powód"))
    assert functions["create_file"]("a.txt", "new", True, False, "opis") is response
    assert calls.pop() == ("create_file", (roots, manager, transaction, "a.txt", "new", True, False, "opis"))
    assert functions["manage_folder"]("rename", "sub", "dst", "other", "opis") is response
    assert calls.pop() == ("manage_folder", (roots, manager, lock, "rename", "sub", "dst", "other", "opis"))


def test_single_shared_transaction_binding():
    path, tree = main_tree()
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "server.file_transaction"]
    assert len(imports) == 1 and any(a.name == "FileTransaction" for a in imports[0].names)
    constructors = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id == "FileTransaction"]
    assert len(constructors) == 1
    assignments = [n for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "FILE_TRANSACTION" for t in n.targets)]
    assert len(assignments) == 1 and assignments[0].value is constructors[0]
    roots, lock = object(), object()
    calls = []

    def constructor(*args):
        calls.append(args)
        return object()

    namespace = {"FileTransaction": constructor, "ROOTS": roots, "CONFIG_STORE": SimpleNamespace(lock=lock)}
    exec(compile(ast.Module(body=assignments, type_ignores=[]), str(path), "exec"), namespace)
    assert calls == [(roots, lock)]
    assert namespace["FILE_TRANSACTION"] is not None
