# Plan implementacji nowego MCP (krok 6)

## Zasady prowadzenia prac
- Każda faza to osobna, niewielka zmiana; użytkownik akceptuje plan fazy przed zapisem.
- Po każdej fazie powstają testy pytest, ale agent ich NIE uruchamia: podaje komendę, użytkownik wkleja wynik.
- Stary MCP obsługuje bieżącą pracę (port 8100). Nowy działa równolegle na porcie **8101** do czasu przełączenia (harness nie wymaga konkretnego portu).
- Nowe MCP: własny `venv` i `requirements.txt`; komendy instalacji podaje użytkownikowi.
- Po każdej fazie aktualizacja `docs/plan/CURRENT.md`.

## Środowisko
- Miniforge: `C:\Program Files\Miniforge3` (instalacja systemowa). Ścieżki `python.exe` środowisk wykrywane przez `conda info --envs --json` i zapisywane w `config.json` (bez zakładania lokalizacji `envs`).

## Fazy
| # | Faza | Zakres | Test |
|---|---|---|---|
| 0 | Szkielet | `main.py`, `requirements.txt` (fastmcp, uvicorn, pyyaml, pytest), `config.json` z domyślnymi, `state.py`, `telemetry.py` (kopia) | start serwera, `/mcp` odpowiada |
| 1 | `skills_engine` | scanner, parser SKILL.md (tolerancja błędnego YAML), walidacja lenientna, kolizje nazw | testy jednostkowe: poprawny skill, zły YAML, brak description |
| 2 | `list_skills`, `activate_skill` | katalog, `<skill_content>`, lista zasobów, enum nazw, deduplikacja | test + wywołanie z harnessa |
| 3 | Runner i trust | hash SKILL.md + scripts/, `vibecoder-confirm`, interpreter z `conda-env`, limity outputu, timeout, błąd z komendą `conda create` | testy z atrapą skryptu; skill demo |
| 4 | `ApprovalService` + minimalne GUI | kolejka zatwierdzeń, tkinter w głównym wątku, serwer MCP w wątku w tle, dźwięk/okno na wierzchu | ręcznie: wywołanie z potwierdzeniem |
| 5 | Narzędzia podstawowe | pliki/workspace, `edit_file` z diffem, search, `execute_command` + guardy + `policy.py`, referencje, zewnętrzne MCP (trwała konfiguracja) | testy path guarda i polityk |
| 6 | Pełne okno konfiguracji | zakładki Workspace, MCP, Skills (włączanie, potwierdzenia), API, Log | ręczny przegląd |
| 7 | Kontekst projektu | `workspace_overview` z AGENTS.md i CURRENT.md, fallback `.vibecoder_context.md`, `save_progress` | test na tym repo i starym projekcie |
| 8 | Skills startowe | git-commits, python-env, react-init, api-testing, change-planning, excel-analysis, project-bootstrap | walidacja formatu; użycie w praktyce |
| 9 | Przełączenie | port 8100, usunięcie `.vibecoder_context.md`, aktualizacja AGENTS.md | praca na prawdziwym projekcie |

## MVP
Fazy 0–4: skills da się wylistować, aktywować i uruchomić z potwierdzeniem; GUI stabilne. Testowalne równolegle ze starym MCP. Faza 5 jest największa (przeniesienie dużej części starego `main.py`).

## Ryzyka
- Tkinter w głównym wątku + FastMCP w tle: weryfikacja w fazie 4, przed dalszą rozbudową.
- Nieznane wymagania harnessa wobec nowych narzędzi (limity, timeouty): wczesne testy na porcie 8101.
- Testy uruchamia użytkownik: tempo zależy od pętli "agent pisze, użytkownik uruchamia".
