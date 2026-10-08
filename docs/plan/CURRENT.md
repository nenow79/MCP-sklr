# Aktualny stan

Ostatnia aktualizacja: koniec sesji 1. Faza 3 napisana, czeka na weryfikację.

## Zrobione
- Kroki 1-5 roadmapy (analiza, agentskills.io, referencja starego MCP, feasibility, audyt). Decyzje: `docs/decisions/0001-architecture.md`, spec skills: `docs/specs/skills-support.md`, plan faz: `docs/plan/implementation.md`.
- **Faza 0** (szkielet): `main.py`, `state.py`, `telemetry.py`, `config.json` (w repo), port 8101. Zweryfikowana.
- **Faza 1** (`skills_engine`: models, parser, scanner). Zweryfikowana (32 testy).
- **Faza 2** (`registry`, `content`, `server/skills_tools.py`; narzędzia `list_skills`, `activate_skill`, `read_skill_resource`). Zweryfikowana: 73 testy przechodzą (1 pominięty: symlink, wymaga Developer Mode), narzędzia działają z harnessa na skillu `hello-skill`.
- **Faza 3** (runner i trust) NAPISANA, NIEZWERYFIKOWANA:
  - `skills_engine/trust.py` (hash SKILL.md + scripts/, `TrustStore`), `policy.py` (decyzja o potwierdzeniu), `conda_env.py` (wykrywanie python.exe środowiska, bez instalacji), `runner.py` (bez shella, oczyszczone env, timeout z ubijaniem drzewa procesów, limit outputu).
  - `gui_simple.py`: TYMCZASOWY dialog (Uruchom / Ufaj i uruchom / Zablokuj), do wymiany w fazie 4.
  - Narzędzie `run_skill_script` w `main.py`, logika w `server/skills_tools.py`.
  - `docs/examples/hello-skill/scripts/sleep.py` do pomiaru limitu harnessa.
  - Testy: `test_skills_trust.py`, `test_skills_policy.py`, `test_conda_env.py`, `test_skills_runner.py`, `test_skills_run_tool.py`.

## Ustalenia środowiskowe
- Nowy MCP: `C:\Users\rap1tw\1_MyScripts\Python\MCP-my-codex-sklr`, własny `venv`, port 8101, podłączony w harnessie jako zewnętrzny MCP z aliasem `new_vibecoder` (stary MCP na 8100 nadal obsługuje czat).
- Referencja starego MCP: alias `mcp_vibecodding` (`...\MCP-my-codex`, tylko odczyt). Przeglądarka: alias `web_browser` (port 8112).
- Miniforge: `C:\Program Files\Miniforge3`. Katalog centralny skills: `C:\Users\rap1tw\.agents\skills` (zawiera `hello-skill`).
- Limity runnera: timeout skryptu domyślnie 60 s, max 300 s (tymczasowo), okno potwierdzenia 120 s (nie wlicza się do timeoutu), output 6000 znaków na strumień.
- Znany błąd: wywołania zewnętrznych MCP czasem zwracają `SyntaxError ... ": ping - 2"... is not valid JSON` (konsola starego MCP). Pomaga ponowienie i podawanie `arguments: {}`.
- Narzędzie `edit_file` starego MCP potrafi zgłosić ten błąd mimo zapisu: sprawdzać plik po zapisie.

## Następny krok (jutro, w tej kolejności)
1. Użytkownik uruchamia `pytest` (oczekiwane ok. 150 testów; wklejić wynik, poprawić ewentualne błędy).
2. Aktualizacja skilla w katalogu centralnym:
   `xcopy docs\examples\hello-skill "%USERPROFILE%\.agents\skills\hello-skill\" /E /I /Y`, restart `python main.py`.
3. Testy ręczne `run_skill_script` z harnessa: zablokowanie, uruchomienie, flaga `vibecoder-confirm: "false"` + "Ufaj i uruchom", zmiana skryptu (zaufanie unieważnione), timeout.
4. Pomiar limitu czasu harnessa na `sleep.py` (np. 30, 60, 120, 240 s), potem ustawienie maksimum timeoutu (obecnie 300 s). Jeśli limit harnessa jest niski, rozważyć tryb w tle (osobne zadanie).
5. Faza 4: `ApprovalService` + minimalne GUI (tkinter w głównym wątku, serwer MCP w wątku w tle, wymiana `gui_simple.py`). Plan do akceptacji przed kodem.

## Dalej
Fazy 5-9 wg `docs/plan/implementation.md` (narzędzia podstawowe + policy, pełne okno konfiguracji, kontekst projektu i `save_progress`, skills startowe, przełączenie na port 8100).

## Otwarte pytania
- Czy zachować opcjonalną twardą blokadę przy braku `AGENTS.md`.
- Maksymalny timeout skryptu po pomiarze limitu harnessa; czy potrzebny tryb w tle.
- Czy włączyć Windows Developer Mode, żeby uruchomić 2 testy symlinków (obecnie pomijane).

## Zasady pracy (przypomnienie)
Plan i akceptacja użytkownika przed każdą zmianą kodu. Testy (`pytest`) uruchamia użytkownik, agent podaje komendę. Nie tworzyć venv, nie instalować pakietów ani nie startować serwera bez polecenia użytkownika.
