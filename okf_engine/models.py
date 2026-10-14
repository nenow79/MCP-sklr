"""Modele danych silnika OKF: tożsamość dokumentu, limity parsera, wynik parsowania."""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

MASK = "***"
BLOCK_MASK = "[ZAMASKOWANO]"

ERR_INVALID_OKF = "INVALID_OKF"
ERR_UNSUPPORTED_PROFILE = "UNSUPPORTED_PROFILE"
ERR_SAFETY_LIMIT = "SAFETY_LIMIT"

_BUNDLE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_RESERVED_NAMES = frozenset({"index", "log"})
_BAD_CHARS = frozenset('\\:*?"<>|')
MAX_CONCEPT_ID_LEN = 512


class IdentityError(ValueError):
    """Niepoprawna tożsamość dokumentu; argument to bezpieczny kod powodu."""


def validate_bundle_id(bundle_id: Any) -> None:
    if not isinstance(bundle_id, str) or _BUNDLE_RE.fullmatch(bundle_id) is None:
        raise IdentityError("bundle_id")


def validate_concept_id(concept_id: Any) -> None:
    """Względna ścieżka POSIX bez .md; reguły nazw Windows rozszerza C1b (paths.py)."""
    if not isinstance(concept_id, str):
        raise IdentityError("concept_id_type")
    if not concept_id:
        raise IdentityError("concept_id_empty")
    if len(concept_id) > MAX_CONCEPT_ID_LEN:
        raise IdentityError("concept_id_too_long")
    if any(ord(ch) < 32 or ord(ch) == 127 or ch in _BAD_CHARS for ch in concept_id):
        raise IdentityError("concept_id_chars")
    if concept_id.lower().endswith(".md"):
        raise IdentityError("concept_id_suffix_md")
    for segment in concept_id.split("/"):
        if segment in ("", ".", "..") or segment != segment.strip() or segment.endswith("."):
            raise IdentityError("concept_id_segment")
        if segment.casefold() in _RESERVED_NAMES:
            raise IdentityError("concept_id_reserved")


@dataclass(frozen=True)
class DocumentIdentity:
    bundle_id: str
    concept_id: str

    def __post_init__(self) -> None:
        validate_bundle_id(self.bundle_id)
        validate_concept_id(self.concept_id)


_LIMIT_BOUNDS = {
    "max_document_bytes": (1024, 1048576),
    "max_frontmatter_bytes": (256, 65536),
    "max_yaml_nodes": (1, 10000),
    "max_yaml_depth": (1, 32),
    "max_alias_references": (0, 32),
}


@dataclass(frozen=True)
class ParseLimits:
    """Limity parsera; domyślne wartości = profil v1 z docs/specs/okf-support.md (sekcja 3.1)."""
    max_document_bytes: int = 1048576
    max_frontmatter_bytes: int = 65536
    max_yaml_nodes: int = 10000
    max_yaml_depth: int = 32
    max_alias_references: int = 32

    def __post_init__(self) -> None:
        for name, (low, high) in _LIMIT_BOUNDS.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"invalid limit: {name}")
        if self.max_frontmatter_bytes > self.max_document_bytes:
            raise ValueError("invalid limit: max_frontmatter_bytes")


@dataclass(frozen=True)
class Issue:
    """Bezpieczny opis problemu: bez fragmentów treści i surowych komunikatów YAML."""
    kind: str  # ERR_INVALID_OKF / ERR_UNSUPPORTED_PROFILE / ERR_SAFETY_LIMIT
    reason: str
    line: Optional[int] = None


@dataclass(frozen=True)
class ParsedDocument:
    """PRYWATNY wynik parsowania: zawiera surowe dane. Nie przekazywać do MCP/telemetrii/SQLite."""
    identity: DocumentIdentity
    size_bytes: int
    errors: Tuple[Issue, ...] = ()
    warnings: Tuple[str, ...] = ()
    metadata: Optional[Dict[str, Any]] = field(default=None, repr=False)
    body: str = field(default="", repr=False)
    body_start_line: int = 1
    has_bom: bool = False
    eol: str = "none"

    @property
    def ok(self) -> bool:
        return not self.errors
