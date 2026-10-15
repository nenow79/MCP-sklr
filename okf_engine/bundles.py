"""Rejestr bundles OKF: walidacja wpisów configu i ocena dostępności.

Projektowe profile wymagają allow_project=True; serwis produkcyjny jawnie je
włącza. Domyślna funkcja pozostaje ograniczona do centralnych bundles.

Nic tu nie zapisuje configu ani nie nadaje dostępu: wpis jest tylko powiązaniem z zatwierdzonym rootem
(root_path przypięte przy dodaniu). Roots przekazujemy jako obiekty z polami alias/path/mode (np. roots.Root).
Niepoprawny wpis zostaje w configu, ale ma available=False i bezpieczny kod powodu.
"""
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .limits import parse_limits
from .models import IdentityError, validate_bundle_id, validate_path_segment
from .paths import PathSecurityError, locate_bundle_dir

SCOPE_CENTRAL = "central"
SCOPE_PROJECT = "project"
SCOPES = (SCOPE_CENTRAL, SCOPE_PROJECT)
MAX_LABEL = 200
MAX_DESCRIPTION = 1000
MODE_RW = "rw"

_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_BAD_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})  # sterujace, bidi/format, separatory linii
_DIR_REASONS = {"not_found": "bundle_dir_missing", "link": "bundle_dir_link", "outside": "bundle_outside_root"}


class BundleError(ValueError):
    """Niepoprawny wpis bundle; code to nazwa pola (bez treści wartości)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BundleEntry:
    id: str
    label: str
    scope: str
    root: str
    subpath: str
    root_path: str
    description: str = ""
    enabled: bool = False
    profile: str = "all"


@dataclass(frozen=True)
class BundleState:
    id: Optional[str]
    entry: Optional[BundleEntry]
    available: bool
    writable: bool
    reason: str  # "" gdy dostępny; inaczej kod (disabled, root_missing, ...)
    detail: str  # np. nazwa niepoprawnego pola
    path: Optional[Path]


def _text(value: Any, field: str, max_len: int, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise BundleError(field)
    text = value.strip()
    if (not allow_empty and not text) or len(text) > max_len:
        raise BundleError(field)
    if any(unicodedata.category(ch) in _BAD_CATEGORIES for ch in text):
        raise BundleError(field)
    return text


def _subpath(value: Any) -> str:
    if not isinstance(value, str):
        raise BundleError("subpath")
    if value == "":
        return ""
    if "\\" in value or value.startswith("/") or ":" in value:
        raise BundleError("subpath")
    for segment in value.split("/"):
        try:
            validate_path_segment(segment)
        except IdentityError:
            raise BundleError("subpath") from None
    return value


def parse_entry(raw: Any) -> BundleEntry:
    """Waliduje wpis configu (bez dostępu do dysku). Brak enabled = wyłączony."""
    if not isinstance(raw, Mapping):
        raise BundleError("entry")
    try:
        validate_bundle_id(raw.get("id"))
    except IdentityError:
        raise BundleError("id") from None
    scope = raw.get("scope")
    if scope not in SCOPES:
        raise BundleError("scope")
    root = raw.get("root")
    if not isinstance(root, str) or _ALIAS_RE.match(root) is None:
        raise BundleError("root")
    root_path = raw.get("root_path")
    if not isinstance(root_path, str) or not root_path.strip() or not Path(root_path).is_absolute():
        raise BundleError("root_path")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise BundleError("enabled")
    profile = raw.get("profile", "docs" if scope == SCOPE_PROJECT else "all")
    if profile not in (("docs", "legacy", "mixed") if scope == SCOPE_PROJECT else ("all",)):
        raise BundleError("profile")
    if profile in ("legacy", "mixed") and raw.get("subpath", ""):
        raise BundleError("subpath")
    return BundleEntry(
        id=raw["id"],
        label=_text(raw.get("label"), "label", MAX_LABEL, False),
        scope=scope,
        root=root,
        subpath=_subpath(raw.get("subpath", "")),
        root_path=root_path,
        description=_text(raw.get("description", ""), "description", MAX_DESCRIPTION, True),
        enabled=enabled, profile=profile)


def _key(path: Any) -> Path:
    return Path(os.path.normcase(os.path.normpath(str(path))))


def _same_path(first: Any, second: Any) -> bool:
    return _key(first) == _key(second)


def _within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _overlaps_skills(path: Path, skills_dir: Any) -> bool:
    try:
        skills = _key(Path(str(skills_dir)).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return True  # nie da się rozstrzygnąć -> bezpieczniej uznać za kolizję
    target = _key(path)
    return _within(target, skills) or _within(skills, target)


class _Work:
    def __init__(self, bundle_id: Optional[str], entry: Optional[BundleEntry]) -> None:
        self.id = bundle_id
        self.entry = entry
        self.reason = ""
        self.detail = ""
        self.path: Optional[Path] = None
        self.root: Any = None


def _safe_id(raw: Any) -> Optional[str]:
    candidate = raw.get("id") if isinstance(raw, Mapping) else None
    try:
        validate_bundle_id(candidate)
    except IdentityError:
        return None
    return candidate


def evaluate_bundles(knowledge: Any, roots: Sequence[Any], skills_dir: Any = None,
                     *, allow_project: bool = False) -> List[BundleState]:
    """Ocenia wpisy sekcji knowledge względem bieżących roots. Kolejność wyników = kolejność w configu.

    Dostępny (available) = poprawny wpis, enabled, root istnieje z tą samą ścieżką, katalog bez linków
    wewnątrz roota, bez kolizji z innym bundle i z katalogiem skills. Zmiana trybu roota zmienia tylko writable.
    """
    section = knowledge if isinstance(knowledge, Mapping) else {}
    raw_list = section.get("bundles")
    if not isinstance(raw_list, list):
        return []
    limits, limit_errors = parse_limits(section.get("limits", {}))

    works: List[_Work] = []
    for raw in raw_list:
        try:
            entry = parse_entry(raw)
        except BundleError as exc:
            work = _Work(_safe_id(raw), None)
            work.reason, work.detail = "invalid_entry", exc.code
            works.append(work)
        else:
            works.append(_Work(entry.id, entry))

    counts = Counter(work.id for work in works if work.entry is not None)
    for index, work in enumerate(works):
        if work.entry is None:
            continue
        if counts[work.id] > 1:
            work.reason = "duplicate_id"
        elif limits is None:
            work.reason, work.detail = "limits_invalid", ",".join(limit_errors)
        elif index >= limits.max_bundles:
            work.reason = "over_limit"

    by_alias: Dict[str, Any] = {str(root.alias).lower(): root for root in roots}
    for work in works:
        if work.entry is None or work.reason:
            continue
        entry = work.entry
        if entry.scope != SCOPE_CENTRAL and not allow_project:
            work.reason = "scope_project_unsupported"
            continue
        if entry.scope == SCOPE_PROJECT and entry.profile == "docs" and not entry.subpath:
            work.reason = "profile_requires_subpath"
            continue
        root = by_alias.get(entry.root.lower())
        if root is None:
            work.reason = "root_missing"
            continue
        if not _same_path(root.path, entry.root_path):
            work.reason = "root_changed"
            continue
        work.root = root
        try:
            work.path = locate_bundle_dir(entry.root_path, entry.subpath)
        except PathSecurityError as exc:
            work.reason = _DIR_REASONS.get(exc.code, "bundle_dir_missing")
            continue
        if skills_dir is not None and _overlaps_skills(work.path, skills_dir):
            work.reason = "overlaps_skills"

    candidates = [work for work in works if work.path is not None and not work.reason]
    for i, first in enumerate(candidates):
        for second in candidates[i + 1:]:
            a, b = _key(first.path), _key(second.path)
            if a == b:
                code = "duplicate_directory"
            elif _within(a, b) or _within(b, a):
                code = "overlapping_directory"
            else:
                continue
            first.reason = first.reason or code
            second.reason = second.reason or code

    states: List[BundleState] = []
    for work in works:
        reason = work.reason
        if not reason and work.entry is not None and not work.entry.enabled:
            reason = "disabled"
        available = not reason
        owners = [root for root in roots if work.path is not None
                  and _within(_key(work.path), _key(root.path))]
        owner = max(owners, key=lambda root: len(_key(root.path).parts), default=None)
        writable = available and owner is not None and owner.mode == MODE_RW
        states.append(BundleState(work.id, work.entry, available, writable, reason, work.detail, work.path))
    return states
