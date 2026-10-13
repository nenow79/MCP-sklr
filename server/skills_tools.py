#!/usr/bin/env python3
"""Logika narzędzi MCP dla skills (bez zależności od FastMCP - łatwe testowanie)."""
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from skills_engine.conda_env import resolve_env_python
from skills_engine.content import (
    DEFAULT_READ_CHARS,
    MAX_READ_CHARS,
    MIN_READ_CHARS,
    build_skill_content,
    read_resource_text,
    resolve_resource_path,
)
from skills_engine.policy import decide_confirmation
from skills_engine.registry import SkillRegistry
from skills_engine.runner import (
    DEFAULT_TIMEOUT,
    build_command,
    resolve_script,
    validate_timeout,
)
from skills_engine.trust import TrustStore, compute_skill_hash

MAX_DIAGNOSTICS = 50
SCRIPT_PREVIEW_CHARS = 1500


@dataclass
class PreparedRun:
    """Zwalidowane uruchomienie skryptu: opis zadania dla kolejki."""
    skill: str
    source: str
    base_dir: Path
    script_rel: str
    args: List[str]
    timeout: int
    command: List[str]
    skill_hash: str
    approval_required: bool
    offer_trust: bool
    approval_reason: str
    script_preview: str


def _not_found_error(registry: SkillRegistry, name: str) -> Dict[str, Any]:
    if registry.is_disabled(name):
        return {"success": False, "error": f"Skill '{name}' jest wyłączony w konfiguracji."}
    available = registry.names()
    return {
        "success": False,
        "error": f"Nie znaleziono skilla '{name}'. Dostępne: {available or 'brak'}.",
    }


def _get_with_rescan(registry: SkillRegistry, name: str):
    """Szuka skilla; przy braku robi jeden rescan (skill mógł pojawić się na dysku)."""
    record = registry.get(name)
    if record is None and not registry.is_disabled(name):
        registry.rescan()
        record = registry.get(name)
    return record


def list_skills_impl(registry: SkillRegistry) -> Dict[str, Any]:
    try:
        registry.rescan()
        snap = registry.snapshot()
        diagnostics = [
            {"level": d.level, "path": d.path, "message": d.message}
            for d in snap.diagnostics[:MAX_DIAGNOSTICS]
        ]
        return {
            "success": True,
            "data": {
                "count": len(snap.active),
                "skills": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "source": s.source,
                        "warnings": s.warnings,
                    }
                    for s in snap.active
                ],
                "disabled_count": len(snap.disabled),
                "shadowed_count": snap.shadowed_count,
                "diagnostics": diagnostics,
                "diagnostics_truncated": len(snap.diagnostics) > MAX_DIAGNOSTICS,
                "hint": "Aby użyć skilla, wywołaj activate_skill(name).",
            },
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def activate_skill_impl(registry: SkillRegistry, name: str) -> Dict[str, Any]:
    try:
        record = _get_with_rescan(registry, name)
        if record is None:
            return _not_found_error(registry, name)

        built = build_skill_content(record)
        return {
            "success": True,
            "data": {
                "name": record.name,
                "source": record.source,
                "skill_directory": str(record.base_dir),
                "metadata": record.metadata,
                "compatibility": record.compatibility,
                "warnings": record.warnings + built["warnings"],
                "content": built["content"],
                "resources": built["resources"],
                "resources_truncated": built["resources_truncated"],
            },
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def read_skill_resource_impl(
    registry: SkillRegistry,
    name: str,
    path: str,
    max_chars: int = DEFAULT_READ_CHARS,
) -> Dict[str, Any]:
    try:
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) \
                or not MIN_READ_CHARS <= max_chars <= MAX_READ_CHARS:
            return {
                "success": False,
                "error": f"max_chars musi być liczbą całkowitą od {MIN_READ_CHARS} do {MAX_READ_CHARS}.",
            }
        record = _get_with_rescan(registry, name)
        if record is None:
            return _not_found_error(registry, name)

        target = resolve_resource_path(record.base_dir, path)
        text, truncated, total = read_resource_text(target, max_chars)
        return {
            "success": True,
            "data": {
                "skill": record.name,
                "path": target.relative_to(record.base_dir.resolve()).as_posix(),
                "content": text,
                "truncated": truncated,
                "total_chars": total,
            },
        }
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _script_preview(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(nie można odczytać skryptu)"
    if len(text) > SCRIPT_PREVIEW_CHARS:
        return text[:SCRIPT_PREVIEW_CHARS] + f"\n[... podgląd skrócony, skrypt ma {len(text)} znaków ...]"
    return text


def prepare_script_run(
    registry: SkillRegistry,
    config: Dict[str, Any],
    trust_store: TrustStore,
    name: str,
    script: str,
    args: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    default_python: Optional[str] = None,
) -> Union[PreparedRun, Dict[str, Any]]:
    """Walidacja uruchomienia skryptu. Zwraca PreparedRun albo {"success": False, "error": ...}."""
    try:
        timeout_error = validate_timeout(timeout)
        if timeout_error:
            return {"success": False, "error": timeout_error}

        args = [] if args is None else args
        if not isinstance(args, list) or not all(isinstance(a, str) and "\x00" not in a for a in args):
            return {"success": False, "error": "args musi być listą napisów (bez znaku NUL)."}

        record = _get_with_rescan(registry, name)
        if record is None:
            return _not_found_error(registry, name)

        script_path = resolve_script(record.base_dir, script)

        # Interpreter: .py -> środowisko conda ze skilla albo python serwera.
        python_exe = default_python or sys.executable
        env_name = record.metadata.get("conda-env", "").strip()
        if script_path.suffix.lower() == ".py" and env_name:
            yml = record.base_dir / "assets" / "environment.yml"
            resolution = resolve_env_python(
                env_name, config, environment_yml=yml if yml.is_file() else None
            )
            if resolution.python is None:
                return {"success": False, "error": resolution.error}
            python_exe = str(resolution.python)

        command = build_command(script_path, args, python_exe)

        approved_hash = compute_skill_hash(record.base_dir)
        trusted = trust_store.is_trusted(record.name, approved_hash)
        decision = decide_confirmation(record.source, record.metadata, trusted)

        return PreparedRun(
            skill=record.name,
            source=record.source,
            base_dir=record.base_dir,
            script_rel=script_path.relative_to(record.base_dir.resolve()).as_posix(),
            args=list(args),
            timeout=timeout,
            command=command,
            skill_hash=approved_hash,
            approval_required=decision.required,
            offer_trust=decision.offer_trust,
            approval_reason=decision.reason if decision.required else "",
            script_preview=_script_preview(script_path),
        )
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}
