# ADR 0001: Architektura nowego MCP opartego na skills

Status: zaakceptowany (rozmowa, krok 5 audytu)

## Kontekst
Harness w przeglądarce obsługuje jeden lokalny MCP. Stary MCP (`MCP-my-codex`): `main.py` 1769 linii, stan globalny tracony po restarcie, osobne `tk.Tk()` na każde okienko (blokada wątku, brak bezpieczeństwa wątkowego), walidacja komend przez prefiksy i czarną listę znaków z `shell=True`, token API w base64, złote zasady zaszyte w kodzie.

## Decyzje
1. **Skills**: standard agentskills.io; katalog centralny `~/.agents/skills/` + opcjonalny projektowy; zaufanie przez hash; `metadata.vibecoder-confirm` tylko dla katalogu centralnego; `metadata.conda-env` (Miniforge, bez auto-instalacji).
2. **Złote zasady i narzędzia Excel/CSV -> skills.** Twarde polityki (pytest, uvicorn) wymuszane w kodzie (`policy.py`).
3. **Kontekst projektu**: `AGENTS.md` + `docs/` (specs, decisions, plan/CURRENT.md, plan/backlog.md) zamiast `.vibecoder_context.md` i checkpointów. `CURRENT.md` nadpisywany przez `save_progress`; historia w git i ADR. Fallback do `.vibecoder_context.md` dla starych projektów. Brak twardej blokady execute_command (skill `project-bootstrap`; blokada opcjonalna w configu).
4. **GUI**: jedno stałe okno tkinter (zakładki Workspace, MCP, Skills, API, Log), serwer MCP w wątku w tle, `ApprovalService` z kolejką zatwierdzeń oddzieloną od UI (otwarte na przyszły frontend WWW). Powiadomienie o zatwierdzeniu: okno na wierzchu, dźwięk, sygnał na pasku zadań.
5. **Trwała konfiguracja** w `config.json` (referencje, zewnętrzne MCP, skills, hashe, potwierdzenia, host/CORS).
6. **CORS i bind bez zmian** (wymagania harnessa), ale parametryzowane w configu.
7. **Wykonywanie komend**: docelowo lista argumentów bez shella, interpreter wskazywany wprost.
8. **Token API**: docelowo Windows Credential Manager (`keyring`) lub tylko pamięć.
9. **Moduły**: `main.py`, `server/`, `skills_engine/`, `policy.py`, `state.py`, `gui/`, `telemetry.py`.

## Zachowujemy ze starego MCP
Wrapper `register_tool` + telemetria, path guard, diff GUI, guardy komend (git/npm/pip/alembic/-c), limit outputu, timeouty, wzorzec referencji i zewnętrznych MCP.

## Konsekwencje
Przepisanie okienek na wspólny mechanizm i refaktor `main.py` na moduły; w zamian stabilność GUI, trwała konfiguracja i przenośny standard kontekstu.
