---
type: Reference
title: Historia i decyzje przeglądu backlogu sklr
description: Skrót historii usuniętej z aktywnego backlogu oraz decyzje o ograniczeniu zakresu dalszych prac.
tags: [sklr, backlog, archive, review]
---
# Przegląd backlogu — historia i decyzje

Ten dokument zachowuje skrót historycznych wpisów, nie pełną kopię poprzedniego backlogu. Nie jest listą prac ani zgodą na wykonanie. Aktualne zadania: [backlog](../plan/backlog.md); stan i szczegółowe potwierdzenia: [CURRENT](../plan/CURRENT.md). Wcześniejsza historia: [przegląd 2026-10-21](state-review-2026-10-21/backlog.md).

## Zakończone zakresy usuwane z aktywnej listy

- A2b3: zakończony przegląd wyników i granic jako wejście do A2b4.
- A2b4: implementacja i walidacja lokalna; dedykowane 48 passed, szersza regresja 941 passed, DOM Node 4/4. Raport: [A2b4](state-review-2026-10-21/A2b4.md). Odbiór rzeczywistego autopilota pozostaje odrębny.
- W1: config.example.json, ignorowanie lokalnej konfiguracji i procedura migracji z kopią poza checkoutem; config.json zachowano lokalnie. W2: przypadkowy wydruk diff-stat wyłączono ze śledzenia, lokalna kopia zachowana. W3: ówczesne kontrole Linux, pip check, 941 passed oraz 277 passed/2 skipped i Node 8/8. Nie dowodzi to wykonania wszystkich kroków migracji Windows.
- D7: poprawiono atrapy KNOWLEDGE/after_write i kontrolę tożsamości callbacku, zachowując produkcyjny hook OKF.
- D8: normalizacja wyjątku clock do bezpiecznego DocumentError, oba porządki wątków, cleanup i ponowne użycie workera, bez zwiększenia timeoutów.

## D14/D15 — zastąpione punkty przekazania

Pierwotny D14: Windows na checkoutcie 2a182bf, 17 failed/993 passed/1 skipped w 3277.31 s; brak dokładnej komendy i wersji środowiska. Błędy obejmowały oczekiwanie autopilota/knowledge_sessions, puste wyniki cached search oraz starą asercję tab-roots. Późniejsza paczka Linux: 7 passed, D14 112 passed, szerszy zakres 934 passed. Poprawiono asercje etapów, synchronizację, część cleanup i przygotowanie retencji; dodano opt-in diagnostykę.

D15 po 58c7986: Windows 20 failed/3231 passed/31 skipped w 4960.54 s. Źródło: [oczyszczony zrzut](../full_tests_20261224.txt); nazwa nie potwierdza daty, timestampy wskazywały 2026-10-24. Grupy błędów: oczekiwanie/synchronizacja, D7/D8, external MCP, stary skan po zapisie oraz search OKF. Lokalne naprawy na e6e27d7: 13 passed, cztery moduły 43 passed, powiązana regresja 178 passed i wszystkie 20 nodeid jako 23 warianty zielone z diagnostyką oraz bez niej. Szczegóły w CURRENT.

Odbiór nadrzędny 2026-10-26, baza 5417e4c z poprawką telemetry.py i pięcioma testami: pełne Windows **3260 passed, 31 skipped/239.67 s, bez failed i teardown errors**. Przed poprawką: 20 failed/3235 passed/31 skipped/1 teardown error w 5301.12 s. Pełna kolekcja z czterema reprezentatywnymi testami odtworzyła spowolnienie (1 failed/3 passed w 114.50 s); po naprawie 4 passed/5.21 s. Telemetria 11 passed; osiem modułów plus telemetria 184 passed/1 skipped. Node panelu wcześniej: 99 passed.

Przyczyną odtworzonego spowolnienia była aktywna pętla telemetrii przy pustej kolejce. Zachowano batching, trwałość SQLite, budżety, timeouty, zgody i zakaz retry niepewnych skutków. Stare polecenia ponownego odtwarzania D14/D15 nie są aktualnym zadaniem. Nowa obserwacja regresji wymaga nowej diagnozy. Ręczny panel/harness, D6/POSIX, luki cleanup D16 i scenariusze pominięte pozostają osobnym zakresem.

31 skip: 10 brak pandas, 5 POSIX process groups, 1 POSIX mode, 1 case-sensitive filesystem, 14 symlink/link unavailable. Nie oznacza to odbioru junctionów ani zgody na instalację lub zmianę uprawnień.

## Decyzje użytkownika podczas przeglądu

Backlog ma zawierać wyłącznie dług: prace potrzebne do wykonania, niekoniecznie teraz. Historia i zakończone zakresy nie pozostają checkboxami aktywnej listy. Scalenie wpisów nie oznacza rezygnacji z ich otwartych kryteriów.

- D5: usunąć historyczne działania poza repo (cost-centers po vibecoder-confirm=false, dev frontmatter/task #109, .ps1 w skill-writer, ręczny deploy git-ops). Rezygnacja z aktywnych wpisów, nie potwierdzenie ich wykonania i nie polecenie usuwania zewnętrznych plików.
- Diagnostyka Python/SDK: realizować minimum, proponowana forma małego skilla. Wersja Pythona/platforma i wersje jawnie wskazanych pakietów przez importlib.metadata, bez importowania ich implementacji, instalacji, sieci, analizy symboli/kodu lub ujawniania zmiennych środowiskowych. Osobny plan przed implementacją.
- Nie rozszerzać audytu/konwertera na dowolne katalogi. Zakres docs/ z podkatalogami; istniejących kontraktów legacy nie usuwać w ramach porządków.
- okf-curator: zachować jako niski priorytet zgodnie z konwencją kuracji. Semantyczny selektor stanu i hard guard odłożyć do wykazania konkretnej potrzeby.
- Ogólny execute_command: zrezygnować z realizacji. Ograniczony runner testów A3b pozostaje potrzebny autopilotowi; wymaga kontraktu, profilu i uprawnień, nie dowolnego shella.
- Duplikat propozycji oczekiwania 8 s scalić z P8. Uzgodnione ręczne oczekiwanie do 6 s nie jest jeszcze wdrożone; budżet autopilota nadal wymaga uzgodnienia.
- A3/A3b/A4 i P5: jeden zakres testów/poprawek z odrębnymi etapami; Git jako jawny profil działań.
- D1/D10/D13 i P2: wspólnie porządkować wydajność/aktywację, rozróżniając zgłoszenie, istniejącą poprawkę i pozostały odbiór.
- W4/W5: zweryfikować pozostały zakres względem późniejszych publikacji i testów; nie uznawać publikacji za wykonaną bez sprawdzenia Git/remote.

Zgody dotyczą porządków dokumentacyjnych i kierunku backlogu. Nie uruchamiają testów, serwerów, publikacji ani implementacji funkcji.
