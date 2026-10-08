#!/usr/bin/env python3
"""Modele danych silnika skills."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

SOURCE_CENTRAL = "central"
SOURCE_PROJECT = "project"


@dataclass
class Diagnostic:
    """Pojedyncza uwaga z parsowania lub skanowania."""
    level: str  # "warning" | "error"
    path: str
    message: str


@dataclass
class SkillRecord:
    """Skill wykryty na dysku (bez treści SKILL.md - ta jest czytana przy aktywacji)."""
    name: str
    description: str
    location: Path  # absolutna ścieżka do SKILL.md
    source: str  # SOURCE_CENTRAL | SOURCE_PROJECT
    metadata: Dict[str, str] = field(default_factory=dict)
    compatibility: str = ""
    license: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def base_dir(self) -> Path:
        return self.location.parent


@dataclass
class ParseResult:
    """Wynik parsowania jednego SKILL.md: record albo None + diagnostyka."""
    record: "SkillRecord | None"
    diagnostics: List[Diagnostic] = field(default_factory=list)


@dataclass
class ScanResult:
    """Wynik skanu: aktywne skills (po rozstrzygnięciu kolizji), przesłonięte i diagnostyka."""
    skills: Dict[str, SkillRecord] = field(default_factory=dict)
    shadowed: List[SkillRecord] = field(default_factory=list)
    diagnostics: List[Diagnostic] = field(default_factory=list)
