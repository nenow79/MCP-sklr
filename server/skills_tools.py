#!/usr/bin/env python3
"""Logika narzędzi MCP dla skills (bez zależności od FastMCP - łatwe testowanie)."""
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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
    APPROVAL_TIMEOUT,
    DEFAULT_TIMEOUT,
    build_command,
    clean_env,
    resolve_script,
    run_command,
    validate_timeout,
)
from skills_engine.trust import TrustStore, compute_skill_hash

MAX_DIAGNOSTICS = 50
SCRIPT_PREVIEW_CHARS = 1500

# approver(title, message, allow_trust, timeout_s) -> "allow" | "trust" | "deny" | "timeout"
Approver = Callable[[str, str, bool, int], str]


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
                "disabled": snap.disabled,
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
    skill: str,
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
        record = _get_with_rescan(registry, skill)
        if record is None:
            return _not_found_error(registry, skill)

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


def _approval_message(record, script_rel: str, args: List[str], command: List[str],
                      skill_hash: str, reason: str, preview: str) -> str:
    return (
        f"Skill:      {record.name} ({record.source})\n"
        f"Skrypt:     {script_rel}\n"
        f"Argumenty:  {args if args else '(brak)'}\n"
        f"Interpreter: {command[0]}\n"
        f"Katalog:    {record.base_dir}\n"
        f"Hash:       {skill_hash[:16]}...\n\n"
        f"Powód pytania: {reason}\n\n"
        f"--- Podgląd skryptu ---\n{preview}"
    )


def run_skill_script_impl(
    registry: SkillRegistry,
    config: Dict[str, Any],
    trust_store: TrustStore,
    approver: Approver,
    skill: str,
    script: str,
    args: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    default_python: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        timeout_error = validate_timeout(timeout)
        if timeout_error:
            return {"success": False, "error": timeout_error}

        args = [] if args is None else args
        if not isinstance(args, list) or not all(isinstance(a, str) and "\x00" not in a for a in args):
            return {"success": False, "error": "args musi być listą napisów (bez znaku NUL)."}

        record = _get_with_rescan(registry, skill)
        if record is None:
            return _not_found_error(registry, skill)

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

        if decision.required:
            script_rel = script_path.relative_to(record.base_dir.resolve()).as_posix()
            title = "Ufaj temu skillowi?" if decision.offer_trust else "Uruchomienie skryptu skilla"
            message = _approval_message(
                record, script_rel, args, command, approved_hash, decision.reason,
                _script_preview(script_path),
            )
            answer = approver(title, message, decision.offer_trust, APPROVAL_TIMEOUT)
            if answer == "timeout":
                return {"success": False, "error": "Brak odpowiedzi użytkownika w czasie oczekiwania; skrypt nie został uruchomiony."}
            if answer not in ("allow", "trust"):
                return {"success": False, "error": "Użytkownik zablokował uruchomienie skryptu."}

            # Skill mógł się zmienić w trakcie oczekiwania na decyzję.
            if compute_skill_hash(record.base_dir) != approved_hash:
                return {
                    "success": False,
                    "error": "Skill zmienił się podczas oczekiwania na zatwierdzenie; skrypt nie został uruchomiony. Ponów wywołanie.",
                }
            if answer == "trust" and decision.offer_trust:
                trust_store.trust(record.name, approved_hash)

        result = run_command(command, record.base_dir, timeout, clean_env())
        if not result.get("started"):
            return {"success": False, "error": result["error"]}

        data = {
            "skill": record.name,
            "script": script_path.relative_to(record.base_dir.resolve()).as_posix(),
            "exit_code": result["exit_code"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "stdout_truncated": result["stdout_truncated"],
            "stderr_truncated": result["stderr_truncated"],
            "duration_s": result["duration_s"],
            "timed_out": result["timed_out"],
        }
        if result["timed_out"]:
            return {
                "success": False,
                "error": f"Skrypt przerwany po {timeout} s (timeout); proces został zakończony. Zwrócono częściowy output.",
                "data": data,
            }
        if result["exit_code"] != 0:
            return {
                "success": False,
                "error": f"Skrypt zakończył się kodem {result['exit_code']}.",
                "data": data,
            }
        return {"success": True, "data": data}
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}
