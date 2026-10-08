#!/usr/bin/env python3
"""Parser SKILL.md: frontmatter YAML + lenientna walidacja wg agentskills.io."""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from skills_engine.models import Diagnostic, ParseResult, SkillRecord

NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500

_TOP_LEVEL_SCALAR = re.compile(r"^([A-Za-z0-9_-]+):[ \t]+(.+)$")


def _split_frontmatter(text: str) -> Optional[Tuple[str, str]]:
    """Zwraca (blok_yaml, body) albo None, gdy brak poprawnego frontmattera."""
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1:]).strip()
    return None


split_frontmatter = _split_frontmatter  # publiczny alias dla innych modułów


def _quote_colon_values(yaml_block: str) -> str:
    """Fallback: cytuje wartości skalarne zawierające ': ' (typowy błąd skills z innych klientów)."""
    fixed: List[str] = []
    for line in yaml_block.splitlines():
        match = _TOP_LEVEL_SCALAR.match(line)
        if match:
            key, value = match.group(1), match.group(2).strip()
            if (
                ": " in value
                and value[0] not in "\"'|>[{"
            ):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                line = f'{key}: "{escaped}"'
        fixed.append(line)
    return "\n".join(fixed)


def _load_yaml(yaml_block: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Zwraca (dane, użyto_fallbacku). Dane None = nieparsowalny YAML."""
    for attempt, block in enumerate((yaml_block, _quote_colon_values(yaml_block))):
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError:
            continue
        if isinstance(data, dict):
            return data, attempt == 1
        return None, False
    return None, False


def _is_valid_name(name: str) -> bool:
    if not name or name.startswith("-") or name.endswith("-") or "--" in name:
        return False
    return all(ch == "-" or (ch.isalnum() and ch == ch.lower()) for ch in name)


def _stringify_metadata(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def parse_skill_file(skill_md: Path, source: str) -> ParseResult:
    """Parsuje SKILL.md. Zwraca record (lub None przy błędzie krytycznym) i diagnostykę."""
    path_str = str(skill_md)
    diagnostics: List[Diagnostic] = []

    def error(message: str) -> ParseResult:
        diagnostics.append(Diagnostic("error", path_str, message))
        return ParseResult(None, diagnostics)

    try:
        text = skill_md.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return error(f"Nie można odczytać pliku: {exc}")

    split = _split_frontmatter(text)
    if split is None:
        return error("Brak frontmattera YAML (--- ... ---) na początku pliku.")

    data, used_fallback = _load_yaml(split[0])
    if data is None:
        return error("Frontmatter nie jest poprawnym YAML-em (słownikiem), skill pominięty.")
    if used_fallback:
        diagnostics.append(Diagnostic(
            "warning", path_str,
            "Niepoprawny YAML naprawiony automatycznie (wartości z dwukropkiem ujęte w cudzysłów).",
        ))

    description = data.get("description")
    description = "" if description is None else str(description).strip()
    if not description:
        return error("Brak pola 'description' lub jest puste, skill pominięty.")
    if len(description) > DESCRIPTION_MAX:
        diagnostics.append(Diagnostic(
            "warning", path_str, f"description ma {len(description)} znaków (limit {DESCRIPTION_MAX})."
        ))

    folder_name = skill_md.parent.name
    raw_name = data.get("name")
    name = "" if raw_name is None else str(raw_name).strip()
    if not name:
        name = folder_name
        diagnostics.append(Diagnostic(
            "warning", path_str, f"Brak pola 'name', użyto nazwy folderu '{folder_name}'."
        ))
    else:
        if len(name) > NAME_MAX:
            diagnostics.append(Diagnostic(
                "warning", path_str, f"name ma {len(name)} znaków (limit {NAME_MAX})."
            ))
        if not _is_valid_name(name):
            diagnostics.append(Diagnostic(
                "warning", path_str,
                f"name '{name}' łamie reguły (małe litery/cyfry/myślniki, bez myślnika na krańcach i bez '--').",
            ))
        if name != folder_name:
            diagnostics.append(Diagnostic(
                "warning", path_str, f"name '{name}' różni się od nazwy folderu '{folder_name}'."
            ))

    compatibility = data.get("compatibility")
    compatibility = "" if compatibility is None else str(compatibility).strip()
    if len(compatibility) > COMPATIBILITY_MAX:
        diagnostics.append(Diagnostic(
            "warning", path_str, f"compatibility ma {len(compatibility)} znaków (limit {COMPATIBILITY_MAX})."
        ))

    license_value = data.get("license")
    record = SkillRecord(
        name=name,
        description=description,
        location=skill_md.resolve(),
        source=source,
        metadata=_stringify_metadata(data.get("metadata")),
        compatibility=compatibility,
        license="" if license_value is None else str(license_value).strip(),
        warnings=[d.message for d in diagnostics if d.level == "warning"],
    )
    return ParseResult(record, diagnostics)
