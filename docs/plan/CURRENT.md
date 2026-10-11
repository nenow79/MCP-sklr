# Aktualny stan

Ostatnia aktualizacja: sesja 4. Fazy 4a i 4b zamknięte, faza 5 w toku: plan zaakceptowany (`docs/plan/phase-5.md`), podfaza 5.0 zrealizowana, następna 5.1 (`roots`).

## Zrobione
- Kroki 1-5 roadmapy (analiza, agentskills.io, referencja starego MCP, feasibility, audyt). Decyzje: `docs/decisions/0001-architecture.md`, `docs/decisions/0002-task-queue-and-web-panel.md`, spec skills: `docs/specs/skills-support.md`, plan faz: `docs/plan/implementation.md`.
- **Faza 0** (szkielet): `main.py`, `state.py`, `telemetry.py`, `config.json` (w repo), port 8101. Zweryfikowana.
- **Faza 1** (`skills_engine`: models, parser, scanner). Zweryfikowana.
- **Faza 2** (`registry`, `content`, `server/skills_tools.py`; narzędzia `list_skills`, `activate_skill`, `read_skill_resource`). Zweryfikowana.
- **Faza 3** (runner, trust, policy, conda_env, tymczasowy `gui_simple.py`, narzędzie `run_skill_script`). Zweryfikowana.
- **Faza 4a** (kolejka zadań): `tasks/` (models, store SQLite, manager, approval, dialog_adapter), `cancel_event` w runnerze, `TrustStore` z lockiem, `server/task_tools.py` (`get_task`, `list_tasks`, `wait_task`, `cancel_task`), `run_skill_script` z `wait` (domyślnie 8 s), sekcja `tasks` w configu. Zweryfikowana: `pytest` przechodzi, testy ręczne z harnessa A-E zaliczone (szybki skrypt, wolny + cancel, zatwierdzenie, odrzucenie, `interrupted` po restarcie). Szczegóły: `docs/plan/phase-4a.md`.

- **Faza 4b** (panel webowy): `panel/` (`auth.py`: token, sesje, Host/Origin; `app.py`: API Starlette + CSP; `server.py`: uvicorn w wątku na `127.0.0.1:8102`; `static/`: HTML + JS + CSS bez frameworka, polling 1,5 s), `tasks/expiry.py` (wygaszanie `awaiting_approval` po `tasks.approval_expiry_min`, domyślnie 60 min), config `panel` i `tasks.approval_ui` (`panel` | `dialog`), zapas na okno tkinter, gdy panel nie wystartuje. Zweryfikowana: `pytest` przechodzi, testy ręczne 1-6 zaliczone (zatwierdzanie, odrzucanie, anulowanie, powiadomienia, wygaśnięcie, bezpieczeństwo sesji). Szczegóły: `docs/plan/phase-4b.md`.

- **Faza 5.0** (porządki pod narzędzia podstawowe): `Task` ma `kind`, `title`, `payload`, `sensitive` (migracja `tasks.db` addytywna, `PRAGMA user_version = 1`); `exit_code = None` dla `cancelled` i `timed_out`; `trust` degradowane do `allow` dla zadań `sensitive`; panel i okno zapasowe renderują zadania wg `kind` (okno odrzuca automatycznie `sensitive`, `root_change`, `secret_reveal` i treść powyżej 10000 znaków). Zweryfikowana: `pytest` przechodzi (nowy `tests/test_phase5_tasks.py`), testy ręczne `hello`, `sleep` + anulowanie, `sleep` + timeout zaliczone. Szczegóły: sekcja "Wyniki 5.0" w `docs/plan/phase-5.md`.

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
**Stan 5.1: kroki 1-5 z 6 ZROBIONE, testy przechodzą; krok 5: `list_roots`, `request_root`, podłączenie w `main.py` (migracja z `config.json.bak`, skills projektowe z domyślnego roota); następny krok 6 (testy ręczne z harnessa)** (krok 4: `/api/roots*` w `panel/app.py` z opcjonalnym `roots`, wyjątki `RootNotFound`/`RootConflict`, zakładka "Foldery"; panel nie jest jeszcze podłączony do rejestru w `main.py`; krok 1-3: `config_store.py`, `roots.py`, `roots_registry.py`, parametr `lock` w `TrustStore`, ponawianie `os.replace` w `state.save_config`, `"roots": None` w `state.py`). **Krok 5 ZROBIONY; następny krok 6 (testy ręczne). Opis kroku 5 (zrealizowany):** (narzędzia `list_roots` i `request_root`, podłączenie w `main.py`, migracja przy starcie, getter telemetrii, callback odświeżający skills projektowe z domyślnego roota) i krok 6 (testy ręczne). Szczegółowy plan i status kroków: sekcja "Plan podfazy 5.1" w `docs/plan/phase-5.md`.

**Faza 5, podfaza 5.1 (`roots`)**: model folderów `rw`/`ro` w `config.json` zamiast workspace + referencji (`ConfigStore` z lockiem wspólnym z `TrustStore`, `RootsRegistry`, migracja configu, `/api/roots` i zakładka "Foldery" w panelu, `list_roots`, `request_root`). Najpierw plan szczegółowy podfazy do akceptacji (bez kodu przed akceptacją). Dalej wg `docs/plan/phase-5.md`: 5a (path guard + odczyt + search), 5b (zapisy z diffem), 5c (`execute_command` + `command_policy`), 5e (domknięcie, ADR 0003). Zewnętrzne MCP przeniesione do fazy 6, kontekst projektu do fazy 7, skills `git`/`python-env`/`npm`/`docker`/`env-info` do fazy 8.
Ustalenia z planu fazy 5 (zatwierdzone): zatwierdzanie zapisów i komend bez limitu czasu (zawsze natychmiastowy `task_id`), lista zmian w jednym zadaniu `edit_file`, P11 (`cd x && komenda` tłumaczone na `cwd`), maskowanie sekretów w odczycie (P14), `command_env_passthrough` (P15), `docker` tylko jako skill.
Uwagi:
- Lock w `TrustStore` chroni tylko dostęp przez TrustStore; inny kod zmieniający `config` (faza 6) musi użyć tej samej synchronizacji.
- `execute_command` zwiększa ryzyko opisane w modelu zagrożeń ADR 0002 (model może wysyłać żądania HTTP do localhost): przy projektowaniu guardów to uwzględnić (np. blokada portu panelu 8102).

## Do rozważenia (drobne)
- (ZAŁATWIONE w 5.0) `exit_code` = `None` przy `cancelled` i `timed_out`.
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
