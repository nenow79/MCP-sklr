---
type: Specification
title: "Opcjonalne utrzymywane stdio external MCP — zaakceptowany kontrakt"
description: "Zaakceptowane wymagania opt-in persistent stdio, generacji runtime, lifecycle, zgodności i etapowego odbioru; implementacja niepotwierdzona."
status: stable
tags: [sklr, external-mcp, stdio, sessions, approvals, power-automate]
---
# Opcjonalne utrzymywane stdio external MCP — zaakceptowany kontrakt

## Status i źródła prawdy

Użytkownik zaakceptował szczegółowy kontrakt i etapowy plan P1–P5 po przeglądzie aktualnego kodu. Niniejszy dokument określa wymagania docelowe; akceptacja nie oznacza implementacji, przełączenia profili ani odbioru działania. Bieżący zakres wykonawczy obejmuje P1: zapis uzgodnień w dokumentacji. Implementacja, uruchomienia testów oraz operacje Power Automate wymagają osobnych uzgodnień. Zapis flow pozostaje zablokowany do odbioru ciągłości sesji oraz preview/apply dla konkretnego profilu, wersji i transportu.

Dokument jest rozszerzeniem [external MCP v1](external-mcp-support.md), nie zastępuje pozostałych jego reguł. Stan wykonania i potwierdzenia: [CURRENT](../plan/CURRENT.md); jedyna lista otwartych prac i etapów: [backlog](../plan/backlog.md). Reguły sesji i zgód: [sesje pracy](work-session-pause.md), [zgody](files-and-approvals.md), [autopilot](autopilot-contract.md). Paczka power-automate w root dev-skills: docs/START.md i docs/implementation-plan.md czytane przez read_skill_resource; nie zakładać, że katalog skilla jest bundle OKF. Status stable dotyczy zaakceptowanych wymagań, nie zweryfikowanego runtime.

## Problem i baza

external_mcp/client.py otwiera transport i ClientSession w _execute na jedną operację, z osobnym anyio.run. Stdio startuje i sprząta proces. FlowAgent 3.2.10 trzyma tokeny preview w procesowym Map, domyślnie 5 minut, single-use, związane z env/flow/proposal i wersją flow. Rozdzielenie preview/apply na dwa procesy gubi token. Nie obchodzić tego zapisem bez preview ani przepisywaniem tokenów na dysk.

Odebrano mały read-only PoC FlowAgent przez rzeczywiste API sklr; zakres i historyczne wyniki pozostają w CURRENT i START skilla. Obsługa draft-07 i wybrana regresja nie są testami utrzymywanych sesji. Nie powtarzać odebranego PoC bez nowej przyczyny. Statyczny przegląd kodu nie jest testem działania; zdalne mutacje, preview/apply i pełny odbiór edycji pozostają niepotwierdzone.

## Zakres i kompatybilność

Pierwsza wersja obejmuje wyłącznie opt-in persistent stdio per profil. Pole stdio_mode przyjmuje one_shot albo persistent. Brak pola w dotychczasowym profilu oznacza one_shot. Pole dotyczy tylko stdio; dla HTTP/SSE jest odrzucane. Nowe profile są domyślnie wyłączone i jednorazowe. HTTP/SSE zachowują dotychczasowe zachowanie; persistent HTTP/SSE są poza zakresem.

Tryb wybiera wyłącznie użytkownik w panelu z oczekiwaną rewizją. Model nie ma narzędzia zmiany trybu, profilu, enabled, trust ani restartu runtime. Zapis konfiguracji, włączenie profilu i start sklr nie uruchamiają zewnętrznego procesu. Persistent wymaga aktywnej, przypiętej WorkSession; przy wyłączonych sesjach lub zamkniętej bramce odmawia, bez cichego fallback do one-shot i bez automatycznego włączenia sesji.

Zmiana obejmuje external MCP, panel i wiring WorkSession. Parser skilli, zwykłe skille, narzędzia plikowe i pozostałe wbudowane funkcje nie zmieniają zachowania. Nie rozszerzać uprawnień zgód ani delegacji autopilota. Węższa granica dispatch dla persistent jest jawnie opisana poniżej i w kontrakcie WorkSession. API pozostaje ogólne, nie specyficzne dla FlowAgent; zgodność wielokrotnych calls w jednej sesji wymaga odbioru konkretnej wersji serwera.

## Pierwszy odbiór M1 — manual-first

Zaakceptowany zakres pierwszego wdrożenia jest węższy od wymagań docelowych: persistent działa wyłącznie z aktywną, przypiętą WorkSession i poza zaangażowaną delegacją autopilota, także zawieszoną. Sam pusty ContextVar albo brak ticketu nie dowodzi trybu ręcznego. Serwer sprawdza ograniczenie przed submit/start oraz ponownie przy dopuszczeniu spawn/commit. Odmowa nie wyłącza autopilota, nie tworzy automatycznej ręcznej zgody i nie przechodzi do one-shot. Błąd kontroli pozostawia bramkę zamkniętą.

Każdy persistent tools/call w M1 wymaga osobnego Allow dla konkretnego zadania, także przy zapisanym trust. Trust nie autoryzuje calls w tej wersji. Discovery zachowuje autoryzację przez enabled profilu, bez osobnego okna zgody, ale podlega WorkSession, ograniczeniu autopilota i aktualnym kontrolom profilu. Oczekiwanie na Allow nie zajmuje slotu runtime ani workera; zgoda nie przedłuża generacji, katalogu ani tokenu preview. Ręczne Allow nie omija kontroli schema/profile/panel/owner/generation/cancel/deadline. Ścieżka approved omija wyłącznie trust i limiter zgodnie z istniejącą polityką ręcznych zgód; M1 nie wprowadza automatycznego reapproval persistent.

M1 obejmuje osobny routing discovery/call do supervisora, wymagane expected_runtime_generation, cache przypięty do właściciela i generacji, kontrolę właściciela wyników oraz lifecycle pause/end/profile/shutdown. Granica discovery/spawn musi zachować kolejność manager → controller → stores → supervisor; nie wolno wywoływać manager/controller z callbacku pod blokadą supervisora. Kontrole i lokalne dopuszczenie nie utrzymują globalnych blokad podczas I/O, slotu, walidacji lub cleanup. Call korzysta z resource-aware dispatch boundary, nie starego before_dispatch. Blokada persistent_runtime_required w kliencie one-shot pozostaje ochroną przed fallbackiem.

Integracja rzeczywistego scope i rozliczania autopilota (C2) jest odłożona, nie odebrana ani usunięta z wymagań docelowych. M1 nie implementuje wyjątku exhausted/expired. Kryteria P04/P05 dotyczące delegacji/trust/reapproval pozostają otwarte w tym zakresie; M1 wymaga testów odmowy autopilota i osobnego Allow także przy trust. One-shot/HTTP/SSE pozostają bez zmiany.

Włączenie opt-in wymaga odbioru ręcznego routingu/lifecycle, lokalnej ścieżki API → TaskManager → SDK → fake z dwoma calls i osobnymi zgodami oraz minimalnego panelu z rewizją i oddzielnym admin one-shot. Sam zapis kontraktu lub testy atrap nie autoryzują przełączenia profilu. P5 Power Automate safe-edit, instalacje, restarty i zmiany konfiguracji nadal wymagają osobnego uzgodnienia.

## Konfiguracja, migracja i trust

Rejestr czyta poprawne istniejące external_mcp v1 bez zapisu i bez zmiany zachowania. Przy pierwszej zatwierdzonej zmianie wykorzystującej nową strukturę zapisuje v2 przez ConfigStore.update, z dotychczasowym rollbackiem pamięci przy błędzie. Puste external_mcp={} nadal oznacza brak profili. Nie migrować nieznanych, uszkodzonych ani niepustych historycznych struktur w ciemno. Nie wykonywać połączenia w celu migracji.

Migracja zachowuje stabilne id, enabled i poprawne rekordy trust. Dotychczasowe rewizje konfiguracji i profilu nadal chronią przed konfliktem/ABA. Dla braku stdio_mode oraz one_shot connection_digest zachowuje dotychczasową wartość: jawne dodanie domyślnej wartości nie unieważnia zaufania. Dla persistent digest uwzględnia tryb. Zmiana one_shot ↔ persistent zeruje trust; powrót do poprzedniego trybu nie odtwarza usuniętego trust. Transport, command, args, cwd i env_refs zachowują dotychczasową semantykę połączenia.

Alias i description nie są tożsamością runtime ani zmianą połączenia. Ich edycja może zachować proces i trust, ale nadal zmienia rewizję oraz powoduje dotychczasowe konflikty oczekujących zadań i wymagania odświeżenia katalogu. Nie optymalizować globalnej rewizji rejestru przy okazji tej paczki. Enabled nie zeruje trust, ale disable unieważnia runtime i blokuje dostęp zgodnie z v1.

## Właściciel i izolacja

Jedna instancja robocza na (instancja sklr, WorkSession, stabilne profile_id, generacja runtime). Generacja jest unikalnym, nieodtwarzanym identyfikatorem ciągłości w RAM; nowy proces nigdy nie dostaje poprzedniej generacji. Nie utożsamiać jej z rewizją profilu, cache katalogu, transportowym sessionId ani tokenem preview. Generacja nie jest sekretem ani uprawnieniem.

Nie współdzielić runtime ani katalogów roboczych między WorkSessions. W obecnym sklr wszystkie klienckie rozmowy korzystają z jednego controllera; nie zapewniamy izolacji per czat/per klient w tej samej WorkSession. Sam identyfikator rozmowy nie nadaje dostępu. Początkowy odbiór Power Automate zakłada jednego operatora sterującego cyklem zmian. Wiele niezależnych konsumentów z izolowanym stanem wymaga osobnego kontraktu i tożsamości klienta.

Globalne profile pozostają zapisane po zmianie projektu; zamykamy runtime, nie usuwamy konfiguracji. Nowa WorkSession oraz wznowienie po pauzie nie dziedziczą procesu/generacji/preview. Właściciel jest przypinany przez serwer do zadania i sprawdzany także przy późniejszym wykonaniu; model nie przekazuje session_id.

Panel inspect/test/trust korzysta wyłącznie z oddzielnych krótkotrwałych sesji one-shot, także dla profilu z persistent. Nie pożycza roboczej instancji, nie zmienia jej current env/flow, nie odświeża jej idle i nie publikuje swojego katalogu jako roboczego discovery. Jawna akcja użytkownika autoryzuje konkretną operację administracyjną zgodnie z v1; pozostałe bramki sesji i ograniczenia kolejki pozostają.

## Supervisor i async lifecycle

Supervisor external MCP posiada jeden dedykowany wątek i długowieczną pętlę AnyIO/asyncio. Każdy runtime posiada osobne zadanie-właściciela, utrzymujące transport i ClientSession. Wejście i wyjście z ich kontekstów oraz związanych task groups następuje w tym samym zadaniu. Nie przenosić otwartych kontekstów AnyIO między przypadkowymi wątkami, pętlami ani osobnymi invocations portalu.

Synchroniczne executory TaskManager przekazują ograniczone żądania do supervisora i czekają z kontrolą timeout/cancel. Supervisor jest konstruowany bez połączeń; wątek i procesy uruchamiają się lazy po autoryzowanej operacji. Shutdown blokuje nowe dopuszczenia, pozwala dokończyć już dopuszczone tools/call w ich limitach i sprząta runtime przed zamknięciem używanych magazynów. Nie zamykać bazy pod działającym executorem ani odtwarzać executora z SQLite.

ContextVar autopilota nie jest trwałym stanem instancji MCP. Kontekst konkretnej operacji jest przenoszony jawnie, ze sprawdzeniem i rozliczeniem jej aktualnej delegacji. Nie przechwytywać kontekstu pierwszego discovery na czas życia procesu i nie używać go dla następnych calls. Recheck i oznaczenie dispatch/uncertain obejmują także wywołanie z ręczną zgodą; przejście na inny wątek nie może pomijać tych kontroli.

Kolejność blokad musi zachować obecne manager → controller → stores. Żadna globalna blokada managera, controllera ani ConfigStore nie obejmuje I/O, walidatora, oczekiwania na slot ani cleanup. Zdarzenia lifecycle ustanawiają krótką, synchronizowaną blokadę dopuszczania i zlecają cleanup poza globalnymi blokadami. Błąd powiadomienia nie otwiera bramki: kontrole właściciela/generacji i aktualnych warunków przed dispatch są obowiązkowe niezależnie od callbacków.

## Start, serializacja i limity

Persistent runtime tworzy się lazy po nowym autoryzowanym discovery modelu. Zwykły call nie tworzy procesu zastępczego po utracie ciągłości. Discovery zdrowej instancji używa tej samej sesji; brak startu wszystkich MCP przy uruchomieniu sklr. Ponownie sprawdzać aktualną zgodę na discovery i właściciela przed spawn, także po oczekiwaniu na slot.

Jedna operacja MCP naraz na runtime, w tym discovery, aktualny katalog, walidacja i tools/call. Nie tworzyć drugiego procesu, gdy pierwszy jest zajęty. Oczekiwanie na slot jest ograniczone i przerywalne; bez dodatkowej nieograniczonej kolejki w supervisorze. Kolejność wykonania wynika z istniejącej kolejki i kolejności dopuszczenia do slotu; nie obiecywać atomowej wielooperacyjnej transakcji ani kolejności według task_id.

| Parametr | Wartość zaakceptowana |
|---|---|
| Robocze instancje persistent | Maksymalnie 4 na instancję sklr; starting/closing i nieposprzątane broken również zajmują limit. |
| Start: spawn + initialize | Maksymalnie 15 s, podporządkowane pozostałemu timeout operacji. |
| Oczekiwanie executora na slot | Maksymalnie 8 s albo pozostały timeout, zależnie co krótsze. |
| Idle TTL | 600 s od zakończenia rzeczywistej operacji; zamknięcie tylko bez wykonywanej operacji. |
| Cleanup | Jeden wspólny deadline 5 s na całe sprzątanie, nie osobne 5 s na każdą próbę. |
| Timeout operacji | Dotychczasowe 1–300 s; slot/start/katalog/walidacja/call mieszczą się w jednym budżecie executora. Cleanup ma osobny ograniczony budżet. |

Po wyczerpaniu limitu instancji odmawiać, bez automatycznej eksmisji innego runtime przechowującego potencjalne preview. Limity zadań i max_parallel pozostają dotychczasowe; persistent nie tworzy bocznej ścieżki ich omijania. Oczekiwanie na ręczną zgodę nie zajmuje slotu runtime ani wątku workera. Odczyt profili, get_task/wait_task, panel i oczekiwanie na zgodę nie odświeżają idle. Idle 600 s nie przedłuża TTL tokenu FlowAgent ani TTL wyników.

Serializacja calls nie rezerwuje całego preview → zgoda → apply; inne autoryzowane wywołanie może wystąpić pomiędzy nimi. W tej paczce bez blokady zdalnego flow między profilami/sesjami, bez lease całego cyklu i bez atomowości Power Platform. Jawne env/flow i token związany z niezmienioną propozycją oraz kontrola wersji pozostają konieczne. Konkurencja spoza sklr i race między odczytem wersji a zapisem zdalnym nie są wyeliminowane.

## API modelu, katalog i generacja

call_external_mcp_tool otrzymuje opcjonalny parametr expected_runtime_generation: str | null, domyślnie null. Dla persistent jest wymagany; dla one-shot/HTTP/SSE pozostaje null. Parametr nie jest przekazywany do arguments zewnętrznego narzędzia. Brak, obca, stara lub niedostępna generacja powodują odmowę przed dispatch. Podanie generacji nie nadaje zgody i nie może wybierać cudzej WorkSession.

Autoryzowane list_external_mcp_tools zwraca generację w bezpiecznych, serwerowych metadanych external_result obok content; katalog w content pozostaje kompletny i ograniczony. Wyniki persistent calls także zawierają metadane własnej generacji. Metadane sklr nie są scalane z niezaufanym MCP result. Cache persistent jest przypinany do WorkSession, profile_id, rewizji konfiguracji i generacji; TTL katalogu pozostaje 300 s. Admin inspect nie może napełniać tego cache.

Utrata generacji unieważnia dopuszczanie calls i użycie cache do nowych zadań. Nowy proces można uzyskać tylko przez nowe autoryzowane discovery; zwraca nową generację i nie uruchamia starego call. Zadanie oczekujące na zgodę lub slot pozostaje związane z pierwotną generacją i argumentami. Żadna automatyczna podmiana generacji, wznowienie/replay ani retry tools/call.

list_external_mcp i panel mogą pokazywać wyłącznie bezpieczne metadane: tryb, rzeczywisty stan runtime bieżącej WorkSession, generację lub jej brak oraz bezpieczny powód utraty ciągłości/cleanup. Lokalny odczyt nie łączy się z serwerem i nie odświeża idle. Model nie dostaje możliwości restartu ani konfiguracji procesu. Panel odróżnia tryb, enabled, aktywny runtime i historyczny last_test; inspect nie oznacza połączenia roboczego.

## Kontrole i granica dispatch

Po uzyskaniu slotu, przed każdą próbą tools/call:

1. Sprawdzić przypiętą WorkSession, aktualny profil i oczekiwaną generację.
2. Pobrać aktualny katalog z tej samej sesji MCP; sprawdzić narzędzie i niezmieniony digest schematu przypiętego do zgody/trust.
3. Walidować niezmienione argumenty istniejącym workerem z dotychczasowymi limitami i dialektami. Bez coercion/defaults i bez przepisywania digestu.
4. Ponownie sprawdzić panel, enabled/revision, właściciela/generację, WorkSession, zgodę/delegację, trust/limiter i cancel.
5. Pod krótką synchronizacją rozstrzygnąć lokalne dopuszczenie tools/call względem pauzy/zmiany konfiguracji/utraty zgody. Nie utrzymywać blokad podczas transmisji.
6. Oznaczyć dispatch dla właściwej operacji i wykonać jedną próbę session.call_tool.

Odmowa przed punktem 5 nie oznacza zdalnego skutku biznesowego. Po dopuszczeniu skutek może wystąpić: błąd/timeout/cancel/utrata protokołu jest konserwatywnie niepewny, bez gwarancji rollbacku. Start executora albo admission token TaskManager nie zastępują powyższej granicy.

Dla persistent pauza blokuje także executor czekający na slot albo przygotowujący niedopuszczony tools/call. Dopiero tools/call dopuszczony przed pauzą może dokończyć w dotychczasowym limicie; nie ustawiać mu cancel tylko z powodu pauzy. Jest to jawne uzupełnienie WorkSession wyłącznie dla persistent: nie zmieniać przy okazji granicy istniejącego one-shot/HTTP/SSE ani pozostałych narzędzi. Przygotowanie/katalog należy bezpiecznie przerwać lub zakończyć bez biznesowego dispatch i sprzątnąć; żądanie pauzy nie czeka na I/O.

Limiter pozostaje per profile_id poza runtime; spawn, idle, zmiana trybu i nowa WorkSession nie resetują 30/min w tej samej instancji sklr. Dotychczasowy restart procesu sklr nadal traci RAM limitera. Przekroczenie przed dispatch może skierować to samo zadanie do ręcznej zgody zgodnie z istniejącą polityką jednego reapproval; zdrowy runtime nie musi być niszczony. Zgoda nie odświeża generacji ani tokenu preview. Persistent nigdy nie jest stałą zgodą na calls; annotations nie nadają uprawnień.

## Lifecycle i utrata ciągłości

Stany runtime: absent, starting, ready, busy, closing, broken. Ready oznacza rzeczywistą aktywną sesję po initialize, nie sam enabled, cache katalogu ani dawny test. EOF/crash muszą unieważniać generację także w bezczynności. Idle/shutdown i callbacks nie mogą powodować podwójnego spawn ani podwójnego cleanup.

| Zdarzenie | Zachowanie |
|---|---|
| Pauza/koniec/zmiana projektu lub WorkSession | Natychmiast zablokować nowe dopuszczenia, unieważnić generację i oznaczyć closing. Dopuszczony call drain w swoim limicie, potem cleanup. Niedopuszczony call bez wysłania. Wznowienie nie odzyskuje runtime/preview/kolejki. |
| Disable/delete/zmiana połączenia lub trybu | Unieważnić runtime i cache; blokada nowych calls. Już dopuszczony call drain, potem cleanup. Dawna zgoda nie uruchamia nowej konfiguracji. Kontrola dostępu do wyników zgodnie z v1. |
| Alias/description | Proces może pozostać; zachować dotychczasową kontrolę rewizji, konfliktów i odświeżenia katalogu. |
| Zmiana trust | Kontrola przed dispatch; sama zmiana trust nie musi niszczyć procesu. Nie odzyskiwać utraconej zgody. |
| Idle | Zamknąć bez aktywnej operacji; utrata generacji i cache do nowych calls. |
| Cancel/timeout oczekiwania na slot | Brak tools/call; zdrowy runtime pozostaje. |
| Timeout/cancel wykonywanej operacji/protokół/crash | Runtime unusable, broken i bounded cleanup. Przed dopuszczeniem odmowa; po nim jawne *_uncertain. Bez automatycznego retry/reconnect call. |
| Poprawna odpowiedź MCP z isError=true | Zachować wynik biznesowy; sam isError nie oznacza awarii transportu i nie wymaga niszczenia runtime. |
| Restart sklr | Brak odtworzenia runtime/generacji/tokenów/executorów. Nowe discovery wyłącznie w aktualnym zakresie dostępu. |

Cleanup failure ma bezpieczny kod i stan broken; nie oznaczać absent bez potwierdzenia. Nie uruchamiać nowego procesu tego właściciela/profilu, dopóki sprzątanie nie jest potwierdzone. Dopuszczony call z poprawnym wynikiem oraz późniejsza awaria cleanup to odrębne fakty: nie wymazywać potwierdzonego skutku ani nie przedstawiać cleanup failure jako dowodu braku zapisu. Niepewny wynik wymaga odczytowej weryfikacji zdalnej po osobnym uzgodnieniu, nie ponowienia mutacji.

## Bezpieczeństwo, wyniki i granice containment

Zachować ograniczone env/env_refs, uruchamianie bez shella, Windows Job Object i POSIX process group, bez poszerzania dziedziczenia środowiska. Rozwiązane wartości są potrzebne przy spawn; usuwać zbędne referencje po starcie/cleanup. Python strings nie są bezpiecznie zerowane, RAM może być swapowany; proces nie jest sandboxem. Dłuższy czas życia zwiększa czas przechowywania stanu upstream w RAM.

Windows Job Object KILL_ON_JOB_CLOSE ogranicza życie drzewa także po śmierci sklr, z wymaganym odbiorem nested jobs. POSIX process group sprząta przy działającym supervisorze, ale sama nie gwarantuje usunięcia potomków po SIGKILL/hard crash sklr ani nie jest ochroną przed świadomą ucieczką procesu. Nie deklarować równej gwarancji obu platform; dodatkowy mechanizm POSIX hard-crash wymaga osobnego uzgodnienia. Awarii cleanup i pominiętych scenariuszy nie uznawać za sukces.

Tokeny/payloady nie trafiają do config, SQLite, logów ani telemetrii sklr. Surowe wyjątki nie wychodzą z async supervisora; stderr pozostaje bez utrwalania. Własny cache dyskowy, auth, backup i telemetry upstream wymagają osobnego odbioru i nie są wyłączone automatycznie przez persistent.

Zachować dotychczasowe limity: 200 tools, 10 stron katalogu upstream, schema/arguments po 65536 B, katalog/wynik wewnętrzny po 1048576 B oraz ograniczenie kanału external_result do 80000 znaków. Walidator 7 s i blokady referencji/dialektów pozostają. Nie dodawać pagingu model API ani zmieniać HTTP/SSE w tej paczce. Wyniki zachowują content, structuredContent i isError; oversize odrzucany jawnie, bez cichego skracania lub auto-fetch URL.

external_result pozostaje RAM/TTL 300 s, bez konsumowania i bez przedłużania przy get/wait/discovery. Wynik persistent dodatkowo sprawdza właściciela WorkSession. Samo idle/pauza i zamknięcie runtime nie muszą usuwać poprawnego wyniku przed TTL: dawna generacja pozostaje metadanymi wyniku, ale nie nadaje prawa do nowego call. Disable/delete/zmiana połączenia/restart/utrata dostępu nadal blokują wynik zgodnie z v1. Nie zmieniać secret_result.

Sklr nie interpretuje dowolnych biznesowych tokenów preview. Generacja chroni ciągłość transportu, nie wymusza uniwersalnego preview ani dokładnej treści biznesowego diffu w maskowanej zgodzie. Skill nadal wymaga validation/preflight, dokładnej propozycji, preview, osobnej zgody na apply i readback. Trust narzędzia łączącego dryRun i zapis nie jest zgodą wyłącznie na preview; FlowAgent pozostaje bez trust na pierwszy odbiór edycji. Brak exactly-once: brak retry tools/call w sklr nie wyklucza własnych retry/fallback upstream.

## Kryteria akceptacji

| ID | Kryterium |
|---|---|
| P01 | Odczyt v1/brak pola/jawny one_shot bez zapisu, zmiany digestu ani trust; zatwierdzony zapis v2 z rollback; nieznany/uszkodzony config odrzucony. HTTP/SSE i zwykłe skille bez zmiany zachowania. |
| P02 | Opt-in wyłącznie w panelu z rewizją; brak spawn przy zapisie/enabled/starcie; persistent bez WorkSession odrzucony bez fallback. Zmiana trybu zeruje trust bez odtworzenia przy powrocie. |
| P03 | Realny lokalny fake stdio z RAM tokenem: one-shot traci token; persistent zachowuje go między dwoma calls rzeczywistej ścieżki API sklr z osobnymi decyzjami. |
| P04 | Jeden spawn/zadanie-właściciel i serializacja; rozdzielone WorkSessions/admin inspect, jawny kontekst autopilota, brak podwójnego cleanup i obcego dostępu. Bez deklaracji izolacji klientów tej samej WorkSession. |
| P05 | Aktualny schema/profile/trust/session/cancel/generacja sprawdzane po slot/walidacji i przy dopuszczeniu dispatch; limiter 30/min nie resetowany przez runtime, to samo zadanie wraca do zgody tylko przed dispatch. |
| P06 | Pause/end/disable/config/mode/project change i idle: natychmiast zamknięte dopuszczanie, drain dopuszczonego call, bounded cleanup bez replay. Alias/description zachowują obecną semantykę rewizji. |
| P07 | Generacja utracona przed zgłoszeniem, podczas zgody/slot/walidacji oraz crash/restart: brak call/reconnect ze starą zgodą. Timeout/cancel po dopuszczeniu jawnie uncertain, zero retry. |
| P08 | Ścieżki ze spacjami, Windows/POSIX cleanup i potomkowie, nested jobs, awarie i wspólny deadline cleanup; broken zajmuje limit i blokuje zastępczy proces. POSIX hard-crash ograniczenie jawne, skip nie jest odbiorem. |
| P09 | Ochrona danych, właściciel wyników, TTL bez przedłużania, isError bez pozornego sukcesu biznesowego; limity i odmowy schematów jak dotychczas. 4 instancje/15 s start/8 s slot/600 s idle/5 s cleanup sprawdzone. |
| P10 | Dopiero po fake/lifecycle i osobnym uzgodnieniu FlowAgent/bezpiecznego flow: validation/preflight → preview → zgoda → apply z tą samą generacją/propozycją → backup/readback. Conflict/expired/reused token zatrzymuje zapis; uncertain bez retry. |

P01–P09 bez prawdziwego tenanta: testy logiki oraz rzeczywisty lokalny fake MCP przez SDK/TaskManager/API. Procesy fake wymagają uzgodnionego wykonania; nie są operacjami Power Platform. Pytest tylko na nowe wyraźne polecenie przez test-runner: test RED → minimalna implementacja → GREEN → regresja. Zero testów lub brak RED/GREEN nie są sukcesem. Historia poprzedniej regresji nie jest wynikiem tej paczki.

P10 wymaga osobnego planu środowiska, flow, wersji/hash upstream, dokładnej mutacji, backup i skutków. Personal Productivity/PBIdaxTest nie są automatycznym celem edycji. Run/publish/resubmit, auth/connection mutations i instalacje osobno. Zapis flow pozostaje zablokowany do odebranego i zapisanego zakresu; sama implementacja flagi ani odbiór fake nie odblokowują SKILL.md.

## Etapowy plan i kontynuacja

Akceptacja obejmuje kontrakt i kolejność P1–P5, nie domyślne wykonanie całej sekwencji. P1: zapis dokumentacji. P2: osobno zaakceptowana paczka testów i wyraźne polecenie ich uruchomienia. P3: po RED i zgodzie na kod — konfiguracja/panel, supervisor/runtime, następnie API/WorkSession z GREEN/regresją właściwych paczek. P4: osobno uzgodniony lokalny odbiór rzeczywistej ścieżki z fake backendem. P5: osobno uzgodniony odbiór Power Automate. Konkretne otwarte zadania pozostają wyłącznie w backlogu; wyniki w CURRENT, nie jako kolejne checkpointy specyfikacji.

Przy kontynuacji prepare_project, CURRENT/backlog i świeży odczyt właściwych źródeł. Zachować cudze zmiany i lokalny config; nie przełączać profilu flowagent ani web_mcp automatycznie. Przed kodem ustalić konkretną paczkę plików/testów i istniejący interpreter. Bez instalacji, uruchamiania serwerów lub testów i operacji na flow bez osobnego uzgodnienia.
