#!/usr/bin/env python3
"""Wykrywanie interpretera środowiska conda (Miniforge) bez uruchamiania condy i bez shella."""
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

_ENV_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass
class EnvResolution:
    python: Optional[Path]
    error: Optional[str] = None


def env_python_path(env_dir: Path) -> Path:
    return env_dir / "python.exe" if os.name == "nt" else env_dir / "bin" / "python"


def detect_conda_base(config: Dict[str, Any], environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """Katalog bazowy condy: config.conda.base, potem CONDA_EXE, potem conda z PATH."""
    configured = (config.get("conda") or {}).get("base")
    if configured:
        return Path(str(configured)).expanduser()

    env = os.environ if environ is None else environ
    conda_exe = env.get("CONDA_EXE")
    if conda_exe:
        return Path(conda_exe).parent.parent

    path = env.get("PATH")
    if path:
        found = shutil.which("conda", path=path)
        if found:
            # ...\condabin\conda.bat lub ...\Scripts\conda.exe -> katalog bazowy dwa poziomy wyżej
            return Path(found).resolve().parent.parent
    return None


def _candidate_dirs(
    env_name: str, config: Dict[str, Any], home: Path, environ: Optional[Mapping[str, str]]
) -> List[Path]:
    candidates: List[Path] = []
    base = detect_conda_base(config, environ)
    if base is not None:
        if env_name == "base":
            candidates.append(base)
        candidates.append(base / "envs" / env_name)
    candidates.append(home / ".conda" / "envs" / env_name)

    environments_file = home / ".conda" / "environments.txt"
    try:
        lines = environments_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        line = line.strip()
        if line and Path(line).name == env_name:
            candidates.append(Path(line))
    return candidates


def _not_found_message(env_name: str, environment_yml: Optional[Path]) -> str:
    lines = [
        f"Środowisko conda '{env_name}' nie zostało znalezione.",
        "Uruchom w swoim terminalu i napisz, gdy skończy się instalacja:",
        f"  conda create -n {env_name} python",
    ]
    if environment_yml is not None:
        lines.append("lub (skill zawiera definicję środowiska):")
        lines.append(f'  conda env create -n {env_name} -f "{environment_yml}"')
    lines.append(
        "Jeśli środowisko leży w niestandardowym miejscu, wpisz ścieżkę do python.exe "
        f"w config.json: conda.envs.{env_name}."
    )
    return "\n".join(lines)


def resolve_env_python(
    env_name: str,
    config: Dict[str, Any],
    home: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    environment_yml: Optional[Path] = None,
) -> EnvResolution:
    """Znajduje python środowiska conda. Niczego nie instaluje; przy braku zwraca komendę dla użytkownika."""
    if not _ENV_NAME.match(env_name or ""):
        return EnvResolution(None, f"Niepoprawna nazwa środowiska conda: '{env_name}'.")

    # 1) jawna ścieżka w config.conda.envs (plik python lub katalog środowiska)
    explicit = ((config.get("conda") or {}).get("envs") or {}).get(env_name)
    if explicit:
        explicit_path = Path(str(explicit)).expanduser()
        if explicit_path.is_file():
            return EnvResolution(explicit_path)
        if explicit_path.is_dir() and env_python_path(explicit_path).is_file():
            return EnvResolution(env_python_path(explicit_path))

    # 2) wykrywanie
    user_home = home if home is not None else Path.home()
    for env_dir in _candidate_dirs(env_name, config, user_home, environ):
        python = env_python_path(env_dir)
        if python.is_file():
            return EnvResolution(python)

    return EnvResolution(None, _not_found_message(env_name, environment_yml))
