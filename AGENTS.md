# AGENTS.md

## Projekt
Nowy lokalny serwer MCP ("vibecoder") oparty na standardzie Agent Skills (agentskills.io). Zamienia ubogi harness przeglądarkowy w agenta: pliki/workspace, komendy z guardami, referencje, zewnętrzne MCP oraz skills (katalog, aktywacja, uruchamianie skryptów).

## Stack
Python, FastMCP + Uvicorn (port 8100), tkinter (stałe okno konfiguracji), Miniforge/conda (środowiska skryptów skills).

## Status
Faza projektowa. Aktualny stan: `docs/plan/CURRENT.md`. Zadania: `docs/plan/backlog.md`.

## Dokumentacja
- `docs/specs/skills-support.md` – obsługa skills (spec + nasze rozszerzenia)
- `docs/decisions/` – ADR (decyzje architektoniczne)
- `docs/plan/` – bieżący stan (`CURRENT.md`), backlog, plan implementacji (`implementation.md`)

## Konwencje
- Jeden plik = jedna odpowiedzialność.
- Conventional Commits, wiadomości ekstremalnie zwięzłe.
- Plan i akceptacja użytkownika przed każdą zmianą kodu.
- Stary MCP (`MCP-my-codex`) jest referencją tylko do odczytu.
- Nie uruchamiaj pytest ani serwera API/uvicorn bez wyraźnego polecenia; podawaj komendę użytkownikowi.
- Python: venv/conda; instalacji i tworzenia środowisk nie wykonuj automatycznie.

## Migracja
Do czasu przełączenia na nowe MCP obowiązuje też `.vibecoder_context.md` (wymagany przez stary MCP).
