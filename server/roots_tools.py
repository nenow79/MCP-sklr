#!/usr/bin/env python3
"""Narzędzia MCP dla folderów (roots): list_roots i request_root (bez zależności od FastMCP)."""
from typing import Any, Dict, Optional

from roots import MODES, RootError
from roots_registry import RootsRegistry
from tasks.manager import QueueFullError, TaskManager, TaskStateError
from tasks.models import TaskStatus

KIND_ROOT_CHANGE = "root_change"

ROOT_HINT = ("Narzędzia plikowe przyjmują parametr root (alias z tej listy); brak parametru, pusty "
             "napis lub \"false\" oznacza folder domyślny.")
from server.task_tools import MANUAL_APPROVAL_HINT

WAIT_HINT = MANUAL_APPROVAL_HINT + " Po sukcesie sprawdź list_roots."


def _error(message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"success": False, "error": message}
    if data is not None:
        result["data"] = data
    return result


def list_roots_impl(registry: RootsRegistry) -> Dict[str, Any]:
    try:
        entries = registry.list_status()
        roots = [
            {
                "alias": entry["alias"],
                "mode": entry["mode"],
                "description": entry["description"],
                "path": entry["path"],
                "default": entry["default"],
                "available": entry["valid"],
            }
            for entry in entries
        ]
        default = next((entry["alias"] for entry in entries if entry["default"]), None)
        data: Dict[str, Any] = {"count": len(roots), "default": default, "roots": roots, "hint": ROOT_HINT}
        if not roots:
            data["hint"] = ("Brak folderów. Użytkownik dodaje je w panelu zatwierdzeń (zakładka Foldery); "
                            "możesz też poprosić o folder przez request_root.")
        return {"success": True, "data": data}
    except Exception as e:
        return _error(str(e))


def _make_executor(registry: RootsRegistry, path: str, mode: str, alias: str, description: str):
    def executor(cancel_event) -> Dict[str, Any]:
        try:
            # add() waliduje ponownie (TOCTOU): folder mógł zniknąć, a lista roots zmienić się w międzyczasie
            root = registry.add(path, mode, alias=alias, description=description)
        except RootError as exc:
            return {"started": False, "error": str(exc)}
        return {"exit_code": 0, "stdout": f"Dodano folder '{root.alias}' ({root.mode}): {root.path}",
                "stderr": "", "duration_s": 0.0}
    return executor


def request_root_impl(
    registry: RootsRegistry,
    manager: TaskManager,
    path: str,
    mode: str = "ro",
    alias: Optional[str] = None,
    description: str = "",
) -> Dict[str, Any]:
    try:
        if not isinstance(path, str) or not path.strip():
            return _error("path musi być niepustym napisem (bezwzględna ścieżka do folderu).")
        if mode not in MODES:
            return _error(f"mode musi być jednym z: {list(MODES)}.")
        if alias is not None and not isinstance(alias, str):
            return _error("alias musi być tekstem albo pominięty.")
        if not isinstance(description, str):
            return _error("description musi być tekstem.")
        alias = alias.strip() if alias and alias.strip() else None
        try:
            candidate = registry.check_add(path, mode, alias, description)
        except RootError as exc:
            return _error(str(exc))

        fields = dict(
            kind=KIND_ROOT_CHANGE,
            sensitive=True,
            title=f"Dodaj folder '{candidate.alias}' ({candidate.mode})",
            payload={"root": candidate.alias, "path": candidate.path, "mode": candidate.mode,
                     "description": candidate.description},
            approval_reason="Model prosi o dostęp do folderu. Tryb rw pozwala na zapisy (po zatwierdzeniu "
                            "każdej zmiany), ro tylko na odczyt.",
            timeout=10,
        )
        try:
            submitted = manager.submit(
                fields, _make_executor(registry, candidate.path, candidate.mode,
                                        candidate.alias, candidate.description),
                needs_approval=True)
        except (QueueFullError, TaskStateError) as e:
            return _error(str(e))

        task = manager.get(submitted.id) or submitted
        if task.status == TaskStatus.REJECTED:
            return _error("Zmiany folderów są możliwe tylko w panelu zatwierdzeń; panel jest niedostępny, "
                          "więc zadanie odrzucono. Poproś użytkownika o dodanie folderu w panelu.",
                          {"task_id": task.id, "status": task.status.value})
        return {"success": True, "data": {
            "task_id": task.id, "status": task.status.value, "root": candidate.alias,
            "path": candidate.path, "mode": candidate.mode, "hint": WAIT_HINT}}
    except Exception as e:
        return _error(str(e))
