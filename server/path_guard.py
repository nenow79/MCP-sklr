#!/usr/bin/env python3
"""Path guard: zamienia ścieżkę podaną przez model na bezpieczną ścieżkę wewnątrz roota.

Czysta logika bez zależności od FastMCP. Kolejność: odrzucenie składni niebezpiecznej dla Windows
(UNC, strumienie NTFS, nazwy zarezerwowane, znaki wieloznaczne), potem resolve() (rozwija 8.3,
symlinki i junctiony) i dopiero wynik porównujemy z rootem. Zapisy (faza 5b) muszą wołać
resolve_path ponownie tuż przed zapisem (TOCTOU).
"""
import ntpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roots import MODE_RW, Root, RootError
from roots_registry import RootsRegistry

BACKSLASH = chr(92)
UNC_PREFIX = BACKSLASH * 2
_DEFAULT_WORDS = ("", "false", "none", "null")
_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_WILDCARDS = frozenset('<>"|?*')
_SPLIT_RE = re.compile(r"[\\/]+")


class PathError(ValueError):
    """Ścieżka odrzucona (komunikat po polsku, gotowy do pokazania modelowi)."""


@dataclass(frozen=True)
class ResolvedPath:
    root: Root      # root, względem którego rozwiązano ścieżkę
    owner: Root     # najgłębszy root zawierający ścieżkę; jego tryb decyduje o zapisie
    path: Path      # ścieżka bezwzględna po resolve()
    relative: str   # względem `root`, w stylu posix ("." dla samego roota)

    @property
    def writable(self) -> bool:
        return self.owner.mode == MODE_RW


def _is_default(root: Any) -> bool:
    return root is None or root is False or (isinstance(root, str) and root.strip().lower() in _DEFAULT_WORDS)


def _within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathError(f"Nie można rozwiązać ścieżki: {exc}") from exc


def _check_raw(raw: str) -> None:
    """Odrzuca składnię niebezpieczną dla Windows, zanim ścieżka trafi do resolve()."""
    if any(ord(ch) < 32 for ch in raw):
        raise PathError("Ścieżka zawiera znaki sterujące.")
    normalized = raw.replace("/", BACKSLASH)
    if normalized.startswith(UNC_PREFIX):
        raise PathError("Ścieżki sieciowe (UNC) i z prefiksem urządzenia są niedozwolone.")
    drive, rest = ntpath.splitdrive(normalized)
    if ":" in rest:
        raise PathError("Strumienie NTFS (dwukropek w nazwie pliku) są niedozwolone.")
    if any(ch in _WILDCARDS for ch in rest):
        raise PathError("Ścieżka zawiera niedozwolone znaki (< > \" | ? *).")
    if drive and not rest.startswith(BACKSLASH):
        raise PathError("Ścieżka z literą dysku musi być pełna (np. C:" + BACKSLASH + "projekt" + BACKSLASH + "plik).")
    if not drive and rest.startswith(BACKSLASH):
        raise PathError(
            "Ścieżka zaczynająca się od ukośnika jest niejednoznaczna: podaj ścieżkę względną "
            "względem folderu (root) albo pełną z literą dysku.")
    for part in _SPLIT_RE.split(rest):
        if part in ("", ".", ".."):
            continue
        if part != part.rstrip(" ."):
            raise PathError(f"Nazwa '{part}' kończy się kropką lub spacją (niedozwolone w Windows).")
        if part.split(".")[0].rstrip(" ").upper() in _RESERVED:
            raise PathError(f"Nazwa '{part}' jest zarezerwowana w Windows (urządzenie).")


def _aliases(registry: RootsRegistry) -> str:
    return ", ".join(root.alias for root in registry.list()) or "brak"


def resolve_path(registry: RootsRegistry, path: Any, root: Any = None, *,
                 write: bool = False, allow_empty: bool = False) -> ResolvedPath:
    """Rozwiązuje ścieżkę w rootcie. Zgłasza PathError (nigdy nie zwraca ścieżki spoza roots).

    root: alias; brak, pusty napis lub 'false' = domyślny. Ścieżka bezwzględna bez podanego `root`
    wybiera root po położeniu. write=True wymaga trybu rw najgłębszego roota zawierającego ścieżkę.
    allow_empty=True: pusta ścieżka oznacza sam root (dla katalogów).
    """
    explicit = not _is_default(root)
    if path is None:
        path = ""
    if not isinstance(path, str):
        raise PathError("Ścieżka musi być tekstem.")
    raw = path  # nie usuwamy spacji ani znaków sterujących przed walidacją
    if not raw:
        if not allow_empty:
            raise PathError("Podaj ścieżkę.")
        raw = "."
    _check_raw(raw)

    candidate = Path(raw)
    absolute = bool(candidate.drive)
    if absolute and not explicit:
        target = _safe_resolve(candidate)
        base_root = registry.find(target)
        if base_root is None:
            raise PathError(
                f"Ścieżka wychodzi poza dozwolone foldery. Dostępne foldery: {_aliases(registry)}.")
    else:
        try:
            base_root = registry.resolve(root)
        except RootError as exc:
            raise PathError(str(exc)) from exc
        target = _safe_resolve(candidate if absolute else Path(base_root.path) / candidate)
    base = _safe_resolve(Path(base_root.path))

    if not _within(target, base):
        other = registry.find(target)
        if absolute and not explicit and other is not None:
            base_root, base = other, _safe_resolve(Path(other.path))  # root wybrany po ścieżce
        elif other is not None and other.alias != base_root.alias:
            raise PathError(
                f"Ścieżka leży w folderze '{other.alias}', a nie '{base_root.alias}'. "
                f"Użyj parametru root='{other.alias}'.")
        else:
            raise PathError(
                f"Ścieżka wychodzi poza dozwolone foldery (root '{base_root.alias}'). "
                f"Dostępne foldery: {_aliases(registry)}.")

    owner = registry.find(target) or base_root
    if write and owner.mode != MODE_RW:
        raise PathError(f"Folder '{owner.alias}' jest tylko do odczytu (tryb ro); zapis niedozwolony.")
    relative = target.relative_to(base).as_posix()
    return ResolvedPath(root=base_root, owner=owner, path=target, relative=relative)
