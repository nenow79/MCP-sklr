---
type: Procedure
title: Aktualizacja i odbiór sklr na Windows
description: Bieżąca procedura Windows po Linux M1, ochrona danych lokalnych i bramki fake, FlowAgent oraz safe-edit.
tags: [sklr, persistent-stdio, windows, testing]
---
# Aktualizacja i odbiór sklr na Windows

## Bieżący zakres

Następny etap to platformowy odbiór persistent stdio manual-first M1. Linux L1–L4 zakończono na bramce LINUX-M1-READY; nie jest to potwierdzenie produkcyjne Windows ani FlowAgent. Wyniki i ograniczenia: [CURRENT](plan/CURRENT.md). Jedyna lista otwartych zadań: [backlog](plan/backlog.md), sekcja persistent MCP-P3a/P3b/P3c/P4/P5. [Prompt do przekazania agentowi](continue-work.md) określa zakres następnej sesji i zasady zgód.

Dawne instrukcje autonomii, migracji OKF i diagnostyki D14/D15 przeniesiono do [archiwum](archive/windows-handoff-before-m1-2026-11-08.md). Nie uruchamiaj ich jako bieżącego planu. Ten dokument opisuje procedurę, nie udziela zgody na jej wykonanie.

## Wersja źródłowa

Użytkownik zatwierdził publikację do origin/main w git@github.com:nenow79/MCP-sklr.git. Checkpoint annotated: checkpoint/m1-linux-ready-20261108. Baza: checkpoint/m1-linux-base-20261108, commit 1205c8755e43f9425c3791d1017629e959d30710. Końcowy hash odczytaj z taga i porównaj z raportem publikacji oraz zdalnymi refs.

## Aktualizacja checkoutu

1. Sprawdź git status, branch, HEAD, remote i lokalne/zdalne tagi. Zachowaj lokalne zmiany; nie wykonuj reset --hard, force, stash/clean ani nadpisania istniejącego taga.
2. Zachowaj poza checkoutem własny config.json i potrzebne dane runtime: bazy zadań/sesji/autopilota, wiedzę/cache z kluczem i inne pliki według rzeczywistej konfiguracji. Zweryfikuj kopię bez wypisywania sekretów. Spójną kopię aktywnych baz uzgodnij z działającą instancją; nie kopiuj w ciemno otwartego SQLite bez plików WAL. Zatrzymanie/restart produkcyjnych procesów wymaga zgody.
3. Sprawdź, czy Windows nadal śledzi config.json (`git ls-files -- config.json`). Jeśli tak, zatrzymaj aktualizację i uzgodnij migrację po sprawdzeniu zewnętrznej kopii. Dawny skrypt jest wyłącznie w archiwum; nie przywracaj automatycznie configu z Git. Nowy kandydat nie powinien śledzić tego pliku.
4. Pobierz origin/main oraz dwa konkretne tagi. Przykładowe polecenia poniżej służą tylko do fetch i kontroli; sprawdź powodzenie każdego kroku. Konflikt istniejącego taga wymaga wyjaśnienia, nie --force.

```powershell
git fetch origin main
git fetch origin tag checkpoint/m1-linux-base-20261108
git fetch origin tag checkpoint/m1-linux-ready-20261108
git rev-parse 'checkpoint/m1-linux-ready-20261108^{}'
git ls-remote origin refs/heads/main 'refs/tags/checkpoint/m1-linux-ready-20261108^{}'
```

5. Przy czystym checkout main i uzgodnionym stanie można wykonać `git merge --ff-only origin/main`. Przy lokalnych commitach, innym branchu lub zmianach najpierw uzgodnij sposób przeniesienia. Odmowa fast-forward nie jest powodem do resetu.
6. Porównaj HEAD z checkpointem. Późniejsze commity na main wymagają sprawdzenia dodatkowego zakresu. Potwierdź obecność nowych plików i zachowanie lokalnej konfiguracji. Nie przenoś z Linuksa venv, configu, baz, wiedzy ani sekretów.
7. Kod na dysku nie aktualizuje działającego procesu MCP. Odnotuj, której wersji używa uruchomiony sklr; produkcyjny restart i zmiany flag/profili uzgodnij osobno.

## Środowisko i wykonanie testów

Przeczytaj pełne obowiązujące AGENTS przez dostępny sklr/prepare_project. Użyj dostępnych skilli i test-runner; wyjątek od test-runner dotyczył tylko Linuksa. Sprawdź rzeczywisty interpreter Windows, wersje i runner w istniejącym środowisku. Python 3.14 jest celem; historyczna wersja Windows nie dowodzi bieżącej konfiguracji. Brak środowiska/skilla zgłoś jako blokadę, bez automatycznej instalacji.

Wykonuj wyłącznie zatwierdzoną paczkę i jej skutki. Testy muszą używać izolowanych fake, tymczasowych config/SQLite i lokalnej komunikacji; procesy i porty testowe należy posprzątać. Nie uruchamiaj produkcyjnego main/uvicorn na potrzeby testów. Wyniki Linux ani pominięcia platformowe nie zastępują odbioru Windows; nie podnoś timeoutów i nie wyłączaj testów tylko dla GREEN.

## Lokalny odbiór M1 przed FlowAgent

Dobierz małe paczki z zakresów CURRENT i backlogu zamiast uruchamiania całego repo na start:

- Ścieżki Windows, spacje/junctiony, spawn i partial entry; teardown potomków, Job Object/nested jobs, bounded cleanup i broken bez zastępczego procesu.
- Discovery i call admission, pauza/end/zmiany profilu, owner/generation, wyniki/cache/TTL oraz shutdown przed zamknięciem magazynów. Zachowaj właściwą kolejność blokad i brak I/O pod globalnymi lockami.
- Model API → TaskManager → uwierzytelnione osobne zgody → SDK → lokalny fake: preview/apply zachowują PID/generację/token RAM. One-shot jako kontrola utraty tokenu. Nie jest to zdalny zapis Power Automate.
- Panel: wybór stdio_mode i rewizja/409, brak spawn po save/enabled/list, bieżący runtime oddzielony od last_test. Admin inspect jest osobnym one-shot, również przed discovery, bez zmiany roboczego PID/generacji/tokenu/cache/idle.

M1 wymaga aktywnej przypiętej WorkSession w trybie ręcznym. Każdy persistent call wymaga osobnego Allow mimo trust; zaangażowany autopilot, także zawieszony, blokuje M1. Nie włączaj delegacji do tego odbioru. expected_runtime_generation pochodzi z własnego katalogu, bez fallback/replay/reconnect starego call. Po commit drain w pierwotnym limicie; błąd/timeout/cancel po dopuszczeniu jest *_uncertain, bez ponowienia mutacji.

Zapisuj w CURRENT system/interpreter, komendę/runner, zakres, liczby passed/failed/skipped, przyczyny pominięć, exit code i potwierdzenie cleanup. Ratunkowe zabicie pozostałego procesu nie oznacza zaliczenia sprzątania. Otwarte zadania zapisuj wyłącznie w backlogu. Zmiany kodu wymagają planu i akceptacji, potem RED → minimalna implementacja → GREEN i właściwa regresja.

## Następne bramki

Po lokalnym odbiorze Windows zatrzymaj się przed rzeczywistym FlowAgent. Osobno uzgodnij konkretny profil, wersję, transport, środowisko, zmianę trybu i skutki testu ciągłości. Nie zmieniaj rzeczywistych profili ani flag i nie restartuj produkcji na podstawie samej procedury.

Zapis flow jest zablokowany do osobnego P5: wskazany bezpieczny flow/środowisko, validation/preflight → preview → zgoda → apply → backup/readback. Odczytowy PoC nie odbiera edycji. Run/publish/resubmit, C2 autopilota i zmiany SKILL.md wymagają osobnego uzgodnienia. Linux process group nie gwarantuje cleanup po SIGKILL sklr; nie deklaruj na tej podstawie gwarancji Windows/nested jobs.
