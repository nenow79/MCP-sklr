# Aktualny stan

Ostatnia aktualizacja: sesja 3. Fazy 4a i 4b zamknięte, następna jest faza 5.

## Zrobione
- Kroki 1-5 roadmapy (analiza, agentskills.io, referencja starego MCP, feasibility, audyt). Decyzje: `docs/decisions/0001-architecture.md`, `docs/decisions/0002-task-queue-and-web-panel.md`, spec skills: `docs/specs/skills-support.md`, plan faz: `docs/plan/implementation.md`.
- **Faza 0** (szkielet): `main.py`, `state.py`, `telemetry.py`, `config.json` (w repo), port 8101. Zweryfikowana.
- **Faza 1** (`skills_engine`: models, parser, scanner). Zweryfikowana.
- **Faza 2** (`registry`, `content`, `server/skills_tools.py`; narzędzia `list_skills`, `activate_skill`, `read_skill_resource`). Zweryfikowana.
- **Faza 3** (runner, trust, policy, conda_env, tymczasowy `gui_simple.py`, narzędzie `run_skill_script`). Zweryfikowana.
- **Faza 4a** (kolejka zadań): `tasks/` (models, store SQLite, manager, approval, dialog_adapter), `cancel_event` w runnerze, `TrustStore` z lockiem, `server/task_tools.py` (`get_task`, `list_tasks`, `wait_task`, `cancel_task`), `run_skill_script` z `wait` (domyślnie 8 s), sekcja `tasks` w configu. Zweryfikowana: `pytest` przechodzi, testy ręczne z harnessa A-E zaliczone (szybki skrypt, wolny + cancel, zatwierdzenie, odrzucenie, `interrupted` po restarcie). Szczegóły: `docs/plan/phase-4a.md`.

- **Faza 4b** (panel webowy): `panel/` (`auth.py`: token, sesje, Host/Origin; `app.py`: API Starlette + CSP; `server.py`: uvicorn w wątku na `127.0.0.1:8102`; `static/`: HTML + JS + CSS bez frameworka, polling 1,5 s), `tasks/expiry.py` (wygaszanie `awaiting_approval` po `tasks.approval_expiry_min`, domyślnie 60 min), config `panel` i `tasks.approval_ui` (`panel` | `dialog`), zapas na okno tkinter, gdy panel nie wystartuje. Zweryfikowana: `pytest` przechodzi, testy ręczne 1-6 zaliczone (zatwierdzanie, odrzucanie, anulowanie, powiadomienia, wygaśnięcie, bezpieczeństwo sesji). Szczegóły: `docs/plan/phase-4b.md`.

## Pomiar limitu harnessa (2026-10-09)
- sklr podłączony bezpośrednio do harnessa (`http://127.0.0.1:8101/mcp`): `sleep.py` 5, 9, 10, 11 s OK; 12, 15, 30 s błąd `SyntaxError ... \": ping - 2\"... is not valid JSON`.
- Przez stary MCP jako pośrednika: 8 s OK, 9 s błąd.
- Błąd to nie timeout skryptu: wygląda na keep-alive SSE (`: ping`) niesparsowany przez klienta. **Bezpieczne maksimum jednego wywołania: 8 s.** Szczegóły i decyzja: ADR 0002.
- Eksperyment `json_response=True` w `http_app()` (2026-10-10): niekonkluzywny. Błąd `: ping` nadal pojawia się przy pierwszym wywołaniu po restarcie serwera (drugie działa), więc to prawdopodobnie nawiązywanie sesji po stronie klienta. `json_response=True` zostaje w `main.py`.

## Ustalenia środowiskowe
- Nowy MCP: `C:\Users\rap1tw\1_MyScripts\Python\MCP-my-codex-sklr`, własny `venv`, FastMCP 4.0.11, port 8101. Alias zewnętrznego MCP w starym vibecoderze: `sklr` (`http://localhost:8101/mcp`). Stary MCP na 8100 nadal obsługuje czat (docelowo zostanie wycofany po fazie 5).
- Referencja starego MCP: katalog `...\MCP-my-codex` (alias `vibecoder`, tylko odczyt). Przeglądarka: `web_browser` (port 8112, osobny Chrome z debugiem na 9222).
- Miniforge: `C:\Program Files\Miniforge3`. Katalog centralny skills: `C:\Users\rap1tw\.agents\skills` (zawiera `hello-skill`).
- Limity runnera: timeout skryptu domyślnie 60 s, max 300 s, okno potwierdzenia 120 s (nie wlicza się do timeoutu), output 6000 znaków na strumień.
- Narzędzie `edit_file` starego MCP potrafi zgłosić błąd `: ping` mimo zapisu: sprawdzać plik po zapisie.
- Pierwsze wywołanie zewnętrznego MCP po restarcie sklr zwykle kończy się błędem `: ping`; wystarczy powtórzyć.

## Następny krok
**Faza 5** (narzędzia podstawowe): pliki/workspace, `edit_file` z diffem, search, `execute_command` + guardy + `policy.py`, referencje, zewnętrzne MCP (trwała konfiguracja) wg `docs/plan/implementation.md`. Zacząć od planu szczegółowego w `docs/plan/phase-5.md` do akceptacji (bez kodu przed akceptacją). Zatwierdzanie operacji (diff, komendy) ma korzystać z istniejącego `ApprovalService` i panelu z fazy 4b.
Uwagi:
- Lock w `TrustStore` chroni tylko dostęp przez TrustStore; inny kod zmieniający `config` (faza 6) musi użyć tej samej synchronizacji.
- `execute_command` zwiększa ryzyko opisane w modelu zagrożeń ADR 0002 (model może wysyłać żądania HTTP do localhost): przy projektowaniu guardów to uwzględnić (np. blokada portu panelu 8102).

## Do rozważenia (drobne)
- `exit_code` wynosi 1 przy statusie `cancelled` (kosmetyka; do decyzji, czy zerować/ustawiać None).
- `offer_trust` jest dziś zawsze `false` (powód: domyślnie każde uruchomienie wymaga potwierdzenia), więc przycisk "Ufaj skillowi" w panelu nie pojawia się. Do rozstrzygnięcia: kiedy oferować zaufanie na stałe (hash); logika w `policy.py`. Przycisk pokryty tylko testem jednostkowym.
- `gui_simple.py` zostaje jako zapas (`tasks.approval_ui: "dialog"` lub brak startu panelu); do rewizji w fazie 6.
- Pierwsze wywołanie MCP po restarcie sklr kończy się błędem `: ping`; przy `run_skill_script` serwer mimo to tworzy zadanie, więc przed ponowieniem sprawdzić `list_tasks` (unikanie duplikatów).

## Dalej
Fazy 5-9 wg `docs/plan/implementation.md` (narzędzia podstawowe + policy, konfiguracja, kontekst projektu i `save_progress`, skills startowe, przełączenie na port 8100).

## Otwarte pytania
- Zatwierdzenie pojedynczego zadania a zaufanie do skryptu na stałe (hash).
- Czy zachować opcjonalną twardą blokadę przy braku `AGENTS.md`.
- Czy włączyć Windows Developer Mode (2 pominięte testy symlinków).

## Zasady pracy (przypomnienie)
Plan i akceptacja użytkownika przed każdą zmianą kodu. Testy (`pytest`) uruchamia użytkownik, agent podaje komendę. Nie tworzyć venv, nie instalować pakietów ani nie startować serwera bez polecenia użytkownika.
