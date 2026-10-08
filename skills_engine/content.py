#!/usr/bin/env python3
"""Treść skilla przy aktywacji: body bez frontmattera, lista zasobów, bezpieczny odczyt zasobów."""
import os
from html import escape
from pathlib import Path
from typing import Dict, List, Tuple

from skills_engine.models import SkillRecord
from skills_engine.parser import split_frontmatter
from skills_engine.scanner import IGNORED_DIRS, SKILL_FILE

MAX_RESOURCES = 100
MAX_RESOURCE_BYTES = 1_000_000
DEFAULT_READ_CHARS = 12_000
MIN_READ_CHARS = 1_000
MAX_READ_CHARS = 80_000
RECOMMENDED_MAX_LINES = 500


def read_body(record: SkillRecord) -> str:
    """Czyta SKILL.md z dysku (zawsze świeżo) i zwraca treść bez frontmattera."""
    text = record.location.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    split = split_frontmatter(text)
    return split[1] if split else text.strip()


def list_resources(base_dir: Path, limit: int = MAX_RESOURCES) -> Tuple[List[str], bool]:
    """Zwraca (względne ścieżki w stylu posix, czy_obcięto). Pomija SKILL.md i katalogi śmieciowe."""
    files: List[str] = []
    truncated = False
    for current, dirs, names in os.walk(base_dir):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        for name in sorted(names):
            relative = (Path(current) / name).relative_to(base_dir).as_posix()
            if relative == SKILL_FILE:
                continue
            if len(files) >= limit:
                truncated = True
                break
            files.append(relative)
        if truncated:
            break
    return files, truncated


def build_skill_content(record: SkillRecord) -> Dict[str, object]:
    """Buduje blok <skill_content> zgodnie z zaleceniami agentskills.io."""
    body = read_body(record)
    resources, truncated = list_resources(record.base_dir)

    lines = [f'<skill_content name="{escape(record.name, quote=True)}">', body, ""]
    lines.append(f"Skill directory: {record.base_dir}")
    lines.append("Relative paths in this skill are relative to the skill directory.")
    lines.append("")
    lines.append("<skill_resources>")
    lines.extend(f"  <file>{escape(path)}</file>" for path in resources)
    if truncated:
        lines.append(f"  <!-- lista obcięta do {MAX_RESOURCES} plików -->")
    lines.append("</skill_resources>")
    lines.append("</skill_content>")

    warnings: List[str] = []
    body_lines = body.count("\n") + 1 if body else 0
    if body_lines > RECOMMENDED_MAX_LINES:
        warnings.append(
            f"SKILL.md ma {body_lines} linii (zalecane maks. {RECOMMENDED_MAX_LINES}); "
            "rozważ przeniesienie szczegółów do references/."
        )
    return {
        "content": "\n".join(lines),
        "resources": resources,
        "resources_truncated": truncated,
        "warnings": warnings,
    }


def resolve_resource_path(base_dir: Path, relative: str) -> Path:
    """Zamienia względną ścieżkę na plik wewnątrz katalogu skilla.

    Rzuca ValueError przy próbie wyjścia poza katalog (.., ścieżka absolutna, symlink na zewnątrz),
    FileNotFoundError gdy plik nie istnieje.
    """
    if not relative or not relative.strip():
        raise ValueError("Pusta ścieżka zasobu.")
    candidate = Path(relative)
    if candidate.anchor:
        raise ValueError("Ścieżka zasobu musi być względna względem katalogu skilla.")
    base = base_dir.resolve()
    target = (base / candidate).resolve()
    if not target.is_relative_to(base):
        raise ValueError("Próba wyjścia poza katalog skilla! (Path Traversal)")
    if not target.is_file():
        raise FileNotFoundError(f"Nie znaleziono pliku zasobu: {relative}")
    return target


def read_resource_text(path: Path, max_chars: int = DEFAULT_READ_CHARS) -> Tuple[str, bool, int]:
    """Czyta zasób tekstowy. Zwraca (tekst, czy_obcięto, całkowita_liczba_znaków)."""
    if path.stat().st_size > MAX_RESOURCE_BYTES:
        raise ValueError(f"Plik jest zbyt duży ({path.stat().st_size} B, limit {MAX_RESOURCE_BYTES} B).")
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        raise ValueError("Plik wygląda na binarny - odczyt zablokowany.")

    text = None
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Nie udało się odczytać pliku w obsługiwanym kodowaniu.")

    text = text.replace("\r\n", "\n")
    total = len(text)
    if total <= max_chars:
        return text, False, total
    half = max_chars // 2
    shortened = (
        text[:half]
        + f"\n\n[... skrócono: pokazano {max_chars} z {total} znaków ...]\n\n"
        + text[-half:]
    )
    return shortened, True, total
