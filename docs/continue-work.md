---
type: Procedure
title: Kontynuacja sklr na Windows — handoff po Linux M1
description: Prompt odbioru Windows po LINUX-M1-READY, aktualizacja z origin/main i rozdzielenie lokalnych fake od FlowAgent oraz P5.
tags: [sklr, handoff, persistent-stdio, context, windows, testing]
---
# Przekazanie sklr na Windows po Linux M1

## Cel i źródła prawdy

Linux L1–L4 zakończono na bramce LINUX-M1-READY. Nie rozpoczynaj ponownie wiring M1-L po #2880: lifecycle, admission discovery/call, wyniki/TTL, shutdown, main/model API i minimalny panel są już podłączone. Zakres i wyniki pozostają wyłącznie w [CURRENT](plan/CURRENT.md), otwarte zadania w [backlogu](plan/backlog.md). Wymagania: [kontrakt persistent stdio](specs/external-mcp-persistent-stdio-proposal.md) oraz istotne sekcje kontraktów external MCP i WorkSession. Obowiązuje świeży kod i stan, nie historyczny checkpoint.

Celem następnej sesji jest odbiór platformowy Windows/P4 i przygotowanie osobno uzgodnionego testu ciągłości rzeczywistego FlowAgent. C2 autopilota i P5 safe-edit są poza tą bramką. Wyniki Linux nie oznaczają gotowości produkcyjnej Windows ani odbioru Power Automate.

## Wersja i bezpieczne przeniesienie

Użytkownik wskazał **origin/main** w repozytorium git@github.com:nenow79/MCP-sklr.git. Bazowy annotated tag: checkpoint/m1-linux-base-20261108, commit 1205c8755e43f9425c3791d1017629e959d30710. Docelowy annotated checkpoint: checkpoint/m1-linux-ready-20261108. Końcowy hash sprawdź przez odczyt taga; nie wpisujemy go do commita, którego hash miałby się przez to zmienić.

1. Sprawdź status, bieżący branch/HEAD, remote oraz lokalne i zdalne refs. Zachowaj cudze zmiany i lokalny config/venv/bazy/wiedzę/sekrety. Nie kopiuj tych danych z Linuksa.
2. Pobierz main i konkretne tagi. Nie używaj force, reset --hard, stash/clean ani automatycznego nadpisywania. Przy rozbieżnym main lub zmianach lokalnych uzgodnij sposób aktualizacji; nie wybieraj w ciemno merge/rebase/cherry-pick.
3. Dla czystego checkout main możliwa jest aktualizacja --ff-only po potwierdzeniu refs. Sprawdź, czy HEAD odpowiada checkpointowi, czy zawiera późniejsze zmiany. Rozbieżność wymaga przeglądu zakresu, nie pominięcia kontroli.
4. Zweryfikuj obecność nowego kodu i dokumentów na dysku. Jeżeli działający sklr pochodzi ze starego procesu, odnotuj to; restart produkcyjnej instancji, lokalne flagi i profile wymagają osobnego uzgodnienia.

[Procedura Windows](windows-okf.md) opisuje ochronę lokalnej konfiguracji i szersze scenariusze. Nie wykonuj automatycznie jej wszystkich historycznych paczek.

## Prompt do wklejenia agentowi na Windows

To treść przyszłego polecenia użytkownika. Sam odczyt tego dokumentu nie udziela zgody na testy, zmianę konfiguracji, restart, Git ani FlowAgent.

```text
Kontynuuj MCP sklr na Windows według docs/continue-work.md, po LINUX-M1-READY.
Źródłem jest origin/main, checkpoint/m1-linux-ready-20261108; sprawdź remote,
HEAD i tag przed dalszą pracą. Najpierw zachowaj lokalne zmiany i konfigurację.
Nie kopiuj z Linuksa venv, config.json, baz, lokalnej wiedzy ani sekretów.
Przy rozbieżności Git przedstaw bezpieczny sposób aktualizacji do akceptacji.

Pracuj przez dostępny sklr. Zacznij od prepare_project dla katalogu pracy,
czytaj obowiązujące AGENTS w całości i doczytaj skrócone instrukcje. CURRENT
czytaj tematycznie: persistent/M1 i Linux L1–L4; backlog: MCP-P3a/P3b/P3c/P4/P5.
Nie ładuj hurtowo docs ani archiwów. Używaj dostępnych skilli; list_skills,
activate_skill i test-runner zgodnie z instrukcjami. Wyjątek bez test-runner
obowiązywał tylko na Linuksie. Brak narzędzia/skilla zgłoś, nie obchodź go.

Zezwalam na lokalne testy odbiorcze Windows w istniejącym środowisku przez
właściwy test-runner: logika/wyścigi, API/panel/SDK z izolowanymi profilami
fake, tymczasową konfiguracją i SQLite, lokalne procesy fake wraz z potomkami
oraz komunikacja lokalna niezbędna tym testom. Najpierw przedstaw konkretną
pierwszą paczkę i sprawdź interpreter, zależności, runner, zakres i skutki.
Bez zewnętrznej sieci, rzeczywistych serwerów MCP, tenanta i operacji FlowAgent.
Nie uruchamiaj produkcyjnego main/uvicorn. Każdy serwer testowy ma izolowane
porty i dane oraz potwierdzony cleanup. Nie instaluj zależności ani środowisk.

Zacznij od celowanego zakresu CURRENT/L4 i dotkniętych ścieżek Windows:
ścieżki ze spacjami/junctiony, spawn/partial entry, teardown potomków,
Windows Job Object/nested jobs, shutdown przed zamknięciem magazynów.
Odbierz API→TaskManager→osobne zgody→SDK→fake: discovery i preview/apply
w tej samej generacji; one-shot jako kontrolę utraty tokenu. Sprawdź panel:
tryb/revision/409, brak spawn po save/enabled, bieżący runtime vs last_test,
admin inspect jako oddzielny one-shot bez roboczego cache, generacji i idle.
Nie wyłączaj testów Windows ani nie podnoś timeoutów tylko dla GREEN.
Skip, zero testów i ratunkowe zabicie pozostałego procesu nie są odbiorem.

Przed każdą paczką zmian kodu przedstaw plan i uzyskaj moją akceptację.
Dla błędów: test regresji RED, minimalna implementacja, GREEN i właściwa
regresja. Raportuj komendę, system/interpreter, liczby passed/failed/skipped,
exit code, skutki i cleanup. Bieżące wyniki zapisuj w CURRENT, otwarte zadania
wyłącznie w backlogu. Nie nadawaj verified niewykonanym zakresom.

Po lokalnym odbiorze Windows zatrzymaj się i zaproponuj osobną paczkę
ciągłości rzeczywistego FlowAgent z dokładnym profilem/wersją/transportem,
środowiskiem i skutkami. Nie włączaj rzeczywistych profili persistent,
nie zmieniaj flag/configu i nie restartuj produkcji bez osobnego uzgodnienia.
Nie wykonuj zdalnego zapisu flow. P5 wymaga osobnej zgody na bezpieczny flow,
validation/preflight→preview→zgoda→apply→backup/readback.
Run/publish/resubmit, C2 autopilota i zmiany SKILL.md poza zakresem.
Commit/push/tag po pracach Windows wymagają osobnego polecenia.
```

## Niezmienne granice M1

- Aktywna przypięta WorkSession i tryb ręczny; każdy persistent call wymaga osobnego Allow mimo trust. Zaangażowany autopilot, także zawieszony, blokuje M1. Nie włączaj delegacji dla odbioru tej ścieżki.
- Discovery wymaga enabled, sesji i aktualnych kontroli. Call wymaga expected_runtime_generation z własnego katalogu; nie przekazuj go do arguments upstream. Brak fallback/replay/reconnect starego call; persistent_runtime_required w kliencie one-shot pozostaje.
- Przed commit pauza/end/zmiana profilu blokuje call; po commit drain w pierwotnym limicie. Błąd/timeout/cancel po dopuszczeniu oznacza jawne *_uncertain, bez ponowienia mutacji. Poprawnego wyniku nie kasuje późniejsza awaria cleanup.
- Kolejność manager → controller → stores → RAM lifecycle → supervisor; callbacki tylko krótkie RAM, bez globalnych blokad przy I/O, slocie, walidacji lub cleanup. Spóźniona invalidacja nie może zamknąć nowej generacji.
- Wyniki/cache RAM mają TTL 300 s bez przedłużania przy odczycie; dostęp wyników sprawdza właściciela. Zapis/enabled/list/panel nie uruchamiają procesu ani nie odświeżają idle. Inspect nie tworzy roboczego cache ani sesji persistent.
- Sekrety i payloady nie trafiają do configu, SQLite, logów ani telemetrii sklr. POSIX process group nie zapewnia cleanup po SIGKILL sklr; Windows/nested jobs trzeba rzeczywiście odebrać. Nie deklaruj równej gwarancji platform.
- Przez sklr przestrzegaj polityki pojedynczego wait_task(seconds=6) na osobny task_id przy awaiting_approval, decyzji w panelu i dostępu poufnych wyników wyłącznie przez get_task. Allow nie oznacza sukcesu wykonania.

## Zamknięcie odbioru Windows

Zapisz w CURRENT zakres i ograniczenia, nie kopiuj tutaj wyników sesji. Przy blokadzie podaj konkretny następny krok. Linux LINUX-M1-READY pozostaje faktem historycznego odbioru Linux; nie zastępuje wyniku Windows. FlowAgent continuity, P5 safe-edit i C2 mają odrębne bramki. Nie wykonuj automatycznego ponowienia po niepewnym zdalnym skutku.
