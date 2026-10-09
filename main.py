#!/usr/bin/env python3
"""Vibecoder Skills MCP - punkt wejścia (faza 4a: skills + kolejka zadań)."""
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

import gui_simple
from server import skills_tools, task_tools
from skills_engine import SkillRegistry
from skills_engine.content import DEFAULT_READ_CHARS
from skills_engine.runner import DEFAULT_TIMEOUT
from skills_engine.trust import TrustStore
from state import load_config, resolve_central_skills_dir, resolve_workspace_path, save_config
from tasks.approval import ApprovalService
from tasks.dialog_adapter import make_dialog_notifier
from tasks.manager import TaskManager
from tasks.store import TaskStore
from telemetry import track_mcp_tool

CONFIG = load_config()

MAX_WAIT_LIMIT_S = 8  # twardy limit harnessa (ADR 0002)
TASKS_DB = Path(__file__).resolve().parent / "tasks.db"


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return default
    return value


_TASKS_CFG = CONFIG.get("tasks", {})
MAX_WAIT_S = min(_positive_int(_TASKS_CFG.get("max_wait_s"), MAX_WAIT_LIMIT_S), MAX_WAIT_LIMIT_S)

# Do fazy 4 (GUI) workspace pochodzi z config.json (workspace.path); null = tylko katalog centralny.
PROJECT_ROOT = resolve_workspace_path(CONFIG)
WORKSPACE_NAME = PROJECT_ROOT.name if PROJECT_ROOT else ""

REGISTRY = SkillRegistry(
    central_dir=resolve_central_skills_dir(CONFIG),
    project_root=PROJECT_ROOT,
    disabled=CONFIG["skills"]["disabled"],
)
TRUST_STORE = TrustStore(CONFIG, save=lambda: save_config(CONFIG))

TASK_STORE = TaskStore(str(TASKS_DB))
INTERRUPTED_ON_START = TASK_STORE.mark_interrupted()
PURGED_ON_START = TASK_STORE.purge(
    _positive_int(_TASKS_CFG.get("retention_days"), 30),
    _positive_int(_TASKS_CFG.get("retention_max"), 500),
)
APPROVAL = ApprovalService()
MAX_PARALLEL = _positive_int(_TASKS_CFG.get("max_parallel"), 2)
TASK_MANAGER = TaskManager(
    TASK_STORE,
    APPROVAL,
    max_parallel=MAX_PARALLEL,
    max_queued=_positive_int(_TASKS_CFG.get("max_queued"), 50),
    max_wait_s=MAX_WAIT_S,
)
# Tymczasowo (do fazy 4b): zatwierdzanie przez okno tkinter; w 4b adapterem będzie panel web.
APPROVAL.set_notifier(make_dialog_notifier(
    APPROVAL,
    gui_simple.ask_skill_confirmation,
    _positive_int(_TASKS_CFG.get("approval_timeout_s"), 120),
))

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
def read_skill_resource(name: str, path: str, max_chars: int = DEFAULT_READ_CHARS) -> Dict[str, Any]:
    """
    Czyta plik tekstowy z katalogu skilla (np. references/guide.md), ścieżka względna od katalogu skilla.
    Nie wychodzi poza katalog skilla. Domyślnie max 12000 znaków (zakres 1000-80000).
    """
    return skills_tools.read_skill_resource_impl(REGISTRY, name, path, max_chars)


@register_tool
def run_skill_script(
    name: str,
    script: str,
    args: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    wait: float = task_tools.DEFAULT_WAIT,
) -> Dict[str, Any]:
    """
    Uruchamia skrypt z katalogu scripts/ skilla (np. script="scripts/run.py") jako ZADANIE w kolejce.
    Argumenty podaj jako listę napisów (bez shella). Obsługiwane: .py (interpreter ze środowiska conda
    z metadata.conda-env albo domyślny), .js (node), .sh (bash). Timeout skryptu: 1-300 s (domyślnie 60);
    po jego przekroczeniu proces jest ubijany. Skrypt działa bez interakcji, w katalogu skilla.
    Czeka maks. `wait` s (domyślnie 8, limit 8). Szybki skrypt: od razu wynik (exit_code, stdout, stderr,
    po 6000 znaków) wraz z task_id i status. Wolny: success=true z task_id i status (queued/running) -
    użyj wait_task(task_id) / get_task(task_id). Użytkownik zwykle musi zatwierdzić uruchomienie
    w oknie GUI: wtedy od razu wraca task_id ze statusem awaiting_approval (zatwierdza użytkownik,
    nie ty); po jego decyzji sprawdź wynik przez wait_task / get_task.
    """
    return task_tools.run_skill_script_impl(
        REGISTRY, CONFIG, TRUST_STORE, TASK_MANAGER,
        name, script, args, timeout, wait,
    )


@register_tool
def get_task(task_id: int) -> Dict[str, Any]:
    """
    Zwraca pełne dane zadania z kolejki (status, skrypt, argumenty, decyzja, exit_code, stdout, stderr,
    błąd). Statusy: awaiting_approval, queued, running, done, failed, rejected, timed_out, cancelled,
    interrupted (przerwane restartem serwera).
    """
    return task_tools.get_task_impl(TASK_MANAGER, task_id)


@register_tool
def list_tasks(status: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    """
    Zwięzła lista zadań (najnowsze pierwsze, bez stdout/stderr). Opcjonalny filtr status (np. "running",
    "awaiting_approval"), limit 1-100 (domyślnie 20). Szczegóły zadania: get_task(task_id).
    """
    return task_tools.list_tasks_impl(TASK_MANAGER, status, limit)


@register_tool
def wait_task(task_id: int, seconds: float = task_tools.DEFAULT_WAIT) -> Dict[str, Any]:
    """
    Czeka maks. `seconds` s (limit 8) na zakończenie zadania i zwraca jego dane. Jeśli zadanie nadal trwa
    (lub czeka na zatwierdzenie przez użytkownika), zwraca aktualny status z podpowiedzią.
    """
    return task_tools.wait_task_impl(TASK_MANAGER, task_id, seconds)


@register_tool
def cancel_task(task_id: int) -> Dict[str, Any]:
    """
    Anuluje zadanie: oczekujące (awaiting_approval/queued) od razu, działające przez ubicie procesu
    (zwraca częściowy output). Zadanie już zakończone pozostaje bez zmian.
    """
    return task_tools.cancel_task_impl(TASK_MANAGER, task_id)


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
    print("Uruchamianie serwera Vibecoder Skills MCP (faza 4a)...")
    print(f"Adres serwera MCP: http://127.0.0.1:{port}/mcp")
    print(f"Skills: {len(scan.skills)} (przesłonięte: {len(scan.shadowed)}, uwagi: {len(scan.diagnostics)})")
    for diagnostic in scan.diagnostics:
        print(f"  [{diagnostic.level}] {diagnostic.path}: {diagnostic.message}")
    print(f"Zadania: baza {TASKS_DB.name}, przerwane restartem: {INTERRUPTED_ON_START}, "
          f"usunięte (retencja): {PURGED_ON_START}, max równolegle: {MAX_PARALLEL}")
    print("=" * 50 + "\n")

    try:
        uvicorn.run(
            mcp.http_app(middleware=get_middleware()),
            host=host,
            port=port,
            log_level="info",
        )
    finally:
        TASK_MANAGER.shutdown()
