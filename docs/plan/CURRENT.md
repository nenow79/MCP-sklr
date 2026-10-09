# Aktualny stan

Ostatnia aktualizacja: sesja 2. Faza 3 zweryfikowana, zaplanowana faza 4a/4b (ADR 0002).

## Zrobione
- Kroki 1-5 roadmapy (analiza, agentskills.io, referencja starego MCP, feasibility, audyt). Decyzje: `docs/decisions/0001-architecture.md`, `docs/decisions/0002-task-queue-and-web-panel.md`, spec skills: `docs/specs/skills-support.md`, plan faz: `docs/plan/implementation.md`.
- **Faza 0** (szkielet): `main.py`, `state.py`, `telemetry.py`, `config.json` (w repo), port 8101. Zweryfikowana.
- **Faza 1** (`skills_engine`: models, parser, scanner). Zweryfikowana.
- **Faza 2** (`registry`, `content`, `server/skills_tools.py`; narzędzia `list_skills`, `activate_skill`, `read_skill_resource`). Zweryfikowana.
- **Faza 3** (runner, trust, policy, conda_env, tymczasowy `gui_simple.py`, narzędzie `run_skill_script`). Zweryfikowana: `pytest` 158 passed, 2 skipped (symlinki, Developer Mode). Testy ręczne z harnessa na `hello-skill`: `list_skills`, `activate_skill`, `read_skill_resource`, `run_skill_script` (`hello.py`, `sleep.py`) działają.

## Pomiar limitu harnessa (2026-10-09)
- sklr podłączony bezpośrednio do harnessa (`http://127.0.0.1:8101/mcp`): `sleep.py` 5, 9, 10, 11 s OK; 12, 15, 30 s błąd `SyntaxError ... ": ping - 2"... is not valid JSON`.
- Przez stary MCP jako pośrednika: 8 s OK, 9 s błąd.
- Błąd to nie timeout skryptu: wygląda na keep-alive SSE (`: ping`) niesparsowany przez klienta. **Bezpieczne maksimum jednego wywołania: 8 s.** Szczegóły i decyzja: ADR 0002.
- Ten sam błąd `: ping` pojawił się także przy wolnych wywołaniach `search_code` (przeszukiwanie całego pakietu w venv) w starym MCP; to obserwacja, nie potwierdzona przyczyna.

## Ustalenia środowiskowe
- Nowy MCP: `C:\Users\rap1tw\1_MyScripts\Python\MCP-my-codex-sklr`, własny `venv`, FastMCP 4.0.11, port 8101. Alias zewnętrznego MCP w starym vibecoderze: `sklr` (`http://localhost:8101/mcp`). Stary MCP na 8100 nadal obsługuje czat (docelowo zostanie wycofany po fazie 5).
- Referencja starego MCP: katalog `...\MCP-my-codex` (alias `vibecoder`, tylko odczyt). Przeglądarka: `web_browser` (port 8112, osobny Chrome z debugiem na 9222).
- Miniforge: `C:\Program Files\Miniforge3`. Katalog centralny skills: `C:\Users\rap1tw\.agents\skills` (zawiera `hello-skill`).
- Limity runnera: timeout skryptu domyślnie 60 s, max 300 s (do rewizji w fazie 4a), okno potwierdzenia 120 s (nie wlicza się do timeoutu), output 6000 znaków na strumień.
- Narzędzie `edit_file` starego MCP potrafi zgłosić błąd `: ping` mimo zapisu: sprawdzać plik po zapisie.

## Następny krok
**Faza 4a**: plan szczegółowy napisany w `docs/plan/phase-4a.md` (status: ZAAKCEPTOWANY, decyzje 1-7 potwierdzone; pkt 1: przy `awaiting_approval` zwrot od razu; krok 1 ZROBIONY (tasks/models.py + store.py, 15 testów); krok 2 ZROBIONY (runner cancel_event); krok 3 ZROBIONY (tasks/approval.py + manager.py); krok 4 ZROBIONY (TrustStore z lockiem); krok 5 w toku: server/task_tools.py + prepare_script_run + tests/conftest.py (main.py nie działa do kroku 6); uwaga: lock chroni tylko dostęp przez TrustStore, inny kod zmieniający `config` (faza 6) musi użyć tej samej synchronizacji). Kolejka zadań (SQLite, stany z ADR 0002), `run_skill_script` z parametrem `wait` (domyślnie 8 s), narzędzia `get_task`, `list_tasks`, `wait_task`, `cancel_task`, testy jednostkowe kolejki bez GUI. Zatwierdzenia tymczasowo przez `gui_simple.py`. Przed kodem użytkownik potwierdza lub zmienia 7 decyzji z sekcji "Decyzje do potwierdzenia".
Potem **faza 4b**: panel webowy na `127.0.0.1:8102` (token, ciasteczko `HttpOnly`, kontrola `Origin`/`Host`), zatwierdzanie i odrzucanie, powiadomienia.

## Do rozważenia (drobne)
- Nazwy parametrów ujednolicone (2026-10-09): `name` w `activate_skill`, `read_skill_resource` i `run_skill_script`. Zweryfikowane: `pytest` przechodzi, po restarcie serwera wywołania z `name` działają.
- `json_response=True` w `http_app()` jako test, czy znika błąd pingów (kolejka potrzebna niezależnie).

## Dalej
Fazy 5-9 wg `docs/plan/implementation.md` (narzędzia podstawowe + policy, konfiguracja, kontekst projektu i `save_progress`, skills startowe, przełączenie na port 8100).

## Otwarte pytania
- Limity kolejki (równoległość, rozmiar wyniku, retencja historii).
- Zatwierdzenie pojedynczego zadania a zaufanie do skryptu na stałe (hash).
- Panel w wątku serwera czy osobny proces.
- Czy zachować opcjonalną twardą blokadę przy braku `AGENTS.md`.
- Czy włączyć Windows Developer Mode (2 pominięte testy symlinków).

## Zasady pracy (przypomnienie)
Plan i akceptacja użytkownika przed każdą zmianą kodu. Testy (`pytest`) uruchamia użytkownik, agent podaje komendę. Nie tworzyć venv, nie instalować pakietów ani nie startować serwera bez polecenia użytkownika.
