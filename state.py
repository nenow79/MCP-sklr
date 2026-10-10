#!/usr/bin/env python3
"""Trwała konfiguracja serwera (config.json obok kodu)."""
import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "server": {
        "host": "0.0.0.0",
        "port": 8101,
        "cors_allow_origins": ["*"],
    },
    "workspace": {
        "path": None,
    },
    "skills": {
        "central_dir": "~/.agents/skills",
        "disabled": [],
        "trust": {},
    },
    "conda": {
        "base": None,
        "envs": {},
    },
    "tasks": {
        "max_parallel": 2,
        "max_queued": 50,
        "max_wait_s": 8,
        "retention_days": 30,
        "retention_max": 500,
        "approval_timeout_s": 120,
        "approval_expiry_min": 60,
        "approval_ui": "panel",
    },
    "panel": {
        "enabled": True,
        "port": 8102,
        "open_browser": True,
    },
    "references": {},
    "external_mcp": {},
}


def _merge(defaults: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
    """Nakłada wczytane wartości na domyślne (rekurencyjnie dla słowników).

    Klucze spoza domyślnych są zachowywane. Słowniki będące wartościami
    użytkownika (np. references, trust) trafiają do wyniku w całości.
    """
    result = copy.deepcopy(defaults)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict) and result[key]:
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Wczytuje konfigurację. Brak lub uszkodzony plik = wartości domyślne."""
    config_path = Path(path) if path else CONFIG_FILE
    if not config_path.is_file():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with config_path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(loaded, dict):
        return copy.deepcopy(DEFAULT_CONFIG)
    return _merge(DEFAULT_CONFIG, loaded)


def save_config(config: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Zapisuje konfigurację atomowo (plik tymczasowy + podmiana)."""
    config_path = Path(path) if path else CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_name(config_path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, config_path)


def resolve_central_skills_dir(config: Dict[str, Any]) -> Path:
    """Zwraca rozwinięty katalog centralny skills (z obsługą ~)."""
    return Path(config["skills"]["central_dir"]).expanduser()


def resolve_workspace_path(config: Dict[str, Any]) -> Optional[Path]:
    """Zwraca katalog workspace z configu albo None (wybór folderu wejdzie z GUI w fazie 4)."""
    raw = config.get("workspace", {}).get("path")
    if not raw:
        return None
    return Path(str(raw)).expanduser()
