#!/usr/bin/env python3
"""Narzędzie MCP manage_folder (5b/B2): create i rename katalogu (bez zależności od FastMCP).

Przepływ jak edit_file: plan budowany PRZED submit (błąd = brak zadania), zadanie
z needs_approval=True i natychmiastowy task_id. Operacja to jedno os.mkdir albo
os.rename, więc nie ma rollbacku. Po decyzji plan jest budowany od nowa z tych samych
argumentów i porównywany ze zgłoszonym (root, tryby, ścieżki, tożsamość źródła).
Brak przenoszenia i usuwania. Komunikaty błędów są stałe, bez str(wyjątku).
"""
import os
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from roots import Root
from roots_registry import RootsRegistry
from server.edit_tools import (
    EXEC_INTERNAL_ERROR, INTERNAL_ERROR, PANEL_DOWN_ERROR, TASK_TIMEOUT, TITLE_MAX, WAIT_HINT,
    check_description,
)
from server.file_changes import ChangeError, SensitivePolicy
from server.file_transaction import WRITE_LOCK
from server.path_guard import PathError, ResolvedPath, resolve_path
from tasks.manager import QueueFullError, TaskManager, TaskStateError
from tasks.models import TaskStatus

KIND_FOLDER = "folder"
ACTIONS = ("create", "rename")
NAME_MAX = 255
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
BACKSLASH = chr(92)

APPROVAL_REASON = ("Model prosi o operację na folderze (utworzenie lub zmiana nazwy). Folder zostanie "
                   "zmieniony dopiero po zatwierdzeniu, a przed operacją stan jest sprawdzany ponownie.")
CONFLICT_ERROR = "Konflikt: folder, ścieżka lub dostęp zmieniły się od przygotowania; nic nie zmieniono."
OP_ERROR = "Operacja na folderze nie powiodła się (zajęty, brak uprawnień lub zmiana stanu); sprawdź folder."


class FolderError(ValueError):
    """Bezpieczny komunikat dla modelu, bez surowych wyjątków I/O."""


class _Cancelled(Exception):
    pass


def _error(message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"success": False, "error": message}
    if data is not None:
        result["data"] = data
    return result


@dataclass(frozen=True)
class FolderPlan:
    action: str
    src: Optional[Path]
    target: Path
    src_identity: Optional[Tuple[int, int]]
    root: Root
    owner: Root
    target_owner: Root
    src_label: str
    target_label: str
    reasons: Tuple[str, ...]

    @property
    def sensitive(self) -> bool:
        return bool(self.reasons)

    def key(self) -> Tuple[Any, ...]:
        def ident(root: Root) -> Tuple[str, str, str]:
            return root.alias, root.path, root.mode
        return (self.action, self.src, self.target, self.src_identity,
                ident(self.root), ident(self.owner), ident(self.target_owner))


def _norm(path: Any) -> str:
    return os.path.normcase(str(path))


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE)


def _lexical(raw: str, resolved: ResolvedPath) -> Path:
    """Ścieżka bez rozwiązywania linków: do wykrycia linków pośrednich i wiszących."""
    if Path(raw).drive:
        return Path(os.path.normpath(raw))
    return Path(os.path.normpath(os.path.join(resolved.root.path, raw)))


def _check_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise FolderError("new_name jest wymagane dla rename (sama nazwa folderu).")
    if len(name) > NAME_MAX:
        raise FolderError(f"new_name może mieć najwyżej {NAME_MAX} znaków.")
    if name in (".", "..") or any(ch in name for ch in ("/", BACKSLASH, ":")):
        raise FolderError("new_name musi być samą nazwą: bez separatorów, dwukropka, '.' i '..'.")
    if any(ord(ch) < 32 for ch in name):
        raise FolderError("new_name zawiera znaki sterujące.")
    return name


def _roots_guard(registry: RootsRegistry, path: Path) -> None:
    """Zakaz create/rename dla rootów i ich przodków (obejście listy roots)."""
    for root in registry.list():
        try:
            base = Path(root.path).resolve()
        except (OSError, RuntimeError, ValueError):
            base = Path(root.path)
        if path == base or path in base.parents:
            raise FolderError("Nie można tworzyć ani zmieniać nazwy folderu będącego rootem "
                              "lub jego przodkiem; zarządza nimi użytkownik w panelu.")


def _sensitive(policy: SensitivePolicy, *resolved: ResolvedPath, src: Optional[Path]) -> Tuple[str, ...]:
    reasons = []
    for item in resolved:
        reasons.extend(policy.reasons(item))
    if src is not None and (src == policy.server_dir or src in policy.server_dir.parents):
        reasons.append("repo działającego serwera")
    return tuple(dict.fromkeys(reasons))


def _build_create(registry: RootsRegistry, path: Any, root: Any, policy: SensitivePolicy) -> FolderPlan:
    resolved = resolve_path(registry, path, root, write=True)
    lexical = _lexical(path, resolved)
    if os.path.lexists(resolved.path) or os.path.lexists(lexical):
        raise FolderError("Cel już istnieje (plik, folder lub link); create nie nadpisuje.")
    if not resolved.path.parent.is_dir():
        raise FolderError("Katalog nadrzędny musi istnieć; nie tworzymy go automatycznie.")
    _roots_guard(registry, resolved.path)
    label = f"{resolved.root.alias}:{resolved.relative}"
    return FolderPlan("create", None, resolved.path, None, resolved.root, resolved.owner, resolved.owner,
                      "", label, _sensitive(policy, resolved, src=None))


def _build_rename(registry: RootsRegistry, path: Any, new_name: Any, root: Any,
                  policy: SensitivePolicy) -> FolderPlan:
    name = _check_name(new_name)
    source = resolve_path(registry, path, root, write=True)
    lexical = _lexical(path, source)
    base = Path(os.path.normpath(source.root.path))
    reached = False
    for index, item in enumerate((lexical, *lexical.parents)):
        if _norm(item) == _norm(base):
            reached = True
            break
        try:
            info = os.lstat(item)
        except OSError:
            raise FolderError("Źródło nie istnieje lub jest niedostępne.") from None
        if _is_link(info):
            raise FolderError("Źródło lub element jego ścieżki jest linkiem/junctionem; rename odrzucony.")
        if index == 0 and not stat.S_ISDIR(info.st_mode):
            raise FolderError("Źródło musi być zwykłym katalogiem.")
    if not reached:
        raise FolderError("Nie można jednoznacznie ustalić ścieżki źródła.")
    if _norm(lexical) == _norm(base):
        raise FolderError("Nie można zmieniać nazwy roota.")
    src = source.path
    try:
        info = os.lstat(src)
    except OSError:
        raise FolderError("Źródło nie istnieje lub jest niedostępne.") from None
    if not stat.S_ISDIR(info.st_mode) or _is_link(info):
        raise FolderError("Źródło musi być zwykłym katalogiem.")
    _roots_guard(registry, src)
    destination = src.parent / name
    if _norm(destination) == _norm(src):
        raise FolderError("Nowa nazwa jest taka sama (zmiana samej wielkości liter nie jest obsługiwana).")
    target = resolve_path(registry, str(destination), source.root.alias, write=True)
    if target.path != destination:
        raise FolderError("Nowa nazwa nie wskazuje bezpośrednio w ten sam katalog nadrzędny.")
    if os.path.lexists(destination):
        raise FolderError("Cel już istnieje; rename nie nadpisuje.")
    return FolderPlan("rename", src, destination, (info.st_dev, info.st_ino), source.root, source.owner,
                      target.owner, f"{source.root.alias}:{source.relative}",
                      f"{target.root.alias}:{target.relative}",
                      _sensitive(policy, source, target, src=src))


def _build(registry: RootsRegistry, action: str, path: Any, new_name: Any, root: Any,
           policy: SensitivePolicy) -> FolderPlan:
    if action == "create":
        return _build_create(registry, path, root, policy)
    return _build_rename(registry, path, new_name, root, policy)


def _check_args(action: Any, path: Any, new_name: Any) -> None:
    if not isinstance(action, str) or action not in ACTIONS:
        raise FolderError(f"action musi być jednym z: {list(ACTIONS)}.")
    if not isinstance(path, str) or not path:
        raise FolderError("path musi być niepustym napisem.")
    if action == "create" and new_name not in (None, ""):
        raise FolderError("new_name jest używane tylko w rename.")


def _conflict(registry: RootsRegistry, args: Tuple[Any, ...], plan: FolderPlan) -> Optional[str]:
    try:
        fresh = _build(registry, *args)
    except Exception:
        return CONFLICT_ERROR
    return None if fresh.key() == plan.key() else CONFLICT_ERROR


def _acquire(lock: Any, event: threading.Event) -> None:
    while True:
        if event.is_set():
            raise _Cancelled()
        if lock.acquire(timeout=0.05):
            return


def _make_executor(registry: RootsRegistry, config_lock: Any, args: Tuple[Any, ...], plan: FolderPlan):
    """Wykonanie pod WRITE_LOCK i config_lock; nigdy nie rzuca."""
    def executor(cancel_event) -> Dict[str, Any]:
        started = time.monotonic()
        write_locked = config_locked = False
        try:
            _acquire(WRITE_LOCK, cancel_event)
            write_locked = True
            _acquire(config_lock, cancel_event)
            config_locked = True
            if cancel_event.is_set():
                raise _Cancelled()
            problem = _conflict(registry, args, plan)
            if problem:
                return {"started": False, "error": problem}
            if cancel_event.is_set():
                raise _Cancelled()
            if plan.action == "create":
                os.mkdir(plan.target)
            else:
                os.rename(plan.src, plan.target)
        except _Cancelled:
            return {"cancelled": True}
        except OSError:
            return {"started": False, "error": OP_ERROR}
        except Exception:
            return {"started": False, "error": EXEC_INTERNAL_ERROR}
        finally:
            if config_locked:
                config_lock.release()
            if write_locked:
                WRITE_LOCK.release()
        text = "Utworzono folder." if plan.action == "create" else "Zmieniono nazwę folderu."
        return {"exit_code": 0, "stdout": text, "stderr": "",
                "duration_s": round(time.monotonic() - started, 3)}
    return executor


def _payload(plan: FolderPlan, path: str, new_name: Any, description: str) -> Dict[str, Any]:
    if plan.action == "create":
        diff = f"+ {plan.target_label}/\n"
    else:
        diff = f"- {plan.src_label}/\n+ {plan.target_label}/\n"
    paths = [plan.target_label] if plan.action == "create" else [plan.src_label, plan.target_label]
    relative = plan.target_label.split(":", 1)[1] if plan.action == "create" else plan.src_label.split(":", 1)[1]
    return {
        "root": plan.root.alias, "action": plan.action, "path": relative,
        "new_name": new_name if plan.action == "rename" else None,
        "target": plan.target_label.split(":", 1)[1], "paths": paths, "diff": diff,
        "description": description,
    }


def manage_folder_impl(
    registry: RootsRegistry,
    manager: TaskManager,
    config_lock: Any,
    action: Any,
    path: Any,
    new_name: Any = None,
    root: Any = None,
    description: Any = "",
    policy: Optional[SensitivePolicy] = None,
) -> Dict[str, Any]:
    try:
        policy = policy if policy is not None else SensitivePolicy()
        try:
            text = check_description(description)
            _check_args(action, path, new_name)
            plan = _build(registry, action, path, new_name, root, policy)
        except (FolderError, ChangeError, PathError) as exc:
            return _error(str(exc))
        args = (action, path, new_name, root, policy)
        if plan.action == "create":
            title = f"Utwórz folder {plan.target_label}"
        else:
            title = f"Zmień nazwę {plan.src_label} → {new_name}"
        reason = APPROVAL_REASON
        if plan.reasons:
            reason += " Zmiana wrażliwa: " + ", ".join(plan.reasons) + "."
        fields = dict(
            kind=KIND_FOLDER, sensitive=plan.sensitive, title=title[:TITLE_MAX],
            payload=_payload(plan, path, new_name, text), approval_reason=reason, timeout=TASK_TIMEOUT,
        )
        try:
            submitted = manager.submit(
                fields, _make_executor(registry, config_lock, args, plan), needs_approval=True,
                verify=lambda: _conflict(registry, args, plan))
        except (QueueFullError, TaskStateError) as exc:
            return _error(str(exc))
        task = manager.get(submitted.id) or submitted
        if task.status == TaskStatus.REJECTED:
            return _error(PANEL_DOWN_ERROR, {"task_id": task.id, "status": task.status.value})
        return {"success": True, "data": {
            "task_id": task.id, "status": task.status.value, "kind": KIND_FOLDER, "action": plan.action,
            "paths": fields["payload"]["paths"], "sensitive": task.sensitive, "hint": WAIT_HINT}}
    except Exception:
        return _error(INTERNAL_ERROR)
