#!/usr/bin/env python3
"""Zaufanie do skills: hash SKILL.md + scripts/ i trwały rejestr zaufanych hashy."""
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SKILL_FILE = "SKILL.md"
SCRIPTS_DIR = "scripts"
_IGNORED_DIRS = {"__pycache__"}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}


def _normalized(data: bytes) -> bytes:
    """Normalizuje końce linii w plikach tekstowych (CRLF == LF dla hasha)."""
    if b"\x00" in data[:8192]:
        return data
    return data.replace(b"\r\n", b"\n")


def _hashed_files(base: Path) -> List[Path]:
    files: List[Path] = []
    skill_md = base / SKILL_FILE
    if skill_md.is_file():
        files.append(skill_md)
    scripts = base / SCRIPTS_DIR
    if scripts.is_dir():
        for current, dirs, names in os.walk(scripts):
            dirs[:] = sorted(d for d in dirs if d not in _IGNORED_DIRS)
            for name in sorted(names):
                path = Path(current) / name
                if path.suffix.lower() in _IGNORED_SUFFIXES:
                    continue
                files.append(path)
    return sorted(files, key=lambda p: p.relative_to(base).as_posix())


def compute_skill_hash(base_dir: Path) -> str:
    """SHA-256 po SKILL.md i wszystkich plikach scripts/ (ścieżki + zawartość, stała kolejność)."""
    base = base_dir.resolve()
    digest = hashlib.sha256()
    for path in _hashed_files(base):
        relative = path.relative_to(base).as_posix()
        content = _normalized(path.read_bytes())
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


class TrustStore:
    """Rejestr zaufanych hashy w config['skills']['trust'] (nazwa -> {hash, trusted_at})."""

    def __init__(self, config: Dict[str, Any], save: Optional[Callable[[], None]] = None) -> None:
        self._config = config
        self._save = save

    def _entries(self) -> Dict[str, Any]:
        return self._config.setdefault("skills", {}).setdefault("trust", {})

    def is_trusted(self, name: str, skill_hash: str) -> bool:
        entry = self._entries().get(name)
        return isinstance(entry, dict) and entry.get("hash") == skill_hash

    def trust(self, name: str, skill_hash: str) -> None:
        self._entries()[name] = {
            "hash": skill_hash,
            "trusted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if self._save:
            self._save()

    def revoke(self, name: str) -> bool:
        removed = self._entries().pop(name, None) is not None
        if removed and self._save:
            self._save()
        return removed
