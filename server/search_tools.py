#!/usr/bin/env python3
"""Dosłowne wyszukiwanie w roots: maskowanie przed dopasowaniem, bez ripgrepa."""
import os
import stat
import time
from pathlib import Path
from typing import Any, Dict

from roots import RootError
from roots_registry import RootsRegistry
from server.file_tools import SKIP_DIRS, _boolean, _integer, _mask_lines
from server.path_guard import PathError, resolve_path
from server.secrets_mask import is_blocked_secret

SEARCH_LIMIT_BYTES = 1024 * 1024
SEARCH_SECONDS = 6.0


class _Deadline(Exception):
    pass


def _check_time(deadline):
    if time.monotonic() >= deadline:
        raise _Deadline()


def _is_link(path):
    info = path.lstat()
    # Windows junctiony (również Python bez Path.is_junction) i inne reparse points.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _linked_ancestor(path, base):
    current = path
    while True:
        try:
            if _is_link(current):
                return True
        except FileNotFoundError:
            pass
        if current == base or current.parent == current:
            return False
        current = current.parent


def _read_text(path):
    """Ograniczamy również sam read(), nie tylko rozmiar sprawdzony przez stat()."""
    if is_blocked_secret(path.name) or path.stat().st_size > SEARCH_LIMIT_BYTES:
        return None
    with path.open("rb") as stream:
        raw = stream.read(SEARCH_LIMIT_BYTES + 1)
    if len(raw) > SEARCH_LIMIT_BYTES:
        return None
    if any(byte < 32 and byte not in (9, 10, 12, 13) for byte in raw):
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1250")
        except UnicodeDecodeError:
            return None


def _files(path, deadline, warnings, alias):
    _check_time(deadline)
    if _is_link(path):
        return
    if path.is_file():
        yield path
        return
    stack = [path]
    while stack:
        _check_time(deadline)
        current = stack.pop()
        try:
            children = []
            with os.scandir(current) as entries:
                for entry in entries:
                    _check_time(deadline)
                    children.append(entry)
            children.sort(key=lambda entry: (entry.name.casefold(), entry.name))
            directories = []
            for entry in children:
                _check_time(deadline)
                child = Path(entry.path)
                try:
                    if _is_link(child):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.lower() not in SKIP_DIRS:
                            directories.append(child)
                    elif entry.is_file(follow_symlinks=False):
                        yield child
                except OSError:
                    # Bez ścieżek/treści z wyjątków: ostrzeżenia są zwięzłe i ograniczone.
                    _warn(warnings, alias, "Pominięto niedostępny wpis.")
            stack.extend(reversed(directories))
        except OSError:
            _warn(warnings, alias, "Pominięto niedostępny katalog.")


def _warn(warnings, alias, message):
    item = {"root": alias, "error": message}
    if item not in warnings and len(warnings) < 20:
        warnings.append(item)


def _available(base, deadline):
    names = []
    with os.scandir(base) as entries:
        for entry in entries:
            _check_time(deadline)
            if len(names) >= 50:
                break
            if (entry.name.lower() not in SKIP_DIRS and not _is_link(Path(entry.path))
                    and entry.is_dir(follow_symlinks=False)):
                names.append(entry.name + "/")
    return ", ".join(sorted(names, key=str.casefold)) or "brak"


def search_code_impl(registry: RootsRegistry, query: Any, subpath: Any = "",
                     case_sensitive: Any = False, context_lines: Any = 0,
                     max_matches: Any = 30, max_total_chars: Any = 24000,
                     root: Any = None) -> Dict[str, Any]:
    """Budżet 6 s jest kooperacyjny: nie przerywa blokującej operacji systemu plików.

    count = liczba dopasowanych linii w zwróconych fragmentach; metadata zakresu opisuje
    pełne okno także wtedy, gdy jego content został skrócony limitem znaków.
    """
    deadline = time.monotonic() + SEARCH_SECONDS
    results, warnings = [], []
    used = count = files_scanned = skipped_files = 0
    reason = None
    valid_roots = 0
    try:
        if not isinstance(query, str) or not query:
            raise ValueError("query musi być niepustym tekstem.")
        if subpath is None:
            subpath = ""
        if not isinstance(subpath, str):
            raise ValueError("subpath musi być tekstem.")
        sensitive = _boolean(case_sensitive, "case_sensitive")
        context = _integer(context_lines, "context_lines", 0, 30)
        matches_limit = _integer(max_matches, "max_matches", 1, 500)
        chars_limit = _integer(max_total_chars, "max_total_chars", 1000, 80000)
        all_roots = isinstance(root, str) and root.strip() == "*"
        if all_roots:
            roots = registry.list()
            if not roots:
                raise ValueError("Brak dostępnych folderów (roots).")
            if Path(subpath).drive:
                roots = [resolve_path(registry, subpath).root]
        else:
            # Path guard wybierze po położeniu również absolutną ścieżkę bez domyślnego.
            roots = [resolve_path(registry, subpath, root, allow_empty=True).root]
        needle = query if sensitive else query.casefold()
        for selected_root in roots:
            _check_time(deadline)
            try:
                resolved = resolve_path(registry, subpath, selected_root.alias, allow_empty=True)
                base = Path(selected_root.path)
                raw_path = Path(subpath or ".")
                lexical = raw_path if raw_path.drive else base / raw_path
                if _linked_ancestor(lexical, base):
                    raise ValueError("Symlinki i junctiony nie są przeszukiwane.")
                if not resolved.path.exists():
                    available = _available(base, deadline)
                    raise ValueError("Podany subpath nie istnieje. Dostępne katalogi w root: " + available)
                if not (resolved.path.is_dir() or resolved.path.is_file()):
                    raise ValueError("subpath musi wskazywać zwykły plik lub katalog.")
                valid_roots += 1
                if any(part.lower() in SKIP_DIRS for part in Path(resolved.relative).parts):
                    continue
            except (PathError, ValueError, OSError, RuntimeError) as exc:
                if not all_roots:
                    raise
                _warn(warnings, selected_root.alias, str(exc))
                continue
            for file_path in _files(resolved.path, deadline, warnings, selected_root.alias):
                _check_time(deadline)
                try:
                    # Ponowna kontrola po enumeracji; nie ufamy samemu os.walk/scandir.
                    if _linked_ancestor(file_path, base):
                        skipped_files += 1
                        continue
                    checked = resolve_path(registry, str(file_path), selected_root.alias)
                    text = _read_text(checked.path)
                    _check_time(deadline)
                    if text is None:
                        skipped_files += 1
                        continue
                    mask, physical_lines = _mask_lines(checked.path.name, text)
                    _check_time(deadline)
                    lines = [line.rstrip("\r\n") for line in physical_lines]
                    files_scanned += 1
                    windows = []
                    found = 0
                    for number, line in enumerate(lines, 1):
                        _check_time(deadline)
                        haystack = line if sensitive else line.casefold()
                        if needle not in haystack:
                            continue
                        start, end = max(1, number - context), min(len(lines), number + context)
                        if windows and start <= windows[-1]["end"] + 1:
                            windows[-1]["end"] = max(windows[-1]["end"], end)
                            windows[-1]["matches"].append(number)
                        else:
                            windows.append({"start": start, "end": end, "matches": [number]})
                        found += 1
                        if count + found >= matches_limit:
                            reason = "max_matches"
                            break
                    for window in windows:
                        _check_time(deadline)
                        remaining = chars_limit - used
                        if remaining <= 0:
                            reason = "max_total_chars"
                            break
                        content = "\n".join(
                            f"{number}: {lines[number - 1]}"
                            for number in range(window["start"], window["end"] + 1))
                        shortened = len(content) > remaining
                        content = content[:remaining]
                        results.append({
                            "root": selected_root.alias, "file": checked.relative,
                            "start_line": window["start"], "end_line": window["end"],
                            "total_lines": len(lines), "match_lines": window["matches"],
                            "content": content, "truncated": shortened,
                            "masked": mask.masked, "masked_count": mask.count,
                        })
                        used += len(content)
                        count += len(window["matches"])
                        if shortened:
                            reason = "max_total_chars"
                            break
                    if reason:
                        break
                except (PathError, ValueError, OSError, RuntimeError):
                    skipped_files += 1
                    _warn(warnings, selected_root.alias, "Pominięto niedostępny plik.")
            if reason:
                break
        if not valid_roots:
            return {"success": False, "error": "Nie można przeszukać żadnego roota.",
                    "data": {"warnings": warnings}}
    except _Deadline:
        reason = "time_budget"
    except (RootError, PathError, ValueError, OSError, RuntimeError) as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": {
        "query": query, "count": count, "results": results,
        "total_chars_returned": used, "max_total_chars": chars_limit,
        "truncated": reason is not None, "truncated_reason": reason,
        "files_scanned": files_scanned, "skipped_files": skipped_files,
        "warnings": warnings,
    }}
