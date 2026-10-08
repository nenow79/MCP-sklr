#!/usr/bin/env python3
"""Vibecoder Skills MCP - punkt wejścia (faza 3: skills + uruchamianie skryptów)."""
from typing import Any, Dict, List, Optional

import uvicorn
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

import gui_simple
from server import skills_tools
from skills_engine import SkillRegistry
from skills_engine.content import DEFAULT_READ_CHARS
from skills_engine.runner import DEFAULT_TIMEOUT
from skills_engine.trust import TrustStore
from state import load_config, resolve_central_skills_dir, resolve_workspace_path, save_config
from telemetry import track_mcp_tool

CONFIG = load_config()

# Do fazy 4 (GUI) workspace pochodzi z config.json (workspace.path); null = tylko katalog centralny.
PROJECT_ROOT = resolve_workspace_path(CONFIG)
WORKSPACE_NAME = PROJECT_ROOT.name if PROJECT_ROOT else ""

REGISTRY = SkillRegistry(
    central_dir=resolve_central_skills_dir(CONFIG),
    project_root=PROJECT_ROOT,
    disabled=CONFIG["skills"]["disabled"],
)
TRUST_STORE = TrustStore(CONFIG, save=lambda: save_config(CONFIG))

mcp = FastMCP("Vibecoder_Skills")


def register_tool(function):
    """Rejestruje narzędzie MCP wraz z lekką telemetrią użycia."""
    return mcp.tool(track_mcp_tool(function, lambda: WORKSPACE_NAME))


@register_tool
def ping() -> Dict[str, Any]:
    """Sprawdza, czy serwer MCP działa. Zwraca nazwę serwera i port."""
    return {
        "success": True,
        "data": {
            "server": "Vibecoder_Skills",
            "port": CONFIG["server"]["port"],
        },
    }


@register_tool
def list_skills() -> Dict[str, Any]:
    """
    Zwraca katalog dostępnych skills (nazwa, opis, źródło, ostrzeżenia) oraz diagnostykę skanu.
    Skills to paczki wiedzy i skryptów ładowane na żądanie. Gdy zadanie pasuje do opisu skilla,
    wywołaj activate_skill(name). Każde wywołanie skanuje dysk na nowo.
    """
    return skills_tools.list_skills_impl(REGISTRY)


@register_tool
def activate_skill(name: str) -> Dict[str, Any]:
    """
    Ładuje pełne instrukcje skilla (SKILL.md bez frontmattera) w bloku <skill_content>,
    razem z katalogiem skilla i listą jego zasobów (scripts/, references/, assets/).
    Zasoby czytaj przez read_skill_resource. Ścieżki w skillu są względne od katalogu skilla.
    """
    return skills_tools.activate_skill_impl(REGISTRY, name)


@register_tool
def read_skill_resource(skill: str, path: str, max_chars: int = DEFAULT_READ_CHARS) -> Dict[str, Any]:
    """
    Czyta plik tekstowy z katalogu skilla (np. references/guide.md), ścieżka względna od katalogu skilla.
    Nie wychodzi poza katalog skilla. Domyślnie max 12000 znaków (zakres 1000-80000).
    """
    return skills_tools.read_skill_resource_impl(REGISTRY, skill, path, max_chars)


@register_tool
def run_skill_script(
    skill: str,
    script: str,
    args: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    Uruchamia skrypt z katalogu scripts/ skilla (np. script="scripts/run.py"). Argumenty podaj jako listę
    napisów (bez shella). Obsługiwane: .py (interpreter ze środowiska conda z metadata.conda-env albo
    domyślny), .js (node), .sh (bash). Użytkownik zwykle musi zatwierdzić uruchomienie w oknie GUI
    (czas oczekiwania na decyzję nie wlicza się do timeoutu). Timeout skryptu: 1-300 s (domyślnie 60);
    po jego przekroczeniu proces jest ubijany. Wynik: exit_code, stdout, stderr (po 6000 znaków).
    Skrypt uruchamia się bez interakcji, z katalogiem skilla jako katalogiem roboczym.
    """
    return skills_tools.run_skill_script_impl(
        REGISTRY, CONFIG, TRUST_STORE, gui_simple.ask_skill_confirmation,
        skill, script, args, timeout,
    )


def get_middleware():
    return [
        Middleware(
            CORSMiddleware,
            allow_origins=CONFIG["server"]["cors_allow_origins"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["mcp-session-id"],
        )
    ]


if __name__ == "__main__":
    host = CONFIG["server"]["host"]
    port = CONFIG["server"]["port"]

    scan = REGISTRY.rescan()
    print("\n" + "=" * 50)
    print("Uruchamianie serwera Vibecoder Skills MCP (faza 3)...")
    print(f"Adres serwera MCP: http://127.0.0.1:{port}/mcp")
    print(f"Skills: {len(scan.skills)} (przesłonięte: {len(scan.shadowed)}, uwagi: {len(scan.diagnostics)})")
    for diagnostic in scan.diagnostics:
        print(f"  [{diagnostic.level}] {diagnostic.path}: {diagnostic.message}")
    print("=" * 50 + "\n")

    uvicorn.run(
        mcp.http_app(middleware=get_middleware()),
        host=host,
        port=port,
        log_level="info",
    )
