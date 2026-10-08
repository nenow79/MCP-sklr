#!/usr/bin/env python3
"""Skaner katalogów skills (centralny + projektowy)."""
import os
from pathlib import Path
from typing import List, Optional

from skills_engine.models import (
    SOURCE_CENTRAL,
    SOURCE_PROJECT,
    Diagnostic,
    ScanResult,
    SkillRecord,
)
from skills_engine.parser import parse_skill_file

SKILL_FILE = "SKILL.md"
IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
MAX_DEPTH = 4
MAX_DIRS = 2000


def project_skills_dir(project_root: Path) -> Path:
    return project_root / ".agents" / "skills"


def _scan_root(root: Path, source: str, diagnostics: List[Diagnostic]) -> List[SkillRecord]:
    """Zwraca skills znalezione w jednym katalogu (deterministyczna kolejność alfabetyczna)."""
    found: List[SkillRecord] = []
    if not root.is_dir():
        return found

    visited = 0
    for current, dirs, files in os.walk(root):
        visited += 1
        if visited > MAX_DIRS:
            diagnostics.append(Diagnostic(
                "warning", str(root), f"Przerwano skan: przekroczono limit {MAX_DIRS} katalogów."
            ))
            break

        current_path = Path(current)
        relative = current_path.relative_to(root)
        depth = len(relative.parts)
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)

        if SKILL_FILE in files and depth > 0:
            result = parse_skill_file(current_path / SKILL_FILE, source)
            diagnostics.extend(result.diagnostics)
            if result.record is not None:
                found.append(result.record)
            dirs[:] = []  # nie schodzimy do wnętrza skilla (scripts/, references/...)
            continue

        if depth >= MAX_DEPTH:
            dirs[:] = []
    return found


def _register(result: ScanResult, records: List[SkillRecord]) -> None:
    """Dodaje skills danego zakresu; w obrębie zakresu wygrywa pierwszy znaleziony."""
    for record in records:
        existing = result.skills.get(record.name)
        if existing is None:
            result.skills[record.name] = record
            continue
        result.diagnostics.append(Diagnostic(
            "warning", str(record.location),
            f"Kolizja nazwy '{record.name}' w obrębie tego samego zakresu; "
            f"użyto {existing.location}.",
        ))
        result.shadowed.append(record)


def scan_skills(central_dir: Optional[Path], project_root: Optional[Path] = None) -> ScanResult:
    """Skanuje katalog centralny i projektowy. Skill projektowy przesłania centralny o tej samej nazwie."""
    result = ScanResult()

    central_records = _scan_root(central_dir, SOURCE_CENTRAL, result.diagnostics) if central_dir else []
    project_records: List[SkillRecord] = []
    if project_root is not None:
        project_records = _scan_root(project_skills_dir(project_root), SOURCE_PROJECT, result.diagnostics)

    # Najpierw centralne, potem projektowe z nadpisaniem.
    _register(result, central_records)
    project_scope = ScanResult()
    _register(project_scope, project_records)
    result.diagnostics.extend(project_scope.diagnostics)
    result.shadowed.extend(project_scope.shadowed)

    for name, record in project_scope.skills.items():
        previous = result.skills.get(name)
        if previous is not None and previous.source == SOURCE_CENTRAL:
            result.shadowed.append(previous)
            result.diagnostics.append(Diagnostic(
                "warning", str(record.location),
                f"Skill projektowy '{name}' przesłania skill centralny {previous.location}.",
            ))
        result.skills[name] = record
    return result
