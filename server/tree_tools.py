"""Create-only tree plans: one approval, private bytes, best-effort rollback.

Not crash-atomic and not a filesystem sandbox against external TOCTOU races.
"""
import hashlib
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from server.edit_tools import (check_description, _diff_text, _file_meta, WAIT_HINT,
                               PANEL_DOWN_ERROR, INTERNAL_ERROR, TASK_TIMEOUT)
from server.file_changes import (ChangeError, PreparedChanges, SensitivePolicy,
                                 _binding, _file, _text, _totals)
from server.folder_tools import _lexical, _is_link, _roots_guard
from server.file_transaction import WRITE_LOCK
from server.path_guard import resolve_path
from tasks.models import TaskStatus

MAX_DIRECTORIES = 200
MAX_TREE_FILES = 100
CONFLICT = "Konflikt: struktura, ścieżka lub dostęp zmieniły się; nic nie zapisano."


@dataclass(frozen=True, repr=False)
class TreePlan:
    directories: tuple
    anchors: tuple
    bindings: tuple
    changes: PreparedChanges
    reasons: tuple

    def key(self):
        return (self.directories, self.anchors, self.bindings,
                tuple((f.path, f.after_hash) for f in self.changes.files), self.reasons)


def _identity(path):
    info = path.lstat()
    return info.st_dev, info.st_ino


def _guard(registry, raw, root):
    resolved = resolve_path(registry, raw, root, write=True)
    lexical = _lexical(raw, resolved)
    base = Path(resolved.root.path)
    for part in (lexical, *lexical.parents):
        if os.path.lexists(part):
            if _is_link(part.lstat()):
                raise ChangeError("Linki i junctiony w strukturze są niedozwolone.")
        if os.path.normcase(str(part)) == os.path.normcase(str(base)):
            break
    return resolved


def prepare_tree(registry, directories, files, root=None, policy=None):
    policy = policy or SensitivePolicy()
    if not isinstance(directories, list) or len(directories) > MAX_DIRECTORIES:
        raise ChangeError("directories musi być listą najwyżej 200 ścieżek.")
    if not isinstance(files, list) or len(files) > MAX_TREE_FILES:
        raise ChangeError("files musi być listą najwyżej 100 wpisów.")
    if not directories and not files:
        raise ChangeError("Podaj katalogi lub pliki.")
    missing, anchors, bindings, prepared, reasons = {}, {}, [], [], []
    file_paths = set()

    def bind(raw):
        item = _guard(registry, raw, root)
        bindings.append((raw, item.root, item.owner, item.path))
        reasons.extend(policy.reasons(item))
        return item

    def directory(path, alias):
        while True:
            item = _guard(registry, str(path), alias)
            if os.path.lexists(path):
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise ChangeError("Katalog koliduje z plikiem lub linkiem.")
                anchors[path] = _identity(path)
                return
            _roots_guard(registry, path)
            missing[path] = (alias, item.owner)
            reasons.extend(policy.reasons(item))
            if len(missing) > MAX_DIRECTORIES:
                raise ChangeError("Drzewo przekracza 200 brakujących katalogów.")
            path = path.parent

    for raw in directories:
        item = bind(raw)
        directory(item.path, item.root.alias)
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"filepath", "content"}:
            raise ChangeError("Wpis files wymaga wyłącznie filepath i content.")
        item = bind(entry["filepath"])
        key = os.path.normcase(str(item.path))
        if key in file_paths or os.path.lexists(item.path):
            raise ChangeError("Plik docelowy istnieje lub występuje wielokrotnie.")
        file_paths.add(key)
        directory(item.path.parent, item.root.alias)
        _, binding = _binding(registry, entry["filepath"], item.root.alias)
        prepared.append(_file((binding,), None, _text(entry["content"], "content"),
                              False, policy.reasons(item)))
        _totals(tuple(prepared))
    if any(os.path.normcase(str(p)) in file_paths for p in missing):
        raise ChangeError("Ta sama ścieżka jest plikiem i katalogiem.")
    if not missing and not prepared:
        raise ChangeError("Brak zmian: wszystkie katalogi już istnieją.")
    return TreePlan(tuple((p, *missing[p]) for p in sorted(missing, key=lambda p: (len(p.parts), str(p)))),
                    tuple(sorted(anchors.items(), key=lambda x: str(x[0]))), tuple(bindings),
                    PreparedChanges(tuple(prepared)), tuple(sorted(set(reasons))))


def _verify(registry, args, plan):
    try:
        return None if prepare_tree(registry, *args).key() == plan.key() else CONFLICT
    except Exception:
        return CONFLICT


def _executor(registry, config_lock, args, plan):
    def execute(cancel_event):
        started = time.monotonic()
        created_dirs, created_files = [], []
        locked = []
        rollback_ok = True
        try:
            for lock in (WRITE_LOCK, config_lock):
                while not lock.acquire(timeout=0.05):
                    if cancel_event.is_set():
                        raise InterruptedError()
                locked.append(lock)
            if cancel_event.is_set():
                raise InterruptedError()
            if _verify(registry, args, plan):
                return {"started": False, "error": CONFLICT}

            def check(path, alias):
                if cancel_event.is_set():
                    raise InterruptedError()
                if _guard(registry, str(path), alias).path != path:
                    raise ChangeError("Ścieżka zmieniła się podczas zapisu.")
                expected = dict(plan.anchors)
                expected.update(created_dirs)
                for parent in path.parents:
                    if parent in expected and _identity(parent) != expected[parent]:
                        raise ChangeError("Katalog nadrzędny zmienił się podczas zapisu.")

            for path, alias, owner in plan.directories:
                check(path, alias)
                os.mkdir(path)
                created_dirs.append((path, _identity(path)))
            for item in plan.changes.files:
                check(item.path, item.bindings[0].root.alias)
                # Exclusive creation: never overwrite a concurrently created file.
                with item.path.open("xb") as stream:
                    info = os.fstat(stream.fileno())
                    record = [item.path, (info.st_dev, info.st_ino), hashlib.sha256(b"").hexdigest()]
                    created_files.append(record)
                    try:
                        stream.write(item.after)
                        stream.flush()
                    finally:
                        # Track actual bytes even if write failed partway through.
                        stream.flush()
                        record[2] = hashlib.sha256(item.path.read_bytes()).hexdigest()
            return {"exit_code": 0, "stdout": f"Utworzono katalogów: {len(created_dirs)}, plików: {len(created_files)}.",
                    "stderr": "", "duration_s": round(time.monotonic() - started, 3)}
        except Exception:
            for path, identity, digest in reversed(created_files):
                try:
                    if (_identity(path) != identity or _is_link(path.lstat())
                            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
                            or any(p == parent and _identity(p) != ident
                                   for p, ident in created_dirs for parent in path.parents)):
                        raise OSError()
                    path.unlink()
                except Exception:
                    rollback_ok = False
            for path, identity in reversed(created_dirs):
                try:
                    if _identity(path) != identity or _is_link(path.lstat()):
                        raise OSError()
                    path.rmdir()  # Only empty, owned directories.
                except Exception:
                    rollback_ok = False
            if not rollback_ok:
                return {"started": False, "error": "Niepełny rollback struktury; sprawdź pliki i katalogi przed ponowieniem."}
            if cancel_event.is_set():
                return {"cancelled": True}
            return {"started": False, "error": "Tworzenie struktury nie powiodło się; własne zmiany wycofano."}
        finally:
            for lock in reversed(locked):
                lock.release()
    return execute


def create_tree_impl(registry, manager, config_lock, directories=None, files=None,
                     root=None, description="", policy=None):
    try:
        text = check_description(description)
        args = (directories if directories is not None else [],
                files if files is not None else [], root, policy or SensitivePolicy())
        plan = prepare_tree(registry, *args)
        preview = plan.changes.preview()
        labels = [f"{alias}:{path.relative_to(Path(registry.resolve(alias).path)).as_posix()}/"
                  for path, alias, owner in plan.directories]
        paths = labels + [f"{e['root']}:{e['filepath']}" for e in preview["files"]]
        payload = {"paths": paths, "directories": labels,
                   "files": [_file_meta(e) for e in preview["files"]],
                   "diff": "".join(f"+ {label}\n" for label in labels) + _diff_text(preview),
                   "description": text, "truncated": preview["truncated"]}
        submitted = manager.submit(
            dict(kind="tree_create", title=f"Utwórz strukturę ({len(labels)} katalogów, {len(plan.changes.files)} plików)",
                 sensitive=bool(plan.reasons), payload=payload, timeout=TASK_TIMEOUT,
                 approval_reason="Utworzenie całej struktury jedną zgodą. Sprawdź listę katalogów i diffy plików."),
            _executor(registry, config_lock, args, plan), needs_approval=True,
            verify=lambda: _verify(registry, args, plan))
        task = manager.get(submitted.id) or submitted
        if task.status == TaskStatus.REJECTED:
            return {"success": False, "error": PANEL_DOWN_ERROR,
                    "data": {"task_id": task.id, "status": task.status.value}}
        return {"success": True, "data": {"task_id": task.id, "status": task.status.value,
                "kind": "tree_create", "paths": paths, "sensitive": task.sensitive,
                "truncated": preview["truncated"], "hint": WAIT_HINT}}
    except ChangeError as exc:
        return {"success": False, "error": str(exc)}
    except Exception:
        return {"success": False, "error": INTERNAL_ERROR}
