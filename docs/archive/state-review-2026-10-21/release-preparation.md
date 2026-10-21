---
type: Test Report
title: Przygotowanie kandydata A2 do odbioru Windows
description: Zakres paczki A2b3/A2b4 i W1–W3, wyniki dodatkowej regresji oraz ograniczenia przed publikacją.
tags: [sklr, release, autopilot, windows, testing]
---
# Kandydat A2 — 2026-10-21

Baza: `425f91f2bd8499f33800c1fb8af3fa54b3af6749`, main.
Odczyt `git ls-remote origin refs/heads/main` potwierdził tę samą bazę.
Dokładny commit kandydata identyfikuje commit zawierający ten raport;
hash przekazania podaje wynik `git log -1 --oneline`. Publikacja wymaga osobnej decyzji.

## Zakres

- A2b3: runtime, metadane dziennika, generacje zasobów, adaptery i kontrola przy wykonaniu.
- A2b4: bootstrap opt-in, decyzje/API/UI panelu, handshake, watchdog i shutdown.
- W1: config.example.json, ignorowanie lokalnego configu i procedura migracji Windows.
- W2: przypadkowy wydruk diff-stat `kills)…` poza śledzeniem, zachowany lokalnie.
- W3: dostępne kontrole i wskazane regresje Linux, bez uruchamiania docelowego serwera.
- Dokumentacja stanu, kontraktów i dalszego odbioru; bez zmiany uprawnień kontraktu A2.

Nie przenosimy lokalnego config.json, baz, kluczy, venv, logów ani kopii configu.
Usunięcia config.json i wydruku dotyczą śledzenia Git; lokalne pliki zachowano.
Przeszukanie dodawanych/zmienionych plików pod kątem wzorców kluczy prywatnych,
tokenów GitHub/OpenAI i AWS nie wykazało dopasowań; heurystyka nie dowodzi braku
wszystkich sekretów. Dawne wersje configu pozostają w historii Git.

## Walidacja

Dedykowana paczka A2b4 48 passed oraz szersza regresja 941 passed są opisane
w [raporcie A2b4](A2b4.md); nie sumujemy nakładających się paczek.
Dodatkowa regresja wspólnych wykonawców i konfiguracji: **277 passed, 2 skipped
in 9.16s**. Skip: Windows junction w file_transaction i case-only rename w folder_tools.

```bash
venv/bin/python -m pytest -q tests/test_file_transaction.py tests/test_edit_tools.py tests/test_tree_tools.py tests/test_folder_tools.py tests/test_skills_runner.py tests/test_skills_dev.py tests/test_state.py tests/test_state_workspace.py --tb=short --durations=15 -o faulthandler_timeout=30
node --test tests/test_panel_*.cjs
venv/bin/python -m pip check
```

Pytest wykonano poza sandboxem w istniejącym venv. Node: **8/8 plików testowych**,
nie liczba pojedynczych asercji ani scenariuszy; runtime 3.82s. pip check:
No broken requirements found. AST lokalnie zmienionych/nowych plików Python
i git diff --check poprawne. W1 ma osobno potwierdzoną zgodność przykładu,
round-trip starego configu i zachowanie configu w tymczasowych repozytoriach Git.

Środowisko: Python 3.14.4/Linux, Node v22.22.1; FastMCP 4.1.3, MCP 2.3.0,
httpx2/httpcore2 2.16.0, Starlette 1.9.0, Uvicorn 0.54.0, pytest 9.1.1,
PyYAML 6.0.3, jsonschema 4.26.0, referencing 0.37.0.
Nie zmieniano requirements, nie instalowano zależności ani środowisk.

## Granice przekazania

To kandydat do odbioru A2 na Windows, nie ukończona autonomia A2–A4.
Pełnej regresji repo nie powtarzano; znany dług D6–D8 pozostaje w backlogu.
Nie odebrano rzeczywistego MCP/panelu, harnessu, migracji PowerShell ani Windows.
SDK/transportów nie powtarzano w W3. Sam pip check nie gwarantuje zgodności API
ani powtarzalności środowiska Windows przy niepełnym pinningu zależności.

Przed aktualizacją Windows wymagane zatrzymanie aplikacji i kopia configu poza
checkoutem według [procedury](../../windows-okf.md#aktualizacja-na-windows).
Ręczny odbiór A2 opisuje ta sama instrukcja. Włączenie sesji/autopilota pozostaje
jawną lokalną decyzją. Zadania otwarte wyłącznie w backlogu, aktualne potwierdzenia w CURRENT.
