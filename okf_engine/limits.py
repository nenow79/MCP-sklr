"""Limity wiedzy OKF: walidacja sekcji knowledge.limits (kontrakt, sekcja 3.1).

Limity można obniżyć, ale nie wyłączyć ani podwyższyć ponad profil v1. Niepoprawna sekcja daje
listę nazw pól (bezpieczna diagnostyka), a nie profil bez ograniczeń.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .models import ParseLimits

MAX_SECTIONS_PER_DOCUMENT = 2000  # stały limit v1, nie jest konfigurowalny

LIMIT_BOUNDS: Dict[str, Tuple[int, int]] = {
    "max_bundles": (1, 20),
    "max_documents_per_bundle": (1, 5000),
    "max_depth": (1, 20),
    "max_document_bytes": (1024, 1048576),
    "max_frontmatter_bytes": (256, 65536),
    "max_yaml_nodes": (1, 10000),
    "max_yaml_depth": (1, 32),
    "max_alias_references": (0, 32),
    "max_index_text_bytes": (1048576, 67108864),
    "refresh_after_s": (1, 3600),
    "scan_budget_s": (1, 6),
    "search_budget_s": (1, 2),
}


def _valid(name: str, value: Any) -> bool:
    low, high = LIMIT_BOUNDS[name]
    return not isinstance(value, bool) and isinstance(value, int) and low <= value <= high


@dataclass(frozen=True)
class KnowledgeLimits:
    max_bundles: int = 20
    max_documents_per_bundle: int = 5000
    max_depth: int = 20
    max_document_bytes: int = 1048576
    max_frontmatter_bytes: int = 65536
    max_yaml_nodes: int = 10000
    max_yaml_depth: int = 32
    max_alias_references: int = 32
    max_index_text_bytes: int = 67108864
    refresh_after_s: int = 30
    scan_budget_s: int = 6
    search_budget_s: int = 2

    def __post_init__(self) -> None:
        for name in LIMIT_BOUNDS:
            if not _valid(name, getattr(self, name)):
                raise ValueError(f"invalid limit: {name}")
        if self.max_frontmatter_bytes > self.max_document_bytes:
            raise ValueError("invalid limit: max_frontmatter_bytes")

    def to_parse_limits(self) -> ParseLimits:
        return ParseLimits(
            max_document_bytes=self.max_document_bytes,
            max_frontmatter_bytes=self.max_frontmatter_bytes,
            max_yaml_nodes=self.max_yaml_nodes,
            max_yaml_depth=self.max_yaml_depth,
            max_alias_references=self.max_alias_references)


def parse_limits(section: Any) -> Tuple[Optional[KnowledgeLimits], Tuple[str, ...]]:
    """Zwraca (limity, błędy). Brakujące pola = wartości profilu v1; nieznane klucze są ignorowane.

    Przy błędach limity to None, a błędy to nazwy pól (albo 'limits' dla sekcji nie będącej obiektem).
    """
    if not isinstance(section, Mapping):
        return None, ("limits",)
    bad: List[str] = [name for name in LIMIT_BOUNDS if name in section and not _valid(name, section[name])]
    if bad:
        return None, tuple(bad)
    values = {name: section[name] for name in LIMIT_BOUNDS if name in section}
    try:
        return KnowledgeLimits(**values), ()
    except ValueError:
        return None, ("max_frontmatter_bytes",)
