---
type: Reference
title: "Persistent stdio — przekazania przed porządkowaniem kontekstu"
description: Historyczna treść przeniesiona z CURRENT oraz START skilla; nie jest aktualnym planem ani uprawnieniem.
status: deprecated
tags: [archive, persistent-stdio, power-automate, handoff]
---
# Archiwum przekazań przed porządkowaniem kontekstu

Archiwizacja dokumentacyjna po zapisie testów #2805, przed ich uruchomieniem.
Poniższe dawne określenia „najnowsze”, „aktualne”, „następny krok” i polecenia
są historyczne. Źródła obowiązujące: docs/plan/CURRENT.md, docs/plan/backlog.md
oraz zaakceptowany kontrakt docs/specs/external-mcp-persistent-stdio-proposal.md.
Ścieżki/linki w przeniesionej treści są względem pierwotnych dokumentów:
CURRENT: root sklr/docs/plan; START: root dev-skills/power-automate/docs.
Nie wykonywać historycznych promptów, nie odtwarzać zgód ani dawnych zadań.

## Przeniesiona sekcja CURRENT — persistent stdio

## External MCP — persistent stdio: S1/S2a/S2b/S3 z atrapami GREEN, S4 z rzeczywistym lokalnym SDK GREEN

Najnowszy zakres S4 i regresja one-shot (ta sekcja jest nadrzędna wobec poniższych opisów braku rzeczywistego SDK/procesów). Regresja one-shot po zmianach współdzielonego stdio_transport (#2775, #2785): na nowe wyraźne polecenie i osobną zgodę na procesy potomne istniejące test_external_mcp_sdk_stdio i test_external_mcp_sdk_cleanup (rzeczywisty SDK, lokalny FastMCP, ścieżka ze spacją, env bez dziedziczenia, timeout uncertain, serwer znika po timeout/cancel, potomek znika po timeout) — #2789, test-runner, istniejący venv Windows/Python 3.14.7: **6 passed, 3517 deselected / 54.22 s pytest**, wrapper 56.4 s, exit_code 0, bez skipów/błędów. Zgłoszone #2790 było zbędnym duplikatem i zostało odrzucone w panelu; nie jest wynikiem. Luka: potomek jest sprawdzany tylko po timeout, nie po normalnym zamknięciu one-shot.

S4: #2791 zapisano dwa nowe pliki tests/external_mcp_persistent_sdk_helpers.py (lokalny peer JSON-RPC MCP, rig z rzeczywistym PersistentSupervisor + bounded_stdio_client + ClientSession + walidatorem schematów + containment, kontrola PID i teardown z asercją braku awaryjnego kill) oraz tests/test_external_mcp_persistent_sdk.py (6 przypadków). Bez zmian kodu produkcyjnego. Pliki tymczasowe zawierają tylko PID, liczniki i sygnały, token preview tylko w RAM serwera fake. Pierwsze i jedyne uruchomienie #2792 (nowe polecenie, zgoda na potomków), ten sam runner/środowisko: **6 passed, 3523 deselected / 14.97 s pytest**, wrapper 16.6 s, task 17.05 s, exit_code 0, bez skipów/błędów. Potwierdzono na rzeczywistych procesach: preview→apply w tym samym procesie/generacji/jednym initialize (token RAM), ponowne discovery bez drugiego spawn, EOF w bezczynności unieważnia generację bez zastępczego procesu, timeout po dispatch daje call_timeout_uncertain bez retry i sprząta proces, invalidation oraz shutdown usuwają serwer i potomków, shutdown kończy wątek supervisora. To odbiór istniejącej implementacji (testy napisane po kodzie, bez fazy RED); nie jest cyklem RED→GREEN. Nie dowodzi API/TaskManager/WorkSession, admin isolation, zgód, kanału wyników, nested jobs, POSIX ani hard-crash sklr.

P3c-A (tylko przegląd kodu, NIC nie zapisano ani nie uruchomiono): ExternalMCPTools kieruje do klienta one-shot, bez expected_runtime_generation; cache katalogu kluczowany tylko profile_id; wyniki sprawdzają profil/połączenie, nie właściciela WorkSession; guard dispatch używa ContextVar autopilota (recheck_current/mark_dispatched/mark_uncertain), a supervisor celowo startuje z pustym kontekstem — kontekst operacji trzeba przenieść jawnie, nie przechwytywać przy discovery; manager.external_approval_granted(event) wiąże zgodę z eventem executora; SessionController.begin_task/begin_action to dopuszczenie lokalnego startu, nie granica tuż przed persistent tools/call po slocie i walidacji; brak powiadamiania persistent o pauzie/end/zmianie projektu (jest tylko listener aktywacji). Ostrzeżenie: guard supervisora działa pod jego blokadą; lifecycle nie może trzymać blokad manager/controller czekając na supervisor (kolejność manager → controller → stores). Zaakceptowana następna paczka: testy RED wewnętrznego mechanizmu dopuszczenia tuż przed dispatch (tests/external_mcp_persistent_admission_helpers.py i tests/test_external_mcp_persistent_admission.py; właściciel przypięty do zadania/eventu, pauza i end przed dispatch blokują, dopuszczony call drain, zgoda dla dokładnie tego zadania, jawny kontekst autopilota per operacja, odmowa przed dispatch ≠ uncertain, wyścig pauza–dispatch bez blokad na I/O). Pliki jeszcze NIE zapisane; po RED osobna akceptacja minimalnej implementacji; routing API, cache/wyniki, callbacks lifecycle i panel osobnymi paczkami. persistent_runtime_required, profile/config/upstream/SKILL.md i zapis flow bez zmian; FlowAgent nie był wywoływany. Nie powtarzać #2750/#2756/#2770/#2778/#2786/#2789/#2792 bez przyczyny.

Poniżej wcześniejszy zakres S1/S2a/S2b/S3.

Wcześniejszy zakres S3: #2781 dodano tests/external_mcp_persistent_wire_fakes.py i tests/test_external_mcp_persistent_transport_integration.py (10 przypadków). Testy używają rzeczywistego supervisora, bounded_stdio_client i ClientSession SDK, ale atrap procesu/containment; bez subprocessów, sieci, Job Objects/POSIX groups, API/TaskManager/WorkSession i FlowAgent. Pierwszy RED #2782 (**9 failed, 1 passed, 3513 deselected / 8.68 s**, wrapper 11.3 s) zatrzymał się na discovery z client_failed; statyczna kontrola zainstalowanego SDK wykazała błąd helpera: read_timeout_seconds dostawał timedelta zamiast liczby. #2783 poprawiło wyłącznie helper. Miarodajne RED #2784 w istniejącym venv Windows/Python 3.14.7: **2 failed, 8 passed, 3513 deselected / 6.86 s pytest**, wrapper 8.5 s, exit_code 1, bez skipów/setup/teardown errors. Odtworzono: EOF w bezczynności nie unieważnia generacji bez kolejnego call oraz cleanup transportu po zamknięciu sesji dostaje nowy budżet (0.678 s przy limicie przeskalowanym 0.4 s + tolerancja).

#2785 po osobnej zgodzie zmieniło persistent_supervisor.py i stdio_transport.py: publiczny wrapper receive obserwuje EOF/błąd odbioru, oznacza runtime jako zamykany i budzi właściciela bez prywatnych struktur SDK; RAM ContextVar przekazuje jeden realny monotonic cleanup deadline do stdio, supervisor ustawia go na początku teardownu i resetuje. Testy/asercje nie były zmieniane. GREEN/regresja #2786 na nowe wyraźne polecenie, ten sam runner/środowisko: **78 passed, 3445 deselected / 20.71 s pytest**, wrapper 21.5 s, exit_code 0, bez skipów/błędów. Filtr: S3 integration (10), persistent_failures/supervisor/lifecycle (40 S1/S2a), persistent_stdio, stdio/parse oraz containment bez POSIX/Windows-specific (28). To odbiór integracji supervisor–stdio–ClientSession z atrapami procesów; nie jest pełnym persistent runtime ani odbiorem Windows/POSIX containment, potomków i nested jobs.

Otwarte po S3: współdzielony stdio_transport zmieniono dwukrotnie (#2775, #2785), więc nadal brakuje osobno uzgodnionej regresji one-shot na rzeczywistym lokalnym serwerze/SDK (test_external_mcp_sdk_stdio/cleanup; uruchamiają procesy potomne). Następnie lokalny fake przez rzeczywiste API sklr→TaskManager→SDK z dwoma calls/zgodami, P3c fail-closed routing, P3a panel i P4/P5. Nie powtarzać #2750/#2756/#2770/#2778/#2786 bez przyczyny. Nie podłączono produkcyjnego routingu/API/panelu/WorkSession, nie zmieniono profili/configu/upstream/SKILL.md ani persistent_runtime_required. Bez instalacji/restartów/operacji FlowAgent; zapis flow nadal zablokowany. Poniższe sekcje to wcześniejsze S2b/S2a/S1.

Wcześniejszy zakres S2b: #2773 zapisano tests/test_external_mcp_persistent_stdio.py (12 przypadków); RED #2774 przez centralny test-runner w istniejącym venv Windows/Python 3.14.7: **8 failed, 4 passed, 3501 deselected / 12.59 s pytest**, wrapper 15.1 s, exit_code 1, bez skipów/setup/teardown errors. Odtworzono brak total cleanup budget dla stdin/wait-close/process-close i ponowny cleanup w finally, brak zamknięcia containment po cancel spawn, nieograniczony attach failure cleanup, brak sprzątania po błędzie alokacji streams i surowy błąd kill_tree. Survivor, EOF, invalid JSON i argumenty/env przeszły. Atrapy procesu/containment, bez subprocessów i sieci; nie odbiór OS containment.

#2775 po osobnej zgodzie poprawiono wyłącznie stdio_transport.py: stop_process ma opcjonalny deadline i ogranicza wszystkie fazy stdin/wait/terminate/kill/process.aclose jednym budżetem; transport wykonuje pojedynczy cleanup także po błędzie/cancel częściowego startu i sanitizuje błędy. Zmiana dotyczy współdzielonego transportu używanego przez one-shot; nie podłączono supervisora/API. Pierwsza regresja #2776: **27 passed, 1 failed, 3485 deselected / 18.24 s**, wrapper 21.8 s: wszystkie nowe S2b przeszły, stara asercja survivor nie przewidywała dodatkowej próby close. Osobno zaakceptowane #2777 dostosowało tylko tę asercję do terminate/kill/close, zachowując stdio_cleanup_failed i dodając returncode=None oraz stdin.closed; nie przedstawia zamknięcia uchwytów jako zakończenia procesu.

GREEN/regresja #2778 na nowe polecenie w tym samym środowisku: **28 passed, 3485 deselected / 17.55 s pytest**, wrapper 20.6 s, exit_code 0, bez skipów/błędów. Filtr: test_external_mcp_persistent_stdio lub test_external_mcp_stdio (w tym parse) oraz test_external_mcp_containment bez test_posix/test_windows. Odebrane 12 S2b + 16 regresji z atrapami/SDK types; bez rzeczywistych procesów, Job Objects, POSIX groups, tenantów i FlowAgent. Nie sumować z #2770 jako pełnego odbioru integracji supervisor–transport.

Następny krok do osobnego uzgodnienia: testy integracji supervisor–stdio/ClientSession, wspólny deadline całego łańcucha sesja/transport/process/containment, potem rzeczywisty lokalny fake SDK i odpowiednia regresja one-shot po zmianie transportu. S2b domknięte wyłącznie w opisanym zakresie atrap. Pełne MCP-P2, dalsze P3b/P3c/P4/P5 nadal otwarte. Nie podłączono fabryk do produkcyjnego supervisora, nie zmieniono profili/configu/upstream/SKILL.md ani persistent_runtime_required. Bez instalacji/restartów/operacji flow; zapis flow nadal zablokowany. Poniżej wcześniejszy zakres S2a/S1.

Wcześniejszy zakres S2a: #2766 utworzono tests/test_external_mcp_persistent_failures.py (10 przypadków po parametryzacji); #2767 rozszerzono atrapy o partial transport/session entry i teardown. RED #2768 w istniejącym venv Windows/Python 3.14.7 przez centralny test-runner: **4 failed, 6 passed, 3491 deselected / 12.59 s pytest**, wrapper 14.4 s, exit_code 1, bez skipów/setup/teardown errors. Odtworzono broken po cancel initialize, przedwczesne broken zamiast closing w cleanup anulowanego startu, nieanulowany pending acquire po awaryjnym timeout executora i brak terminowego zakończenia starting przy shutdown. Trzy warianty EOF przeszły; statyczne podejrzenie zgubienia wyniku EOF nie zostało odtworzone w tej paczce.

Po osobnej akceptacji #2769 zmieniono wyłącznie persistent_supervisor.py: start watchdog przerywa zadanie-właściciela bez trwałego anulowania lifetime scope cleanup, reaguje też na shutdown/invalidation; abandoned flag i cancel bridge future blokują późniejszą akwizycję/dispatch po awaryjnym timeout; synchroniczne oczekiwania dzielą jeden safety deadline. Testów/asercji nie osłabiono. GREEN/regresja #2770 na nowe wyraźne polecenie, te same środowisko/runner: **40 passed, 3461 deselected / 20.19 s pytest**, wrapper 22.9 s, exit_code 0, bez skipów/błędów. Wyłącznie 10 S2a + 30 S1; bez powtarzania konfiguracji/fallbacku #2750/#2756. To potwierdzenie czterech reprodukcji i regresji dotkniętego supervisora z atrapami, nie pełne repo/SDK/transport ani odbiór Power Automate.

S2a w zakresie powyższych testów domknięte; pozostałe luki/wyścigi i rzeczywisty stdio nadal wymagają osobnych paczek. S2b wykonano później w zakresie opisanym powyżej; nie jest to jeszcze odbiór połączenia z supervisorem ani rzeczywistych procesów. Nie podłączono transportu, API/panelu/WorkSession, nie zmieniono profili/configu ani bramki SKILL.md. Bez instalacji/restartów/sieci/subprocessów/FlowAgent calls. Pełne MCP-P2 pozostaje otwarte; persistent_runtime_required i zapis flow nadal zablokowane. Poniżej zachowano wcześniejszy zakres S1.

Wcześniejsza paczka MCP-P2-S1/P3b-S1 została osobno zaakceptowana i wykonana. #2759: utworzono tests/external_mcp_persistent_fakes.py, tests/test_external_mcp_persistent_supervisor.py (17 testów) i tests/test_external_mcp_persistent_lifecycle.py (13 testów); #2760: korekta przypięcia atrapy walidatora do kontekstu zadania fake sesji. RED #2761 przez centralny test-runner: **30 errors przy setup, 3461 deselected / 1.32 s pytest**, wrapper 2.7 s, exit_code 1; wszystkie przypadki zebrane, brak modułu external_mcp.persistent_supervisor. To RED braku implementacji/API, nie poszczególnych asercji zachowania: ciała testów nie zostały wykonane w RED.

Po osobnej zgodzie #2762 zapisano nowy, odłączony external_mcp/persistent_supervisor.py (done, exit_code 0). Wątek/pętla uruchamiane lazy, zadanie-właściciel wchodzi i wychodzi z kontekstów transportu/sesji, ograniczony slot, generacja, idle/EOF/invalidation, drain i bounded cleanup; transport/fabryka sesji wymagają jawnego wstrzyknięcia. Nie podłączono domyślnego stdio: jego shared-deadline cleanup i containment wymagają osobnej paczki/odbioru. Nie zmieniono client.py, API, panelu, WorkSession, profili ani configu; persistent_runtime_required i bramka zapisu flow pozostają.

GREEN #2763 na nowe wyraźne polecenie przez centralny test-runner, istniejący venv Windows/Python 3.14.7: **30 passed, 3461 deselected / 10.72 s pytest**, wrapper 11.9 s, exit_code 0, bez skipów/błędów. Filtr wyłącznie test_external_mcp_persistent_supervisor lub test_external_mcp_persistent_lifecycle. Potwierdzono testy z atrapami właściciela/pętli, jednego otwarcia, generacji i RAM tokenu, slotu, limitów 4/15/8/600/5, idle, EOF, invalidation/drain, uncertain bez retry, isError i cleanup/broken. Bez sieci, subprocessów i rzeczywistego SDK transportu. To częściowy zakres P04/P06/P07/P08/P09, nie pełne MCP-P2 ani odbiór produkcyjnego persistent runtime. #2750/#2756 i read-only PoC nie były powtarzane; bez instalacji, restartów i operacji FlowAgent.

Następny krok do osobnego uzgodnienia: przegląd luk tej paczki i konkretne testy RED rzeczywistego adaptera stdio/shared-deadline cleanup oraz dalszych kontroli API/WorkSession. P3a/P3c i rzeczywista ścieżka API→TaskManager→SDK→lokalny fake z potomkami/nested jobs P4 pozostają otwarte; nie przełączać profili ani usuwać fallbacku. Zielone atrapy nie gwarantują containment Windows/POSIX, izolacji admina, aktualnych zgód/delegacji ani ochrony kanału wyników. Zapis flow nadal zablokowany. Poniższe wyniki konfiguracji/fallbacku zachowane; najnowszy zakres supervisora opisano powyżej.

Paczkę przejściowej blokady one-shot domknięto RED→GREEN: trzy nowe przypadki w test_external_mcp_client (#2753), RED #2754: 3 failed, 3458 deselected / 2.80 s. Implementacja #2755 (client.py i server/external_mcp_tools.py, 2/2 pliki): profil persistent jest odrzucany kodem persistent_runtime_required przed prepare_runtime, rozwiązaniem env i otwarciem transportu; kod zachowany przez allowlist adaptera. GREEN/regresja #2756 przez centralny test-runner, istniejący venv Windows/Python 3.14.7: **113 passed, 3348 deselected / 7.86 s pytest**, wrapper 8.9 s, exit_code 0, bez skipów/błędów. Filtr: test_external_mcp_client, test_external_mcp_tools, test_external_mcp_persistent_config, test_external_mcp_runtime_config, test_external_result_channel. Atrapy bez sieci/subprocessów; nie jest to test rzeczywistego FlowAgent ani persistent runtime.

Domknięte są paczki konfiguracji i blokady fallbacku oraz najnowszy, częściowy P3b-S1 z atrapami opisany powyżej. **Całe MCP-P2 nie jest ukończone**: rzeczywisty transport/fake MCP, integracja właściciela/generacji z API/WorkSession, zgody/delegacje, wyniki i containment wymagają dalszych testów RED i odbiorów. Nie przeskakiwać do szerokiego P3 ani przełączenia profilu na podstawie zielonych atrap. Nowy czat wymaga świeżego odczytu AGENTS/CURRENT/backlog i zaakceptowanego kontraktu; konkretne zmiany/testy kolejnej paczki wymagają nowej akceptacji. Nie powtarzać odebranego read-only PoC. Zapis flow pozostaje zablokowany.

Pierwsza paczka testów konfiguracji w tests/test_external_mcp_persistent_config.py (#2745) miała potwierdzone RED #2746: 11 failed, 16 passed, 3431 deselected / 3.59 s. Po osobnej akceptacji minimalna implementacja external_mcp/profiles.py i registry.py zapisana #2749: done, exit_code 0, 2/2 pliki. Walidator normalizuje brak stdio_mode do one_shot i przyjmuje persistent tylko dla stdio; connection_digest zachowuje dokładny v1 digest dla one-shot, a persistent obejmuje tryb. Rejestr czyta v1/v2 bez zapisu; zatwierdzona operacja zapisu z normalizowanymi profilami stdio promuje strukturę do v2, network-only v1 pozostaje v1, v2 nie jest obniżane. ID/rewizje/rollback ConfigStore i kontrola trust zachowane; powrót z persistent nie odzyskuje usuniętego trust.

GREEN i uzgodniona regresja #2750 przez centralny test-runner, istniejący venv Windows/Python 3.14.7: **167 passed, 3291 deselected / 3.10 s pytest**, wrapper 4.7 s, exit_code 0, bez skipów i błędów. Filtr: test_external_mcp_persistent_config, test_external_mcp_profiles, test_external_mcp_registry, test_external_mcp_trust, test_external_mcp_runtime_config. Wszystkie 27 nowych przypadków zaliczone, w tym rollback v2 na rzeczywistej symulowanej granicy zapisu (pamięć/dysk/rewizja/trust). To RED→minimalna implementacja→GREEN dla konfiguracji, nie pełna regresja repo ani odbiór transportu. Testy bez MCP/sieci/subprocessów, na tmp_path.

Nie zmieniano lokalnego config, profili ani skilla, bez instalacji/serwerów/FlowAgent calls. Panel i routing API/generacji/WorkSession są niewdrożone; odłączony supervisor P3b-S1 jest zapisany i sprawdzony wyłącznie z atrapami opisanymi powyżej; sam zapis stdio_mode nie uruchamia persistent; dotychczasowy klient obsługuje one-shot, a persistent odmawia przed transportem kodem persistent_runtime_required zgodnie z paczką opisaną wyżej. Nie przełączać profilu ani używać nowego trybu przed wdrożeniem i odbiorem właściwego runtime oraz fail-closed routing. Pełne P2 lifecycle/API/fake, pozostałe P3b, P3c i P4/P5 niewykonane. Kolejne paczki wymagają osobnego planu/akceptacji i polecenia testów. Zapis flow nadal zablokowany.

Po statycznym przeglądzie aktualnego client/stdio/registry/profiles/runtime config, executorów TaskManager, WorkSession/autopilot, panelu i odpowiednich fragmentów FlowAgent użytkownik zaakceptował [szczegółowy kontrakt](../specs/external-mcp-persistent-stdio-proposal.md) i etapy P1–P5. Zapis #2741 potwierdzony: done, exit_code 0, committed=true, cleanup_complete=true; status stable oznacza zaakceptowane wymagania, nie verified runtime. Dokumentacyjnie domknięto P1: kontrakt external MCP odsyła do rozszerzenia, WorkSession opisuje węższą granicę dispatch tylko dla persistent, backlog zawiera kolejne otwarte paczki.

Uzgodniono stdio_mode=one_shot/persistent, brak pola → one-shot, odczyt v1 bez zapisu i zatwierdzony zapis v2, zachowanie dotychczasowego digestu dla one-shot i utratę trust po zmianie trybu. Właściciel WorkSession/profile_id/generacja, bez izolacji per klient tej samej WorkSession; admin inspect zawsze osobny one-shot. Supervisor z dedykowaną pętlą i zadaniem-właścicielem sesji, jawny kontekst operacji autopilota. Limity: 4 instancje, 15 s start, 8 s slot, 600 s idle i jeden deadline cleanup 5 s. Generacja wymagana dla persistent call; po jej utracie nowe autoryzowane discovery, bez reconnect/replay call. Pauza/zmiana połączenia blokują niedopuszczone calls; dopuszczony call drain, potem cleanup. Broken blokuje proces zastępczy; POSIX process group nie gwarantuje cleanup po SIGKILL sklr. Brak lease całego preview/apply i gwarancji atomowości Power Platform.

Przy P1 lokalna lista potwierdziła flowagent enabled/stdio/bez trust i web_mcp enabled/http/trusted; nie wykonywano discovery ani wywołań zewnętrznych MCP. P1 obejmowało tylko dokumentację; późniejsze RED/GREEN i regresja konfiguracji opisane wyżej. Historyczny odebrany read-only PoC nie był powtarzany. Regresja runtime i lokalny fake odbiór są niewykonane. HTTP/SSE, zwykłe skille i pozostałe narzędzia bez zmian zachowania. Zapis Power Automate nadal zablokowany do osobno uzgodnionego i odebranego ciągu validation/preflight → preview → zgoda → apply → backup/readback. Otwarte prace wyłącznie w backlogu; starszy draft w START/planie skilla nie zastępuje tych aktualnych uzgodnień sklr.

## Przeniesiony START skilla power-automate

---
type: Project State
title: "Power Automate w sklr — start nowego czatu"
description: Punkt przekazania dla wdrożenia skilla i integracji FlowAgent MCP, z zakresem zgód i blokadami edycji.
status: draft
tags: [power-automate, sklr, flowagent, handoff, implementation]
---
# Start nowego czatu

## Aktualne przekazanie — S4 z rzeczywistym lokalnym SDK GREEN (najnowsze)

Ta sekcja jest nadrzędna wobec poniższych. Regresja one-shot po zmianach współdzielonego stdio_transport: #2789 (nowe polecenie, zgoda na potomków), test_external_mcp_sdk_stdio + test_external_mcp_sdk_cleanup, test-runner, istniejący venv Windows/Python 3.14.7: 6 passed, 3517 deselected / 54.22 s, exit_code 0, bez skipów. #2790 było zbędnym duplikatem, odrzucone. S4: #2791 zapisano tests/external_mcp_persistent_sdk_helpers.py i tests/test_external_mcp_persistent_sdk.py (6 przypadków, bez zmian kodu produkcyjnego); #2792 na nowe polecenie: 6 passed, 3523 deselected / 14.97 s, wrapper 16.6 s, exit_code 0, bez skipów. Rzeczywisty supervisor + bounded_stdio_client + ClientSession + walidator + containment z lokalnym peerem JSON-RPC: token RAM preview→apply w jednym procesie/generacji, reuse discovery, EOF w bezczynności, timeout po dispatch uncertain bez retry, invalidation i shutdown zabijają serwer i potomków. To odbiór istniejącej implementacji bez fazy RED. Nie dowodzi API/TaskManager/WorkSession, admin isolation, zgód, wyników, nested jobs, POSIX ani hard-crash. Szczegóły i P3c-A: sklr docs/plan/CURRENT.md i backlog.md.

Następny krok: przegląd P3c-A zrobiony (nic nie zapisano). Zaakceptowano zapis testów RED granicy dispatch (tests/external_mcp_persistent_admission_helpers.py i tests/test_external_mcp_persistent_admission.py; prawdziwy TaskManager + SessionController, bez subprocessów); pliki jeszcze nie istnieją. Uwaga: supervisor startuje z pustym ContextVar, więc kontekst autopilota per operacja trzeba przenieść jawnie; lifecycle nie może trzymać blokad manager/controller czekając na supervisor. persistent_runtime_required, profile/config/upstream/SKILL.md i zapis flow bez zmian; FlowAgent nie wywoływany. Nowy czat nie dziedziczy zgody na zmiany ani pytest.

## Wcześniejsze przekazanie — S1/S2a/S2b/S3 z atrapami GREEN

Najnowsze S3: #2781 helper wire + 10 testów integracji rzeczywistego supervisora,
bounded_stdio_client i ClientSession SDK z atrapami procesu/containment.
Pierwszy RED #2782: 9 failed, 1 passed, client_failed w discovery; #2783 poprawiło
helper (numeryczny read_timeout_seconds zamiast timedelta). Miarodajne RED #2784:
2 failed, 8 passed, 3513 deselected / 6.86 s pytest, wrapper 8.5 s, exit_code 1:
brak unieeważnienia generacji po EOF w bezczynności i nowy budżet cleanup transportu
po wyjściu sesji. #2785 zmieniło supervisor i stdio_transport: publiczny wrapper receive
sygnalizuje utratę połączenia, RAM ContextVar przekazuje jeden monotonic deadline
cleanup. GREEN/regresja #2786 przez centralny test-runner, istniejący venv
Windows/Python 3.14.7: 78 passed, 3445 deselected / 20.71 s pytest, wrapper 21.5 s,
exit_code 0, bez skipów/błędów (10 S3 + 40 S1/S2a + 28 stdio/S2b).
To atrapy procesów, nie prawdziwe subprocessy/Job Objects/POSIX groups/API/WorkSession.
Współdzielony stdio_transport zmieniono, więc brakuje osobno uzgodnionej regresji
one-shot na rzeczywistym lokalnym SDK. Nie powtarzać #2750/#2756/#2770/#2778/#2786.
Pełne MCP-P2, P3a/P3c/P4/P5 otwarte; fallback persistent_runtime_required i zapis
flow nadal zablokowane; bez instalacji/restartów/zmian profili/configu/upstream.

Poniżej wcześniejsze S2b/S2a/S1.

Wcześniejsze S2b: #2773 12 testów w test_external_mcp_persistent_stdio.py.
RED #2774: 8 failed, 4 passed, 3501 deselected / 12.59 s pytest,
wrapper 15.1 s, exit_code 1, bez skipów/setup/teardown errors.
#2775 poprawka wyłącznie współdzielonego stdio_transport.py: jeden deadline
stdin/wait/terminate/kill/aclose, pojedynczy cleanup partial start/cancel i
bezpieczne błędy containment. Nie podłączono supervisora/API.
#2776: 27 passed, 1 failed / 18.24 s — nowe S2b zaliczone, stara asercja
survivor nie uwzględniała próby close. #2777 osobno zaakceptowana zgodność:
terminate/kill/close, nadal stdio_cleanup_failed, returncode=None i stdin.closed.
GREEN #2778 przez centralny test-runner, istniejący venv Windows/Python 3.14.7:
28 passed, 3485 deselected / 17.55 s pytest, wrapper 20.6 s, exit_code 0,
bez skipów/błędów. 12 S2b + stdio/parse/containment regresja z atrapami;
bez subprocessów, prawdziwych Job Objects/POSIX groups/sieci/FlowAgent.

Następny krok do osobnego uzgodnienia: RED integracji supervisor–stdio/session
oraz wspólnego deadline całego cleanup. Potem rzeczywisty lokalny fake przez SDK
i regresja one-shot po zmianie współdzielonego transportu. Nie traktować
osobno zielonych S1/S2a i S2b jako odbioru ich integracji.
Pełne MCP-P2, dalsze P3b/P3c/P4/P5 otwarte. Bez instalacji/restartów,
zmian profili/configu/upstream/SKILL.md; persistent_runtime_required i zapis
flow nadal zablokowane. Nowe zmiany/testy i operacje flow osobno.

Poniżej wcześniejsze S2a/S1; najnowszy wynik stdio to #2778.

Wcześniejsze S2a: #2766 nowy test_external_mcp_persistent_failures.py (10 przypadków),
#2767 partial-entry bariery i cleanup atrap. RED #2768: 4 failed, 6 passed,
3491 deselected / 12.59 s pytest, wrapper 14.4 s, exit_code 1, bez skipów
ani błędów setup/teardown. Reprodukcje: cancel initialize i cleanup anulowanego
startu, pending acquire po safety timeout i shutdown podczas starting.
Trzy warianty EOF przeszły; podejrzenia zgubienia wyniku EOF nie odtworzono.
#2769 poprawka wyłącznie supervisora: start cancellation oddzielone od cleanup,
shutdown/invalidation przerywa start, abandoned/cancel bridge oraz jeden
synchroniczny safety deadline. Bez osłabiania testów/asercji.
GREEN/regresja #2770 przez centralny test-runner w istniejącym venv Windows,
Python 3.14.7: 40 passed (10 S2a + 30 S1), 3461 deselected / 20.19 s pytest,
wrapper 22.9 s, exit_code 0, bez skipów/błędów. Atrapy bez subprocessów/sieci;
nie pełne repo, nie SDK/transport/containment ani Power Automate.

S2b wykonano później w zakresie opisanym na początku; integracja z supervisorem
oraz rzeczywiste procesy/SDK pozostają do osobnego odbioru.
Pełne MCP-P2 otwarte, rzeczywista ścieżka API/TaskManager/SDK i P3c nieodebrane.
Nie podłączono transportu, nie zmieniono profili/configu/upstream/SKILL.md,
nie powtarzano #2750/#2756 ani PoC. Fallback i zapis flow nadal zablokowane.
Nowe zmiany/testy, instalacje i operacje flow wymagają osobnego uzgodnienia.

Poniżej wcześniejszy zakres S1; najnowszy wynik to #2770.

Wcześniejsza kontynuacja: MCP-P2-S1/P3b-S1 osobno zaakceptowano i wykonano.
#2759 zapisano trzy pliki testowe (17 supervisor + 13 lifecycle); #2760 korekta
atrapy walidatora. RED #2761: 30 errors przy setup, 3461 deselected / 1.32 s,
wrapper 2.7 s, exit_code 1: brak modułu supervisora, bez błędu collection/skipów.
To RED braku implementacji/API, nie wykonanie asercji zachowania.
#2762 zapisano odłączony external_mcp/persistent_supervisor.py: lazy wątek/pętla,
zadanie-właściciel transportu/sesji, slot, generacja, idle/EOF/invalidation,
drain i ograniczony cleanup. Wymaga jawnego wstrzyknięcia transportu/fabryki sesji.
GREEN #2763 przez centralny test-runner, istniejący venv Windows/Python 3.14.7:
30 passed, 3461 deselected / 10.72 s pytest, wrapper 11.9 s, exit_code 0,
bez skipów/błędów. Wyłącznie dwa nowe moduły; atrapy bez sieci/subprocessów.
To częściowe P04/P06/P07/P08/P09, nie pełne MCP-P2 ani rzeczywisty persistent stdio.
Nie podłączono SDK stdio, API/panelu/WorkSession ani nie usunięto blokady fallbacku.
Nie powtarzano #2750/#2756 ani PoC. Zapis flow nadal zablokowany.

Następny krok do osobnego uzgodnienia: przegląd luk i testy RED adaptera stdio
z shared-deadline cleanup oraz dalszych kontroli API/WorkSession. Rzeczywista
ścieżka API→TaskManager→SDK→lokalny fake, admin isolation, zgody/delegacje,
wyniki/cache/TTL i containment z potomkami/nested jobs pozostają nieodebrane.
Nie przełączać flowagent ani usuwać bramki SKILL.md. Nowe zmiany/testy wymagają
osobnej akceptacji; instalacje i operacje flow osobno.

Poniższe potwierdzenia konfiguracji i fallbacku pozostają aktualne.

Ta sekcja jest nadrzędna wobec wcześniejszego opisu draftu poniżej.
W sklr zaakceptowano szczegółowy kontrakt persistent stdio i etapy P1–P5;
docs/specs/external-mcp-persistent-stdio-proposal.md ma status stable dla wymagań,
nie verified runtime. Źródła aktualnego stanu i jedynej kolejki prac:
root sklr docs/plan/CURRENT.md oraz docs/plan/backlog.md.

Osobno zatwierdzono i wykonano dwie małe paczki kodu/testów:
- profiles.py/registry.py: stdio_mode, domyślny one-shot, odczyt v1/v2 bez zapisu,
  zapis v2 z rollback, zgodny legacy digest i utrata trust po zmianie trybu.
  RED #2746: 11 failed/16 passed; GREEN/regresja #2750: 167 passed,
  3291 deselected / 3.10 s, bez skipów i błędów.
- client.py/server external_mcp_tools: tymczasowa odmowa persistent przez
  dotychczasowy klient one-shot, przed env/transportem, bezpieczny kod
  persistent_runtime_required. RED #2754: 3 failed; GREEN/regresja #2756:
  113 passed, 3348 deselected / 7.86 s, bez skipów i błędów.
Testy przez centralny test-runner w istniejącym sklr venv, Windows/Python 3.14.7.
To wybrane paczki z atrapami i plikami tmp, bez sieci/subprocessów; nie pełne repo,
nie działający persistent transport i nie odbiór Power Automate preview/apply.

Nie zmieniono lokalnego config, profili, upstream ani SKILL.md; bez instalacji,
restartów/serwerów i operacji FlowAgent. Read-only PoC nie powtarzano.
Odłączony supervisor P3b-S1 jest zapisany i sprawdzony tylko z atrapami powyżej.
Rzeczywisty transport, panel trybu, routing generacji i wiring WorkSession
są nieodebrane/niewdrożone. Nie przełączać flowagent ani nie usuwać blokady zapisu.

Domknięto konfigurację, blokadę fallbacku i częściowe P3b-S1 z atrapami,
ale całe MCP-P2 (RED lifecycle/API/fake) nie jest ukończone. Nie traktować
30 zielonych testów jako odbioru rzeczywistego procesu/SDK/containment.
Kolejne paczki zgodnie z CURRENT/backlog; P3a panel i P3c API/WorkSession osobno.
Nie pomijać RED dalszych zmian na podstawie #2750/#2756/#2763.
Nowy czat nie dziedziczy automatycznej zgody na kolejne zmiany, pytest ani flow.

Wejście: list_roots/list_skills → activate_skill(power-automate) → ten START
oraz implementation-plan → prepare_project(sklr) i pełne AGENTS → CURRENT,
backlog, external-mcp-support i zaakceptowany persistent-stdio kontrakt.
Sprawdzić bieżący kod i ustalić następną paczkę/akceptację; nie powtarzać PoC.
Zapis flow nadal zablokowany do odbioru ciągłości i preview/apply;
run/publish/resubmit, instalacje i zdalne mutacje osobno.

## Cel i uzgodniony zakres

Użytkownik chce umożliwić LLM edycję i debug Power Automate cloud flows przez sklr.
Repo upstream Microsoft jest udostępnione jako root pp. W tej sesji zlecono
utworzenie skilla power-automate w root dev-skills i docs/ z planem OKF, tak aby
kontynuować w nowym czacie bez całej historii rozmowy.

Początkowa zgoda obejmowała paczkę instrukcji i walidację skilla. W kontynuacji
2026-11-07 użytkownik osobno uzgodnił read-only PoC, rozszerzenie walidatora
external MCP o draft-07, wybrane testy pytest i aktualizację dokumentacji.
Profil, zmienne Windows, proxy i restarty skonfigurował użytkownik.
Użytkownik następnie wybrał kierunek modyfikacji sklr: opcjonalne utrzymywane
stdio osobno dla profilu, tryb jednorazowy domyślny, HTTP/SSE bez zmian.
Zlecono dokumentację do kontynuacji w nowym czacie, nie implementację.
Projekt kontraktu zapisano w root sklr:
docs/specs/external-mcp-persistent-stdio-proposal.md (draft).
Szczegóły lifecycle/limitów/izolacji wymagają przeglądu i akceptacji przed kodem.
Nie uzgodniono instalacji, zmian upstream, nadania trust ani zdalnych
mutacji/run/test flow. Kolejne uruchomienia wymagają aktualnego zakresu zgody;
odbiór i historyczne polecenie pytest nie nadają zgody na nowe testy.

## Aktualny stan

- Skill 0.1.0: instrukcyjny szkielet diagnozy i przygotowywania zmian; brak skryptów.
- Zapis zdalny domyślnie ZABLOKOWANY do odbioru ciągłości sesji oraz preview.
- Audyt statyczny odpowiednich fragmentów kodu wykonany; nie jest testem działania.
- Read-only PoC potwierdzony 2026-11-07: profil flowagent, enabled, stdio,
  bez trust. Node v24.14.0, Azure CLI 2.67.0, FlowAgent 3.2.10,
  runtime buildId ea6783e65083 (nie pełny hash bundle ani SHA checkoutu).
- Sklr rozszerzono o prawidłowy Draft7Validator przy zachowaniu 2020-12,
  zgód/trust/digestów i zabezpieczeń. Test-runner #2715: Python 3.14.7 Windows,
  214 passed, 3217 deselected / 29.96 s, bez błędów/skipów; wybrana regresja,
  nie pełne repo. Testy dodano przed implementacją, ale nie wykonano RED.
- whoami #2719: użytkownik potwierdził właściwe konto; token i Azure CLI zgodne.
  Osobny MSAL Connectivity nie był ustawiony; brak odbioru connection commands.
- Doctor początkowo zgłaszał ENOTFOUND. Użytkownik skonfigurował px
  http://127.0.0.1:3128 i Node --use-env-proxy. Doctor #2726: dostęp do
  środowisk pass (78), jedynie oczekiwany warn current-env. Nie użyto reconnect.
- Odczyt wybranego przez użytkownika Personal Productivity (DEFAULT), Europe:
  list_environments #2728 -> list_flows #2729 -> get_run_history(top=5) #2730
  -> get_flow(path=properties.definition.actions) #2731. PBIdaxTest: ręczny
  trigger, włączony, jedna akcja Power BI ExecuteDatasetQuery. Zwrócono jeden
  run Succeeded, 2026-11-07 12:34:41–12:34:47 UTC, około 5.48 s.
  Brak obserwowanego failed run; nie wykonano diagnose_run/get_run_actions.
- Używano jawnych env/flow, bez set_current_env/set_current_flow. Nie pobrano
  recorded inputs/outputs ani wyników DAX. Nie wykonano zdalnych zmian ani runów.
  Sukces runu nie dowodzi poprawności jego danych; run uruchomił użytkownik.
- Katalog mieścił się w kanale modelu; discovery około 0.8–1 s, odczyty około
  1.7–5.3 s, doctor przez px około 6.6 s. Brak pełnego benchmarku cold/warm,
  dokładnych rozmiarów i odbioru błędów/pętli/dużych flow/backup/preview.
  Stan profilu, roots i sesji należy ponownie sprawdzać w nowym czacie.
- Zapis paczki potwierdzony: task 2703, done, exit_code 0; 3 katalogi i 6 plików.
- Walidacja SKILL.md potwierdzona: skill-writer / scripts/validate_skill.py,
  task 2704, done, exit_code 0, 0 błędów i 0 ostrzeżeń. To walidacja skilla,
  nie test integracji FlowAgent ani automatyczna walidacja wszystkich dokumentów OKF.
- Widoczność potwierdzona przez list_skills i activate_skill: source dev,
  wersja 0.1.0, bez ostrzeżeń; odczyt docs/START.md i obu references działa.
- Dokumenty docs: statycznie sprawdzono frontmatter i lokalne linki względem
  zapisanej struktury. Nie wykonywano oddzielnego automatycznego walidatora OKF.
- Plan architektoniczny pozostaje propozycją; utworzenie dokumentu nie akceptuje
  automatycznie zmian kontraktu external MCP sklr.

## Najważniejsza blokada

Sklr external MCP otwiera sesję na jedną operację i zamyka proces stdio. FlowAgent
trzyma preview tokens w Map w RAM. Preview w procesie A -> zapis w procesie B
z tym tokenem daje PreviewTokenNotFound. Dotyczy preview_update -> update_flow
oraz edit_flow(dryRun=true) -> edit_flow(previewToken=...). Jawne env/flow pomaga
odczytom, ale NIE naprawia tokenu. Licznik bulk delete też jest sesyjny.

Wniosek: panel stdio odebrano jako PoC odczytowy w opisanym małym zakresie.
Przed bezpieczną edycją potrzebna jest świadomie wybrana i odebrana ciągłość procesu
oraz kontekstu. Nie omijamy problemu przez zapis bez tokenu.

## Konfiguracja odebranego PoC (bez sekretów)

Profil flowagent: command node, args = --use-env-proxy oraz osobny argument
z bezwzględną ścieżką plugins/power-automate/server/mcp.mjs w aktualnym root pp.
Cwd pusty. Env_refs: FLOWAGENT_TELEMETRY -> FLOWAGENT_TELEMETRY oraz
HTTPS_PROXY -> SKLR_FLOWAGENT_HTTPS_PROXY. Użytkownik ustawił źródła w Windows:
FLOWAGENT_TELEMETRY=0, SKLR_FLOWAGENT_HTTPS_PROXY=http://127.0.0.1:3128.
Zmienne muszą istnieć w działającym sklr; w panelu linie NAZWA=ZMIENNA,
nie same nazwy ani wartości. Proxy musi działać. TLS pozostaje weryfikowany.
Odebrano łączność fetch, nie wszystkie ścieżki MSAL/CLI ani niezależny audyt
wyłączenia telemetrii. Nie zapisuj kont, tenant/env/flow/run GUID ani payloadów.

## Mapa zasobów

Wszystkie pliki paczki są wewnątrz katalogu skilla. Czytaj przez
read_skill_resource(name="power-automate", path="docs/START.md").

- [Plan implementacji i kryteria](implementation-plan.md): etapy, decyzje,
  kontrakty, lifecycle, zgody, limity, testy i Definition of Done.
- [Audyt i źródła](audit.md): potwierdzenia w kodzie, rozbieżności, hipotezy i braki.
- [Integracja sklr](../references/sklr-mcp.md): użycie tools, zgody i env/auth.
- [Bezpieczeństwo flow](../references/flow-safety.md): reguły domenowe i realne skutki.

Nie zakładaj, że zagnieżdżone docs/ skilla jest automatycznie bundle OKF. Dostęp
przez read_skill_resource działa bez indeksu. Jeśli użytkownik chce wyszukiwanie
OKF, może osobno podłączyć ten katalog w panelu Wiedza; model nie nadaje dostępu.

## Roots do ponownego sprawdzenia

| Alias | Zastosowanie |
|---|---|
| dev-skills | Tu znajduje się power-automate; katalog developowanych skilli. |
| pp | Checkout microsoft/power-platform-skills; plugin plugins/power-automate. |
| sklr | Repo serwera; external_mcp/, server/, panel/, tests/, docs/. |

Zawsze pobierz list_roots; nie kopiuj historycznych ścieżek jako stałych do kodu.
Roots mogą być odrębnymi repo Git. Katalog dev jest już skanowany w tej instalacji,
ale widoczność nowego skilla sprawdź list_skills. Wdrożenie do centralnego folderu
robi użytkownik; nie kopiuj go automatycznie.

## Procedura kontynuacji

1. list_roots -> list_skills -> activate_skill("power-automate").
2. Przeczytaj ten dokument i nagłówki planu; dobierz dalsze sekcje do zadania.
3. Przed pracą w repo użyj prepare_project dla odpowiedniego roota/subpath.
   Przeczytaj pełne obowiązujące AGENTS (również zagnieżdżone).
4. W sklr źródła kontraktów: docs/specs/external-mcp-support.md i
   docs/specs/skills-support.md; bieżący stan docs/plan/CURRENT.md;
   jedyny backlog docs/plan/backlog.md. Przez OKF wybierz bundle i czytaj outline/sekcje.
5. Sprawdź profile external MCP; nie traktuj starego stanu jako aktualnej zgody.
6. Następny krok: przegląd projektu kontraktu w root sklr
   docs/specs/external-mcp-persistent-stdio-proposal.md i kontraktu v1.
   Kierunek opt-in persistent stdio jest wybrany, ale szczegółowy kontrakt,
   migracja profilu, runtime async, idle TTL, izolacja, drain/cleanup i sygnał
   utraty ciągłości wymagają akceptacji przed implementacją.
   Prace wymagają zmian sklr (external MCP, panel i integracja lifecycle
   WorkSession), nie samego skilla. One-shot pozostaje domyślny.
   Najpierw uzgodniony kontrakt/plan → testy fake MCP RED → implementacja →
   GREEN/regresja → odbiór lifecycle → osobny plan bezpiecznego flow i
   preview/zgoda/apply/backup/readback. Pytest tylko na nowe wyraźne polecenie.
   Nie powtarzać odebranego PoC bez nowej przyczyny; nie odblokowywać zapisu.
7. Po wyborze uzgodnić plan, środowisko i skutki, dopiero potem pisać/uruchamiać.

Przykładowa wiadomość rozpoczynająca nowy czat:

> Kontynuujemy integrację Power Automate z sklr. Aktywuj skill power-automate
> z dev-skills, przeczytaj docs/START.md i docs/implementation-plan.md.
> Read-only PoC jest odebrany. Chcemy opcjonalne persistent stdio w sklr,
> z one-shot jako domyślnym i bez zmian HTTP/SSE. Użyj prepare_project dla sklr,
> przeczytaj CURRENT/backlog, docs/specs/external-mcp-support.md i projekt
> docs/specs/external-mcp-persistent-stdio-proposal.md. Zweryfikuj kod i
> zaproponuj szczegółowy kontrakt oraz etapowy plan do akceptacji przed kodem.
> Bez instalacji, testów i operacji na flow bez osobnego uzgodnienia.
> Zapis flow pozostaje zablokowany do odbioru ciągłości sesji i preview.

## Aktualizacja stanu i ograniczenia narzędzi

Zapisuj rzeczywiste wyniki i następny krok w tym pliku, otwarte zadania tylko
w planie (dla paczki) oraz właściwym backlogu sklr (jeśli zmienia się sklr).
Nie dopisuj nieaktualnych checkpointów jako równorzędnego źródła stanu.
Dokumenty docs pozostają OKF; brak verified nie oznacza błędu. Nie nadawaj
verified ani nie deklaruj odbioru bez wykonanej weryfikacji.

Przy update OKF przez knowledge_save pobierz pełny document bez truncated,
zachowaj resztę body i użyj świeżej rewizji; odczyt sekcji nie jest całym body.
Jeśli katalog nie jest bundle, używaj zatwierdzonych dokładnych edit_file.

Ręczna zgoda: jedno wait_task(seconds=6) na task_id, potem STOP gdy brak decyzji.
Po decyzji queued/running śledź ten sam task; sukces przez get_task.
W sklr pytest oraz serwer/API wymagają wyraźnego polecenia użytkownika. Do testów
użyj test-runner po sprawdzeniu jego instrukcji; nie instaluj środowisk samodzielnie.
