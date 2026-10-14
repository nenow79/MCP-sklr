"""Ścieżki w bundle: normalizacja linków OKF, kolizje wielkości liter i bezpieczne odnajdywanie plików.

Część czysta (normalize_link, find_case_collisions) nie dotyka dysku. Adapter dyskowy
(locate_bundle_dir, resolve_concept_file) odrzuca symlinki, junctiony i inne reparse points na każdym
elemencie ścieżki, nie tylko w wyniku. Nie jest to ochrona przed wyścigami TOCTOU.
"""
import os
import re
import stat
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .models import IdentityError, validate_concept_id, validate_path_segment

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
MAX_LINK_LENGTH = 1024


class PathSecurityError(Exception):
    """Błąd ścieżki; code: invalid_id, invalid_subpath, not_found, not_file, ambiguous, link, outside."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_link(link: object, from_concept_id: str) -> Optional[str]:
    """Link OKF do .md -> concept_id wewnątrz bundle albo None (URL, poza bundle, niepoprawny, reserved).

    Ukośnik początkowy oznacza korzeń bundle. Fragment (#...) i query są odcinane, procent-kodowanie nie
    jest dekodowane. Wynik nie potwierdza istnienia pliku ani dostępu.
    """
    if not isinstance(link, str) or not link or len(link) > MAX_LINK_LENGTH:
        return None
    if "\\" in link or any(ord(ch) < 32 or ord(ch) == 127 for ch in link):
        return None
    try:
        validate_concept_id(from_concept_id)
    except IdentityError:
        return None
    target = link.split("#", 1)[0].split("?", 1)[0]
    if not target or target.startswith("//") or _SCHEME_RE.match(target):
        return None
    if not target.lower().endswith(".md"):
        return None
    if target.startswith("/"):
        parts: List[str] = []
        rest = target[1:]
    else:
        parts = from_concept_id.split("/")[:-1]
        rest = target
    for segment in rest.split("/"):
        if segment == "":
            return None
        if segment == ".":
            continue
        if segment == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(segment)
    concept_id = "/".join(parts)[:-3]
    try:
        validate_concept_id(concept_id)
    except IdentityError:
        return None
    return concept_id


def _fold(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def find_case_collisions(concept_ids: Iterable[str]) -> List[Tuple[str, ...]]:
    """Grupy różnych identyfikatorów, które różnią się tylko wielkością liter / normalizacją Unicode."""
    groups: Dict[str, set] = {}
    for concept_id in concept_ids:
        groups.setdefault(_fold(concept_id), set()).add(concept_id)
    return sorted(tuple(sorted(ids)) for ids in groups.values() if len(ids) > 1)


def relative_file(concept_id: str) -> str:
    validate_concept_id(concept_id)
    return concept_id + ".md"


def _info_is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def is_link(path: Path) -> bool:
    """Symlink, junction lub inny reparse point (nieistniejąca ścieżka to False)."""
    try:
        return _info_is_link(path.lstat())
    except OSError:
        return False


def _within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def check_directory_chain(path: Path) -> None:
    """Sprawdza katalog i wszystkich przodków przed resolve; błędy I/O oznaczają odmowę."""
    path = Path(path)
    if not path.is_absolute():
        raise PathSecurityError("outside")
    for current in reversed((path,) + tuple(path.parents)):
        try:
            info = current.lstat()
        except (OSError, ValueError):
            raise PathSecurityError("not_found") from None
        if _info_is_link(info):
            raise PathSecurityError("link")
        if not stat.S_ISDIR(info.st_mode):
            raise PathSecurityError("not_found")


def locate_bundle_dir(root_path: str, subpath: str) -> Path:
    """Katalog bundle = root_path/subpath po resolve(); zwraca ścieżkę albo zgłasza PathSecurityError."""
    base = Path(root_path)
    current = base
    if subpath:
        if "\\" in subpath or subpath.startswith("/") or ":" in subpath:
            raise PathSecurityError("invalid_subpath")
        segments = subpath.split("/")
        for segment in segments:  # najpierw składnia całej ścieżki
            try:
                validate_path_segment(segment)
            except IdentityError:
                raise PathSecurityError("invalid_subpath") from None
        check_directory_chain(base)
        for segment in segments:
            current = current / segment
            try:
                info = current.lstat()
            except OSError:
                raise PathSecurityError("not_found") from None
            if _info_is_link(info):
                raise PathSecurityError("link")
            if not stat.S_ISDIR(info.st_mode):
                raise PathSecurityError("not_found")
    try:
        resolved, resolved_base = current.resolve(), base.resolve()
    except (OSError, RuntimeError, ValueError):
        raise PathSecurityError("not_found") from None
    if not _within(resolved, resolved_base):
        raise PathSecurityError("outside")
    check_directory_chain(current)
    return resolved


def _match_entry(directory: Path, wanted: str) -> str:
    try:
        with os.scandir(directory) as entries:
            names = [entry.name for entry in entries]
    except OSError:
        raise PathSecurityError("not_found") from None
    key = _fold(wanted)
    matches = [name for name in names if _fold(name) == key]
    if len(matches) == 1:
        return matches[0]
    raise PathSecurityError("ambiguous" if matches else "not_found")


def resolve_concept_file(bundle_dir: Path, concept_id: str) -> Tuple[Path, str]:
    """Plik konceptu w katalogu bundle: (ścieżka, kanoniczny concept_id zgodny z wielkością liter na dysku).

    Odrzuca linki na każdym elemencie, katalog zamiast pliku i niejednoznaczne dopasowanie wielkości liter.
    """
    try:
        validate_concept_id(concept_id)
    except IdentityError:
        raise PathSecurityError("invalid_id") from None
    base = Path(bundle_dir)
    check_directory_chain(base)
    segments = concept_id.split("/")
    current = base
    canonical: List[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        name = _match_entry(current, segment + ".md" if last else segment)
        current = current / name
        try:
            info = current.lstat()
        except OSError:
            raise PathSecurityError("not_found") from None
        if _info_is_link(info):
            raise PathSecurityError("link")
        if last:
            if not stat.S_ISREG(info.st_mode):
                raise PathSecurityError("not_file")
            canonical.append(name[:-3])
        else:
            if not stat.S_ISDIR(info.st_mode):
                raise PathSecurityError("not_found")
            canonical.append(name)
    try:
        resolved, resolved_base = current.resolve(), base.resolve()
    except (OSError, RuntimeError, ValueError):
        raise PathSecurityError("not_found") from None
    if not _within(resolved, resolved_base):
        raise PathSecurityError("outside")
    return resolved, "/".join(canonical)
