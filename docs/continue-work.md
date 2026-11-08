---
type: Procedure
title: Kontynuacja sklr na Linuksie — handoff dla Codexa
description: Start po #2880, zakres Linux manual-first, lokalne testy bez skilli i checkpoint przed odbiorem Windows/Power Automate.
tags: [sklr, handoff, persistent-stdio, context, linux, codex]
---
# Kontynuacja na Linuksie i powrót na Windows

## Cel i źródła prawdy

Rozwijamy opt-in persistent stdio external MCP, docelowo dla Power Automate. Linux służy do implementacji i lokalnego odbioru manual-first M1. Windows pozostaje systemem docelowym i miejscem odbioru platformowego oraz rzeczywistego FlowAgent. C2 autopilota jest poza ścieżką krytyczną; P5 safe-edit osobno.

Ten dokument jest procedurą i promptem startowym, nie dziennikiem wyników ani samodzielną zgodą wykonawczą. Aktualny stan: [CURRENT](plan/CURRENT.md), jedyny backlog: [backlog](plan/backlog.md), wymagania: docs/specs/. Jeśli kod lub CURRENT zmieniły się po punkcie startowym, obowiązuje świeży stan. Nie czytaj archiwów ani całych planów na zapas i nie kopiuj tutaj wyników kolejnych sesji.

## Środowisko Linux — bez lokalnych skilli użytkownika

- Codex pracuje bezpośrednio w lokalnym checkout przez narzędzia swojego harnessu. Nie zakładaj działającego sklr MCP, profili FlowAgent, dev-skills, power-automate ani test-runner. Ich brak nie blokuje lokalnego developmentu.
- Przeczytaj pełne AGENTS.md i zagnieżdżone instrukcje dla katalogów pracy. Jeśli sklr MCP jest dostępny, można użyć prepare_project i selektywnego OKF; inaczej użyj zwykłego odczytu plików i potrzebnych zakresów Markdown. Nie uruchamiaj serwera sklr tylko dla odczytu dokumentacji.
- W tym handoffie użytkownik przewiduje wyjątek od wymagania skilla test-runner: po wyraźnym poleceniu startowym Codex wykonuje pytest bezpośrednio w lokalnym środowisku Linux. Wyjątek dotyczy sposobu wykonania testów, nie reguł bezpieczeństwa, TDD ani produkcyjnych zgód WorkSession. Nie zmieniaj globalnego AGENTS.md przy okazji.
- Sprawdź dostępny interpreter, zależności i konfigurację projektu. Docelowa zgodność obejmuje Python 3.14; dotychczasowe wyniki Windows były na 3.14.7. Raportuj faktyczną wersję Linux i ewentualną różnicę. Nie zgaduj ścieżki interpretera; uruchamiaj zatwierdzony interpreter z istniejącego venv/conda przez `python -m pytest`.
- Nie używaj ścieżek, executorów ani venv z Windows. Brak środowiska/zależności to blokada do uzgodnienia; instalacja lub tworzenie środowiska wymaga osobnej zgody. Nie kopiuj config.json, baz zadań, lokalnej wiedzy, poświadczeń ani sekretów między systemami. Testy używają tmp config/SQLite i fake danych.
- Bez dostępu do sklr nie wywołuj fikcyjnych get_session_state/list_tasks. Sprawdź lokalne uruchomione zadania testowe dostępnymi narzędziami harnessu i nie dubluj przebiegów. Jeżeli korzystasz ze sklr, obowiązują jego rzeczywiste bramki i polityka zadań.

## Punkt startowy M1-L po #2880

PersistentLifecycle w external_mcp/persistent_lifecycle.py został zapisany, ale niepodłączony i nieodebrany. Worker jest lazy, ma kolejkę RAM 64, pins owner/profile/generation i connection digest, deduplikację, failure latch i close(). Nie twórz ponownie istniejącego pliku. Według punktu handoff po #2880 nie wykonano testów; sprawdź świeży CURRENT przed wyciąganiem wniosków.

Istnieją PersistentSupervisor.invalidate_generation(), SessionController.add_lifecycle_listener oraz RAM-only hooki pause/end/shutdown bez odbiorcy. ConfigStore ma add_change_listener — wykorzystaj go, nie dodawaj drugiego systemu w rejestrze. ExternalMCPTools wymaga podłączenia lifecycle, track, kontroli blocked i close_lifecycle.

Adapter M1 odebrano tylko w ograniczonym zakresie na atrapach. Sześć testów M1-L nie jest pełnym odbiorem lifecycle ani wyścigów. Historyczne wyniki Windows nie dowodzą działania na Linuksie; lokalna pierwsza celowana weryfikacja przenośności jest uzasadniona.

## Prompt do wklejenia Codexowi na Linuksie

Poniższy prompt przekazuje aktualną zgodę dopiero po wysłaniu go przez użytkownika w nowej sesji. Sam odczyt pliku nie uruchamia pracy ani Git.

```text
Kontynuuj sklr zgodnie z docs/continue-work.md, od świeżego stanu M1-L po #2880.
Celem jest lokalny odbiór M1 manual-first na Linuksie i checkpoint Git przed
powrotem na Windows. Nie ma moich lokalnych skilli ani wymaganego sklr MCP.

Zezwalam na bezpośrednie lokalne testy pytest w istniejącym środowisku Linux,
bez skilla test-runner: testy logiki oraz lokalny fake stdio przez SDK, w tym
procesy potomne fake i komunikację lokalną niezbędną do testów API. Bez sieci
zewnętrznej, rzeczywistych serwerów MCP, tenanta ani operacji FlowAgent.
Nie uruchamiaj produkcyjnego sklr/uvicorn; ewentualny testowy lokalny serwer
musi używać izolowanej konfiguracji, portu i danych oraz być sprzątnięty.

Najpierw potwierdź aktualny stan, środowisko, branch/remote, plan pierwszej
paczki i blokady. Przed każdą paczką zmian kodu przedstaw konkretny plan i
uzyskaj moją akceptację. Potem TDD: RED → minimalny kod → GREEN → celowana
regresja. Historyczny RED może być punktem odniesienia, nie wynikiem Linux.
Zgoda na opisane testy nie obejmuje instalacji ani zmian środowiska.

Na końcu zatwierdzonych prac zezwalam na commit naszych zmian, push uzgodnionej
gałęzi i nowego annotated tagu checkpointu do potwierdzonego remote.
Nie pushuj bez sprawdzenia dokładnego zakresu diffu i destination.
Nie obejmuj cudzych zmian, nie wykonuj force push, reset --hard, automatycznego
stash/clean ani nadpisania istniejącego tagu. Jeśli branch/remote lub zakres
zmian jest niejednoznaczny, zapytaj. Obowiązuje procedura Git z tego dokumentu.

Zatrzymaj się na bramce LINUX-M1-READY opisanej niżej albo na blokadzie.
Nie włączaj rzeczywistych profili persistent. C2 i P5 pozostają poza zakresem.
```

## Selektywny przegląd i kolejność paczek Linux

Przeczytaj sekcje persistent/M1/M1-L i blokady w CURRENT, MCP-P3c w backlogu oraz „Pierwszy odbiór M1 — manual-first” i potrzebne owner/generation/dispatch/lifecycle w [kontrakcie persistent](specs/external-mcp-persistent-stdio-proposal.md). WorkSession i v1 doczytuj tylko przy istotnej zależności. Kod poznawaj symbol → zakres → zależności.

### L1 — minimalny wiring M1-L

Przejrzyj potrzebne zakresy:
- external_mcp/persistent_lifecycle.py;
- server/external_mcp_tools.py: __init__, _manual_owner, _persistent_list/_persistent_call;
- config_store.py: add_change_listener, _changed, update;
- tasks/session_controller.py: hooki pause/end/shutdown;
- external_mcp/persistent_supervisor.py: snapshot/invalidate_generation;
- tests/external_mcp_persistent_manual_lifecycle_helpers.py i odpowiadające testy.

Uzgodnij track kontra pause/config, retencję starych pins, overflow/failure latch, zamknięcie i brak gubienia cleanup. Spóźnione zdarzenie nie może unieważnić nowej generacji. Test podmienia tools.persistent_supervisor po konstrukcji: ustal bezpieczne wstrzyknięcie, nie implementuj polityki produkcyjnej w helperze. GREEN całej szóstki i celowana regresja; nowe luki wymagają własnych testów RED.

### L2 — niezbędne granice i lifecycle manual-first

Domknij zsynchronizowaną granicę discovery/spawn, call commit/drain/races, end/config/mode/alias, właściciela wyników/TTL i shutdown ordering. Przed każdą paczką wskaż kryteria, testy i minimalny zakres. Obecny before_start pod lockiem supervisora nie nadaje się do wywołań manager/controller. Nie uznawaj sześciu testów L1 za odbiór tych luk.

### L3 — main/model API i lokalny odbiór SDK/fake

Podłącz produkcyjny routing z wymaganym expected_runtime_generation oraz zamknięciem lifecycle. Odbierz lokalną ścieżkę model API → TaskManager → SDK → fake stdio: discovery, dwa calls z osobnymi zgodami, ta sama generacja i token RAM; one-shot jako kontrola utraty tokenu. Używaj prawdziwych mechanizmów zgód/owner, nie testowego obejścia admission. Odbierz odmowy i utratę generacji w krytycznych punktach oraz celowaną regresję dotkniętych ścieżek.

### L4 — minimalny panel opt-in

Odbierz minimalny panel z oczekiwaną rewizją, oddzielnym admin one-shot i brakiem spawn przy zapisie/enabled. Wyłącznie izolowane profile fake. Nie przełączaj rzeczywistego flowagent ani innych profili użytkownika.

## Niezmienne wymagania i zakres wyłączony

M1: aktywna przypięta WorkSession, osobne Allow dla każdego persistent call mimo trust; fail-closed przy zaangażowanej delegacji autopilota, także zawieszonej. Pusty ContextVar/brak ticketu nie dowodzi manual-first. Discovery bez osobnego okna zgody, lecz z enabled/sesją i końcowymi kontrolami. Błąd callbacku nie zastępuje niezależnego recheck przed dispatch.

Callbacki pod session/config lock tylko krótkie RAM, bez manager/supervisor/I/O. Worker bez manager/controller/config locków. Kolejność manager → controller → stores → supervisor; żadnych globalnych locków podczas I/O, slotu, walidacji i cleanup. Po dopuszczeniu call drain zgodnie z kontraktem, uncertain bez retry. Bez replay/reconnect ze starą generacją i bez fallbacku; persistent_runtime_required pozostaje. One-shot/HTTP/SSE i zwykłe skille bez zmiany polityki.

Nie usuwaj testów Windows ani nie osłabiaj ich, aby Linux był zielony. Oznaczenia platformowe muszą mieć rzeczywiste uzasadnienie. Pominięcie nie jest odbiorem. C2, rzeczywisty FlowAgent, preview/apply, run/publish/resubmit, upstream i SKILL.md poza zakresem.

## Bramka zatrzymania: LINUX-M1-READY

Zakończ Linux po L1–L4: manual-first jest spięty, celowane testy logiki/wyścigów i lokalna ścieżka API/SDK/fake oraz minimalny panel mają potwierdzone wyniki Linux. Nie wymaga to C2 ani P5. Nie jest to deklaracja gotowości produkcyjnej Windows lub odbioru Power Automate.

W CURRENT zapisz zwięźle faktyczne środowisko, zakres/komendy, liczby passed/failed/skipped, powody skipów, wynik lokalnego fake i granice odbioru. W backlogu pozostaw konkretne luki i odbiór Windows/P4/P5. Nie używaj verified dla niewykonanych zakresów. Jeśli bramka nie jest osiągnięta, raportuj INCOMPLETE i dokładny następny krok; tag checkpointu nie może sugerować READY.

## Git — punkt powrotu i przekazanie

1. Sprawdź git status/diff, bieżący branch, remote i jego URL, lokalne/zdalne tagi. Ustal destination z użytkownikiem, jeśli nie jest jednoznaczne. Nie zakładaj origin/main i nie przełączaj gałęzi z cudzymi zmianami.
2. Przed zaakceptowanymi zmianami kodu przypnij bazowy commit annotated tagiem, np. `checkpoint/m1-linux-base-YYYYMMDD`. Zapisz hash i informację o istniejących zmianach. Tag obejmuje tylko commit, nie niezacommitowany worktree; nie deklaruj kompletnego backupu przy brudnym drzewie. Uzgodnij zachowanie niezapisanych zmian bez automatycznego commitowania/stashowania.
3. Pracuj na uzgodnionej gałęzi roboczej, preferencyjnie `work/m1-linux`. Commituj wyłącznie nasze zatwierdzone zmiany, po kontroli diffu i braku sekretów/danych lokalnych; krótkie Conventional Commits. Nie używaj bezrefleksyjnego git add -A.
4. Po aktualizacji CURRENT/backlogu i końcowym przeglądzie utwórz nowy annotated tag: `checkpoint/m1-linux-ready-YYYYMMDD` tylko po osiągnięciu bramki, inaczej `checkpoint/m1-linux-incomplete-YYYYMMDD`. Istniejącego tagu nie przesuwaj; wybierz unikalną nazwę.
5. Pushuj jawnie uzgodnioną gałąź i tylko te konkretne tagi, bez force i bez `--tags`. Sprawdź wynik i zgodność zdalnych refs z lokalnymi hashami. Nie ponawiaj push w ciemno po niepewnym wyniku; najpierw sprawdź refs.
6. W końcowym raporcie podaj remote/branch, bazowy i końcowy tag, commit hash, faktyczny wynik push, testy i następny krok Windows. Nie zapisuj końcowego hasha w commicie, którego hash miałby się przez to zmienić.

Tag bazowy umożliwia odczyt starego stanu lub osobny worktree/branch. Nie nakazuj reset --hard. Przy cofnięciu zmian na współdzielonej gałęzi uzgodnij revert zamiast przepisywania historii.

## Powrót tutaj — odbiór Windows i Power Automate

- Najpierw sprawdź lokalne zmiany Windows. Pobierz uzgodnioną gałąź i konkretne tagi bez nadpisywania configu/venv/baz/sekretów; wybór merge/cherry-pick/checkout uzgodnij przy rozbieżności. Sprawdź hash względem raportu Linux.
- Odczytaj świeże AGENTS/CURRENT/backlog i odnów zgody. Powrót do sklr oznacza używanie dostępnych skilli i test-runner zgodnie z lokalnymi zasadami; wyjątek Linux nie przenosi się na Windows.
- Wykonaj celowany odbiór Windows: ścieżki/junctiony, spawn/teardown, cleanup potomków i nested jobs, shutdown oraz regresję nowej paczki i lokalną ścieżkę fake przez docelowe API/SDK. Potwierdź bieżący interpreter i lokalną konfigurację. Wyniki Linux/skipy nie zastępują tego odbioru.
- Dopiero po odbiorze lokalnym osobno uzgodnij włączenie persistent dla rzeczywistego FlowAgent i test ciągłości. Zapis flow nadal zablokowany do osobnego P5: wskazany bezpieczny flow/środowisko, validation/preflight → preview → zgoda → apply → backup/readback. Odczytowy PoC nie odbiera edycji. Run/publish/resubmit osobno.
