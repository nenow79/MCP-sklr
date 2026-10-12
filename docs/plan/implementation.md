# Plan implementacji nowego MCP (krok 6)

## Zasady prowadzenia prac
- Każda faza to osobna, niewielka zmiana; użytkownik akceptuje plan fazy przed zapisem.
- Po każdej fazie powstają testy pytest, ale agent ich NIE uruchamia: podaje komendę, użytkownik wkleja wynik.
- Stary MCP obsługuje bieżącą pracę (port 8100). Nowy działa równolegle na porcie **8101** do czasu przełączenia (harness nie wymaga konkretnego portu).
- Nowe MCP: własny `venv` i `requirements.txt`; komendy instalacji podaje użytkownikowi.
- Po każdej fazie aktualizacja `docs/plan/CURRENT.md` i wyników planu fazy; bez osobnego aktywnego checkpointu.
- Aktualnie pracujemy bezpośrednio przez sklr, stary MCP dostępny awaryjnie. AGENTS.md + docs/ są źródłami kontekstu. .vibecoder_context.md/.vibecoder_checkpoints.md użytkownik przeniósł do docs/archive/vibecoder/ jako historię; obecność w archiwum i brak w korzeniu potwierdzono listowaniem. Nie aktualizujemy ich ani nie wymagamy odczytu. Poniższe historyczne etapy nie przywracają obowiązku tych plików.

## Środowisko
- Miniforge: `C:\Program Files\Miniforge3` (instalacja systemowa). Ścieżki `python.exe` środowisk wykrywane przez `conda info --envs --json` i zapisywane w `config.json` (bez zakładania lokalizacji `envs`).

## Fazy
| # | Faza | Zakres | Test |
|---|---|---|---|
| 0 | Szkielet | `main.py`, `requirements.txt` (fastmcp, uvicorn, pyyaml, pytest), `config.json` z domyślnymi, `state.py`, `telemetry.py` (kopia) | start serwera, `/mcp` odpowiada |
| 1 | `skills_engine` | scanner, parser SKILL.md (tolerancja błędnego YAML), walidacja lenientna, kolizje nazw | testy jednostkowe: poprawny skill, zły YAML, brak description |
| 2 | `list_skills`, `activate_skill` | katalog, `<skill_content>`, lista zasobów, enum nazw, deduplikacja | test + wywołanie z harnessa |
| 3 | Runner i trust | hash SKILL.md + scripts/, `vibecoder-confirm`, interpreter z `conda-env`, limity outputu, timeout, błąd z komendą `conda create` | testy z atrapą skryptu; skill demo |
| 4a | Kolejka zadań | SQLite, stany zadań, pula wątków, `run_skill_script` z `wait` (domyślnie 8 s), narzędzia `get_task`/`list_tasks`/`wait_task`/`cancel_task`, `ApprovalService` niezależny od UI; zatwierdzenia tymczasowo przez `gui_simple.py` (ADR 0002) | testy jednostkowe kolejki (stany, anulowanie, restart) + wywołanie z harnessa |
| 4b | Panel webowy | `127.0.0.1:8102`, lista i szczegóły zadań, zatwierdzanie/odrzucanie, token + ciasteczko `HttpOnly`, kontrola `Origin`/`Host`, powiadomienia | testy API panelu + ręcznie: zatwierdzenie z przeglądarki |
| 5 | Narzędzia podstawowe | pliki/workspace, `edit_file` z diffem, search, `execute_command` + guardy + `policy.py`, referencje, zewnętrzne MCP (trwała konfiguracja) | testy path guarda i polityk |
| 6 | Pełne okno konfiguracji | zakładki Workspace, MCP, Skills (włączanie, potwierdzenia), API, Log | ręczny przegląd |
| 7 | Kontekst projektu | `workspace_overview` z AGENTS.md i CURRENT.md, `save_progress`; opcjonalny legacy fallback dla innych starych projektów do rozstrzygnięcia, bez użycia archiwum tego repo | test na tym repo i starym projekcie |
| 8 | Skills startowe | git-commits, python-env, react-init, api-testing, change-planning, excel-analysis, project-bootstrap | walidacja formatu; użycie w praktyce |
| 9 | Przełączenie | docelowy port 8100, aktualizacja AGENTS.md; kontekst/checkpointy vibecodera tego repo wycofane do archiwum już podczas 5b | praca na prawdziwym projekcie |

## MVP
Fazy 0–4b: skills da się wylistować, aktywować i uruchomić jako zadanie w kolejce z zatwierdzaniem w panelu webowym. Testowalne równolegle ze starym MCP. Faza 5 jest największa (przeniesienie dużej części starego `main.py`).

## Ryzyka
- Limit harnessa: pojedyncze wywołanie MCP do ok. 11 s (bezpiecznie 8 s), potem błąd parsowania pingu SSE. Stąd asynchroniczna kolejka (ADR 0002). Nieznany limit tur narzędziowych w jednej odpowiedzi.
- Bezpieczeństwo panelu web: model ma powłokę, więc zatwierdzanie wymaga tajnego tokenu tylko w pamięci serwera; panel na osobnym porcie bez CORS (ADR 0002). Panel testowany częściowo ręcznie.
- Serwer nie może obudzić LLM po zakończeniu zadania: powiadomienie trafia tylko do użytkownika w panelu.
- Testy uruchamia użytkownik: tempo zależy od pętli "agent pisze, użytkownik uruchamia".
