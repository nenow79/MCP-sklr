#!/usr/bin/env python3
"""Odczyt plików w roots: czysta logika narzędzi, bez FastMCP i bez zapisów."""
import difflib
from pathlib import Path
from typing import Any, Dict

from roots_registry import RootsRegistry
from server.path_guard import PathError, resolve_path
from server.secrets_mask import is_blocked_secret, mask_text

READ_LIMIT_BYTES = 5 * 1024 * 1024
SKIP_DIRS = frozenset({
    ".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
    "coverage", ".pytest_cache",
})
DIRECTORY_LIMIT = 500


def _error(message: str) -> Dict[str, Any]:
    return {"success": False, "error": message}


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Parametr {name} musi być liczbą całkowitą (nie bool).")
    return min(high, max(low, value))


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"Parametr {name} musi być bool.")
    return value


def _line(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Parametr {name} musi być liczbą całkowitą (nie bool).")
    return max(1, value)


def _eol(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    kinds = [name for name, count in (("CRLF", crlf), ("LF", lf), ("CR", cr)) if count]
    return kinds[0] if len(kinds) == 1 else ("mixed" if kinds else "none")


def _decode(path: Path) -> str:
    if is_blocked_secret(path.name):
        raise ValueError("Odczyt plików .pfx i .p12 jest zablokowany.")
    if not path.is_file():
        raise ValueError("Ścieżka nie wskazuje istniejącego zwykłego pliku.")
    if path.stat().st_size > READ_LIMIT_BYTES:
        raise ValueError("Plik przekracza limit odczytu 5 MB.")
    with path.open("rb") as stream:
        raw = stream.read(READ_LIMIT_BYTES + 1)
    if len(raw) > READ_LIMIT_BYTES:
        raise ValueError("Plik przekracza limit odczytu 5 MB.")
    # NUL oraz nietekstowe znaki sterujące: również UTF-16 odrzucamy jako nieobsługiwany.
    if any(byte < 32 and byte not in (9, 10, 12, 13) for byte in raw):
        raise ValueError("Plik binarny lub nieobsługiwane kodowanie.")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1250")
        except UnicodeDecodeError as exc:
            raise ValueError("Nie można odczytać pliku jako UTF-8 ani cp1250.") from exc


def _mask_lines(filename: str, text: str):
    """Maskujemy całość, zachowując numery fizycznych linii nawet przy skróceniu PEM.

    Masker może zastąpić wieloliniowy blok jedną linią. Puste linie w miejsce usuniętych
    zapewniają, że zakres nie przesunie późniejszej treści. Żaden fragment oryginalnej
    treści nie jest przywracany przez wyrównanie.
    """
    result = mask_text(filename, text)
    original = text.splitlines(keepends=True)
    masked = result.text.splitlines(keepends=True)
    if len(original) == len(masked):
        return result, masked
    aligned = []
    matcher = difflib.SequenceMatcher(a=original, b=masked, autojunk=False)
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        replacement = masked[b1:b2]
        if tag != "equal":
            missing = a2 - a1 - len(replacement)
            if missing > 0:
                replacement = replacement + ["\n"] * missing
            elif missing < 0:
                # Np. jednoliniowy surowy klucz: cała zamaskowana treść w jednym slocie.
                slots = a2 - a1
                if slots:
                    replacement = replacement[:slots - 1] + ["".join(replacement[slots - 1:])]
        aligned.extend(replacement)
    return result, aligned


def _read(registry: RootsRegistry, filepath: Any, start_line: Any, end_line: Any,
          max_chars: Any, root: Any, line_numbers: Any, budget: Any = None) -> Dict[str, Any]:
    limit = _integer(max_chars, "max_chars", 1000, 80000)
    numbered = _boolean(line_numbers, "line_numbers")
    start = _line(start_line, "start_line") or 1
    end = _line(end_line, "end_line")
    if end is not None and end < start:
        raise ValueError("end_line nie może być mniejsze niż start_line.")
    resolved = resolve_path(registry, filepath, root)
    text = _decode(resolved.path)
    total_lines = len(text.splitlines(keepends=True))
    mask, lines = _mask_lines(resolved.path.name, text)
    selected = lines[start - 1:end]
    if numbered:
        content = "".join(f"{number}: {line}" for number, line in enumerate(selected, start))
    else:
        content = "".join(selected)
    if budget is not None:
        limit = min(limit, budget)
    truncated = len(content) > limit
    content = content[:limit]
    return {"success": True, "data": {
        "root": resolved.root.alias, "filepath": resolved.relative, "content": content,
        "start_line": start, "end_line": min(end if end is not None else total_lines, total_lines),
        "total_lines": total_lines, "eol": _eol(text), "truncated": truncated,
        "masked": mask.masked, "masked_count": mask.count,
    }}


def read_file_impl(registry: RootsRegistry, filepath: Any, start_line: Any = None,
                   end_line: Any = None, max_chars: Any = 12000, root: Any = None,
                   line_numbers: Any = False) -> Dict[str, Any]:
    try:
        return _read(registry, filepath, start_line, end_line, max_chars, root, line_numbers)
    except (PathError, ValueError, OSError, RuntimeError) as exc:
        return _error(str(exc))


def list_directory_impl(registry: RootsRegistry, subpath: Any = "",
                        root: Any = None) -> Dict[str, Any]:
    try:
        resolved = resolve_path(registry, subpath, root, allow_empty=True)
        if not resolved.path.is_dir():
            return _error("Ścieżka nie wskazuje istniejącego katalogu.")
        entries = []
        truncated = False
        with __import__("os").scandir(resolved.path) as children:
            for child in children:
                # Nie śledzimy linków przy klasyfikacji ani nie ujawniamy celów poza rootami.
                if child.is_symlink():
                    continue
                is_dir = child.is_dir(follow_symlinks=False)
                if is_dir and child.name.lower() in SKIP_DIRS:
                    continue
                try:
                    resolve_path(registry, str(Path(child.path)), resolved.root.alias)
                except PathError:
                    continue
                if len(entries) == DIRECTORY_LIMIT:
                    truncated = True
                    break
                entries.append(child.name + ("/" if is_dir else ""))
        entries.sort(key=lambda name: (not name.endswith("/"), name.casefold(), name))
        return {"success": True, "data": {
            "root": resolved.root.alias, "subpath": resolved.relative,
            "entries": entries, "count": len(entries), "truncated": truncated,
        }}
    except (PathError, ValueError, OSError, RuntimeError) as exc:
        return _error(str(exc))


def read_files_batch_impl(registry: RootsRegistry, requests: Any,
                         max_total_chars: Any = 24000) -> Dict[str, Any]:
    try:
        budget = _integer(max_total_chars, "max_total_chars", 1000, 80000)
        if not isinstance(requests, list) or not 1 <= len(requests) <= 10:
            return _error("requests musi być listą od 1 do 10 wpisów.")
        remaining = budget
        results = []
        truncated = False
        allowed = {"filepath", "start_line", "end_line", "max_chars", "root", "line_numbers"}
        for index, request in enumerate(requests):
            try:
                if not isinstance(request, dict):
                    raise ValueError("Wpis requests musi być obiektem.")
                if "filepath" not in request:
                    raise ValueError("Wpis requests wymaga filepath.")
                if set(request) - allowed:
                    raise ValueError("Wpis requests zawiera nieznane parametry.")
                result = _read(
                    registry, request["filepath"], request.get("start_line"), request.get("end_line"),
                    request.get("max_chars", 12000), request.get("root"),
                    request.get("line_numbers", False), budget=remaining)
                remaining -= len(result["data"]["content"])
                truncated = truncated or result["data"]["truncated"]
            except (PathError, ValueError, OSError, RuntimeError) as exc:
                result = _error(str(exc))
            result["index"] = index
            results.append(result)
        return {"success": True, "data": {
            "results": results, "count": len(results), "total_chars_returned": budget - remaining,
            "max_total_chars": budget, "truncated": truncated,
        }}
    except ValueError as exc:
        return _error(str(exc))
