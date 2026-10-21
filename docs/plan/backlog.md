---
type: Implementation Plan
title: Aktualny backlog projektu sklr
description: Jedyna lista otwartych prac, odbiorów i propozycji dalszego rozwoju sklr.
tags: [sklr, backlog, planning]
---
# Aktualny backlog — 2026-10-18

Jedyna lista otwartych prac. Stan wdrożenia: [CURRENT](CURRENT.md); historyczne zadania i wyniki: [archiwum](../archive/README.md). C1–C3 i minimalne P0/P1/P2 są zaimplementowane w `e6cee29`; nie są ponownie otwartymi paczkami kodu.

## Wstrzymanie, autopilot i testowanie — zaakceptowany kierunek

Użytkownik zaakceptował kierunek i kolejność poniżej. Kontrakt: [wstrzymanie i checkpointy](../specs/work-session-pause.md). Fundament P1 zapisany i odebrany w paczce opisanej w CURRENT; funkcje użytkowe nie są wdrożone. Przed kolejnymi zmianami kodu przygotować konkretny plan paczki. Akceptacja kierunku nie nadaje nowych uprawnień ani nie zmienia obecnych zasad zatwierdzeń i uruchamiania testów.

- [x] P1: odłączone modele i magazyn metadanych sesji, rewizje, powiązania z zadaniami, jawne odzyskiwanie po restarcie oraz testy fundamentu; zakres odbioru w CURRENT. Bez integracji z działającym przepływem.
- [x] P2a: odłączony kontroler jednej sesji na instancję, atomowy start/pauza, licznik rozpoczętych działań, blokada obejścia przez ponowne przypięcie i fail-closed; zakres odbioru w CURRENT. Użytkownik zaakceptował przypisanie po stronie serwera/panelu, bez session_id w argumentach modelu. Pauza obejmuje wszystkie rozmowy tej instancji; bez izolacji równoległych rozmów.
- [x] P2b: opcjonalna integracja kontrolera z TaskManager: przypisanie przed scheduling, bramka dispatch, zachowanie queued bez ponowień/zajmowania workerów, tokeny w finally, bezpieczna obsługa częściowego zapisu i błędu finalizacji. Manager bez kontrolera kompatybilny; integracja nieaktywna w main.py. Wynik i ograniczenia odbioru w CURRENT.
- [x] P2c: odłączony wrapper, jawna klasyfikacja narzędzi roboczych/sterujących, blokada wywołań i osobny komunikat pauzy; sygnatury i poufna telemetria. Poprawka reason oraz atrap kolejek testowych; zakres i ograniczenia odbioru w CURRENT. Bez aktywacji w main.py.
- [ ] P2c — diagnostyka czasu: użytkownik potwierdził zieloną paczkę, ale czas 479.62s wobec wcześniejszych 28.60s/8.31s wymaga --durations (setup/call/teardown), ewentualnie odrębnego pomiaru kolekcji/startu. Przyczyna nieustalona; nie podnosić timeoutów zamiast diagnozy.
- [x] P2d1: odłączony adapter administracyjny i opcjonalne API panelu start/status/pause, jawny wybór roots, auth/Host/Origin, konflikty i safe errors; zakres odbioru w CURRENT. Bez UI i aktywacji main.py.
- [ ] P2d: panel wyboru jednej sesji i pauzy, licznik rozpoczętych działań, ostrzeżenie o zakresie całej instancji; bez wznowienia/przełączenia omijającego pauzę. Bootstrap/restart oraz integracja w main.py dopiero z testami poprzednich paczek. Ręczny odbiór harnessu nie jest zastępowany atrapami.
- [ ] P3: ograniczony zapis checkpointu oraz wznowienie z uzupełnieniem/replanowaniem; domyślnie treść w RAM, trwały zapis swobodnego podsumowania wyłącznie po osobnej zgodzie. Ręczny odbiór rzeczywistego harnessu.
- [ ] A1: „Wstrzymaj po kroku”, oddzielnie od natychmiastowego anulowania. Blokowanie nowych działań, bezpieczne zakończenie trwającego kroku w limicie. Kontrakt ma określić zakres sesji/projektu, wyścigi dispatch/stop, restart i niepewne wyniki MCP bez automatycznego ponawiania.
- [ ] A1b: checkpoint i wznowienie z uzupełnieniem: cel, etap, ustalenia ze źródłami, wykonane działania, odnośniki do wyników/logów, pytania i następny krok. Ustalić retencję bez kopiowania sekretów i poufnych wyników RAM/TTL do trwałych zapisów. Nie obiecywać utrwalenia pełnego kontekstu modelu. Rozdzielić kontrolę dispatch w sklr od współpracy harnessu i pętli wewnątrz skryptu/MCP. Wznowienie po aktualizacji planu i ponownej kontroli kontraktu.
- [ ] A2: autopilot według kontraktu projektu/sesji, jawnie włączany i odwoływany przez użytkownika. Zakres roots, działań, konkretnych narzędzi/argumentów, budżetów i wymaganych decyzji. Model nie rozszerza uprawnień; trust nie oznacza zgody na wszystko. Sekrety, produkcja, publikacja, płatności i destrukcyjne operacje domyślnie wyłączone. Dziennik, wstrzymanie i kontrola kontraktu przed wykonaniem. Zmiany obecnych zasad zgód wymagają jawnego kontraktu przed implementacją.
- [ ] A3: konfiguracja testów w każdym projekcie: osobno framework, zakres i uprawnienia. Domyślnie unittest dla prostych skryptów/skilli, pytest dla API/MCP/złożonych aplikacji; zachować istniejący framework. Unit/integration/end-to-end niezależne od frameworka. Interpreter projektowego venv/conda i polecenia odpowiednie dla platformy. Venv nie jest sandboxem. Bez automatycznych instalacji, tworzenia środowiska i dostępu do produkcji.
- [ ] A3b: ograniczony wykonawca testów według kontraktu poleceń, nie dowolny shell. Testowanie etapami: szybkie kontrole i zmieniona logika przed szerszym zestawem. Logi według polityki poufności; model otrzymuje skrót i potrzebny kontekst błędów. Jawne timeouty, przerwania, pominięcia i ucięcia. Limity czasu, wywołań i logów; budżet tokenów po stronie harnessu, jeśli sklr nie ma danych o zużyciu. Wybrane testy nie oznaczają pełnego odbioru.
- [ ] A4: automatyczne poprawki jako osobne uprawnienie; proponowany limit do 3 cykli poprawka → test, ostateczne wartości w kontrakcie projektu. Brak postępu, niejasne wymaganie lub przekroczenie zakresu → wstrzymanie. Nie zmieniać oczekiwań testów tylko dla zielonego wyniku. Testować zachowanie proporcjonalnie do projektu.

Kolejność: A1/A1b → A2 → A3/A3b → A4. Odbiór obejmuje scenariusze graniczne i regresje zgód, poufności i anulowania. Dotychczasowe otwarte odbiory nie zostają uznane za zakończone.

## Zewnętrzne MCP — zaakceptowana pierwsza wersja

Zakres zaakceptowany przez użytkownika; kontrakt: [zewnętrzne MCP](../specs/external-mcp-support.md). M1–M5 są zaimplementowane; użytkowy smoke HTTP z web_mcp (katalog i zaufane list_open_tabs) zaliczony. Pozostały odbiór M6 i ograniczenia poniżej; nie jest to pełna gotowość produkcyjna. Ten zakres ma obecnie priorytet na prośbę użytkownika; nie oznacza zakończenia odbioru OKF.

- [x] M1: trwały rejestr profili w external_mcp, walidacja HTTP/SSE/stdio, rewizje i konflikty, odwołania do zmiennych środowiskowych; testy zapisu, rollbacku i restartu. Bez połączeń przy zapisie i starcie. Zakres potwierdzonej paczki i ograniczenia odbioru w CURRENT.
- [x] M2: adapter HTTP/SSE/stdio, limity, TLS, ograniczone env i sprzątanie; brak retry call_tool. Dalsze odbiory M2f/M2g poniżej.
- [x] M2c: kontrola procesów potomnych stdio po timeoucie/anulowaniu — odebrana na Windows (Job Object, test wnuka przechodzi). Otwarte: uruchomić testy POSIX na Linuksie (grupa procesów), sprawdzić zagnieżdżone joby i uruchomienie sklr w zewnętrznym jobie. Panel i dokumentacja mają nadal mówić, że to nie sandbox.
- [x] M2d: test HTTP/SSE na prawdziwym SDK z lokalnym serwerem — odebrany na Windows (6 passed). Otwarte: test limitu 4 MiB rozróżniający przyczynę odmowy, TLS z własnym CA, Bearer/nagłówki na prawdziwym serwerze, długa sesja SSE.
- [x] M2e: kontrola adresu docelowego po rozwiązaniu nazwy (link-local/metadane/multicast/zarezerwowane zawsze; loopback i adresy maszyny tylko na portach sklr/panelu; prywatne dozwolone) — odebrana na Windows (54 passed). To nie jest pełna ochrona przed SSRF: patrz M2f.
- [ ] M2f — dalszy odbiór przypinania IP: autorytatywny zmienny DNS, NAT64/6to4, lokalne interfejsy VPN, koszt DNS, retencja połączeń i zgodność wewnętrznego AnyIOBackend po aktualizacji httpcore2. Potwierdzić wyniki ponowionych paczek po #592/#594 (użytkownik zgłosił działający panel, bez liczników pytest). Kontrolowany resolver z rzeczywistym TCP odebrany, localhost/IPv4 fallback działa w smoke. Doprecyzować informację o ograniczeniach w panelu; bez deklaracji pełnej ochrony SSRF.
- [ ] M2g: pozostałe luki odbioru M2: test limitu odpowiedzi 4 MiB rozróżniający przyczynę odmowy (dziś akceptuje dwa kody), TLS z własnym CA i błędnym certyfikatem, Bearer/nagłówki na prawdziwym serwerze, długa sesja SSE, testy POSIX i zagnieżdżone joby Windows.
- [x] M1b (zaufanie wdrożone w panelu i narzędziach, wyniki w CURRENT): rozszerzyć profil o zaufanie (sekcja 5a kontraktu): pole z zapisanym zbiorem narzędzi i skrótami schematów, skrót ustawień połączenia do zerowania przy edycji; walidator dziś odrzuca nieznane pola, więc wymaga zmiany walidacji, wersji sekcji i testów migracji. Nadawanie zaufania dopiero w panelu (M4); model nie ma dostępu.
- [x] M3 (kanał external_result używany przez narzędzia modelu; wyniki w CURRENT): osobny poufny kanał external_result w kolejce i get_task (RAM/TTL, bez surowych argumentów i wyników w SQLite lub telemetrii); zachować kontrakt secret_result i regresje istniejących zadań.
- [x] M3b: test poufnej telemetrii get_task dla external_mcp wdrożony i odebrany. Decyzja v1: wynik ponad 80 000 znaków odrzucany, bez obcinania.
- [x] M4a (odebrane na Windows, 183 passed, fałszywy klient): API administracji w panelu — patrz CURRENT.
- [x] M4b: interfejs zakładki w przeglądarce (lista, formularz zależny od transportu, włączanie, usuwanie z potwierdzeniem, bezpieczne renderowanie tekstu z zewnątrz) oraz test, że prawdziwa sygnatura client.fetch_catalog zgadza się z wywołaniem w external_mcp_admin.
- [x] M4c: test połączenia, katalog i nadanie trust w panelu; ręczny sukces oraz zaufane wywołanie modelu potwierdzone. Pełny zbiór trust/cofnięcie do odbioru w M6.
- [x] M4: administracja i zakładka panelu — dodawanie, edycja, trwałe włączenie/wyłączenie, usuwanie, test i katalog narzędzi w tle; rewizje, autoryzacja i bezpieczne renderowanie.
- [x] M5a: walidacja JSON Schema przed dispatch w workerze 7 s, odebrana paczkami i rzeczywistym procesem na Windows. Ograniczenia dialektu/refs/RAM w CURRENT.
- [ ] M5a — dalszy odbiór: osłonięty spawn/attach i total timeout klienta, POSIX/zagnieżdżone joby, regresje stdio po podłączeniu walidatora; bez deklaracji izolacji pamięci.
- [x] M5b: list_external_mcp, list_external_mcp_tools i call_external_mcp_tool; integracja kolejki/profili, kontrola rewizji i zaufania tuż przed dispatch, limiter 30 zaufanych wywołań/min (ponad limit zwykła zgoda), poufna telemetria i wyniki RAM. Bez narzędzi administracyjnych dla modelu.
- [ ] M6: pełny odbiór HTTP/SSE/stdio przez narzędzia modelu, zmiana projektu, cofnięcie/ponowne trust, pełny zapisany zbiór zaufania, nawigacja open_url, zmiana schematu, limiter i wynik TTL na rzeczywistym serwerze, sekrety, konflikty, timeout/cancel oraz pełna regresja HEAD. Restart/trwałość enabled/trust, katalog 9 i list_open_tabs bez zgody zaliczone. Pytest i serwer uruchamia użytkownik.
- [ ] Ustalić powód czasu paczki panelu około 5 min (--durations); użytkowy katalog/wywołanie również mierzone w CURRENT. Nie zwiększać timeoutów zamiast diagnozy.

## Odbiór i przygotowanie konwersji

- [ ] Dokończyć ręczny odbiór polityki formatu dokumentacji: stare projekty w compatible, zakres i wyjątki require_okf, restart i zmiana aliasu, zapis po zmianie polityki podczas oczekiwania na zgodę oraz scenariusze Windows. Testy potwierdzone przez użytkownika: paczka 8 plików z jednym błędem testu, następnie zielony test_project_knowledge po jego poprawce; 2 pominięcia specyficzne dla Windows.
- [ ] Dokończyć ręczny odbiór automatycznych docs: panelowy przełącznik i jego trwałość, dodanie/usunięcie roota, zniknięcie/powrót docs, pierwszeństwo ręcznych wpisów oraz junctiony Windows. Paczka 5 plików testowych z CURRENT przeszła według potwierdzenia użytkownika.
- [ ] Wyjaśnić zmienność czasu pełnych uzgodnień (około 0,6 s do ponad 2 min). Bundle-check zoptymalizowany, pomiary dostępne, ready/35 i przyrostowe ready/36 potwierdzone. Nie wracać do zwiększania timeoutów jako podstawowej naprawy; docelowo lekka inventory i tylko zmienione źródła.
- [ ] Potwierdzić najnowsze rozszerzenia i regresje na Windows: testy i ręczny odbiór panelu/MCP, central/docs/legacy/mixed, restartu i junctionów według [instrukcji Windows](../windows-okf.md). Brak aktualnego pełnego pytest; starsze wyniki mają zakres zapisany w CURRENT.
- [ ] Dokończyć pilot centralny i projektowy oraz pomiar kontekstu katalog → search → outline/section wobec odczytu pełnych źródeł. Ręczne dodanie wiedzy centralnej jest potwierdzone, pełny pilot nie.
- [ ] Domknąć walidację wybranego zakresu konwersji: 22 dokumenty otrzymały metadane w taskach 339, 341, 343–345, 3 wcześniejsze OKF pozostawiono bez zmian. Potwierdzić format i kompletność po naprawie ograniczeń audytu/indeksu. Standard, przykłady i archiwum pozostają poza konwersją; bez docs/knowledge/ i bez automatycznego require_okf.

## Trwały indeks — pozostałe prace

SQLite v2, osobny klucz, worker, start cache_unchecked i przyrostowe knowledge_save/convert/create/edit są wdrożone. Wyniki paczek i smoke wyłącznie w CURRENT; nie otwierać ich ponownie jako niewdrożonych etapów. Kontrakt: [trwały indeks](../specs/okf-integration/persistent-index.md).

- [ ] Następna paczka: create_tree zgłasza po committed konkretne ścieżki; błąd indeksu nie zmienia wyniku zapisu. Dodać test zachowania reszty RAM i jednego odczytu na zmieniony dokument.
- [ ] Podłączyć rename ze starym i nowym zakresem; uzgodnić usunięcia oraz wynik niepewnego rollbacku bez cichego odtwarzania starych wpisów.
- [ ] Wdrożyć okresową lekką kontrolę zmian z IDE/git pull: stat jako filtr, bez pełnego odczytu i przebudowy niezmienionych dokumentów; zachować force pełnego sprawdzenia treści. Doprecyzować interwały/backoff i nie kasować niewidzianych wpisów po częściowej inventory.
- [ ] Podłączyć purge zbioru/usuniętych dokumentów w SQLite, określić retencję i uprawnienia plików bazy; DPAPI chroni klucz, nie bazę i nie zastępuje ACL. Wprowadzić przebudowę i ewentualne staging-generation DB po osobnym projekcie.
- [ ] Domknąć ręczny smoke create_file/convert oraz edycję podczas trwającego pełnego skanu. Sprawdzić retry zaległych zmian, reconcile_required, spóźnione wyniki, awarie i usunięcia.
- [ ] Potwierdzić pełną regresję bieżącego HEAD oraz rzeczywiste junctiony Windows. Po odbiorze jawnie usunąć lokalny docs/incremental-smoke-test.md i sprawdzić brak wpisu po uzgodnieniu; nie commitować smoke-test.

## Potwierdzony dług i dalszy rozwój

- [ ] Rozdzielić testy zewnętrznych skilli od testów serwera: test_cost_centers_skill.py i test_skill_validator.py przenieść do skilli po osobnym planie; bez repo conftest i zależności serwera w środowiskach skilli.
- [ ] TrustStore.trust/revoke w starszej ścieżce „Ufaj i uruchom”: zapewnić rollback pamięci przy błędzie save. Panel używa ConfigStore.update z rollbackiem; starszej ścieżki nie naprawiano w T1/T2.
- [ ] Dodać automatyczne sprawdzenia filtrów źródeł Skille: localStorage, brak/uszkodzone storage, domyślne źródła, licznik i pusty wynik, niezależność od dostępności/trust. Ręczne działanie potwierdzono, testów po zmianie filtrów nie.
- [ ] Domknąć zewnętrzne skille z użytkownikiem: cost-centers SKILL.md po vibecoder-confirm=false (dev/project zawsze pytają; central zależy od trust), kontrola dev frontmatter/task #109, obsługa .ps1 w skill-writer validator/rules i ręczny deploy git-ops. To starsze niepotwierdzone czynności poza repo; najpierw sprawdzić, czy nadal potrzebne.

## Propozycje wymagające osobnego kontraktu i priorytetu

To kierunki, nie istniejące funkcje ani warunki uruchomienia:

- [ ] Zaprojektować skill lub funkcję diagnostyki środowiska Python/SDK, aby agent mógł sprawdzać wersje pakietów, dostępne symbole, sygnatury API i ograniczone fragmenty implementacji bez proszenia użytkownika o ręczne komendy w CMD/PowerShell. Rozróżniać środowisko działającego sklr od środowisk skilli i projektowych venv/conda; zawsze raportować, które badano. Preferować wersje przez importlib.metadata zamiast pełnego pip freeze (może ujawniać prywatne URL). Ograniczyć moduły/obiekty do jawnej listy i rozmiar wyników; brak dowolnego eval/exec, instalacji, sieci, uruchamiania serwerów i odczytu wartości zmiennych środowiskowych/sekretów. Inspekcja importująca moduł może wykonać jego kod — wymaga jawnej polityki i zgody; nie traktować importu dowolnego modułu jako bezpiecznego odczytu. Motywacja: przy integracji MCP użytkownik musiał ręcznie ustalić FastMCP 4.0.11/MCP 2.3.0, zmianę nazwy streamable_http_client, sygnatury transportów i scalanie domyślnego środowiska w stdio_client. Osobny kontrakt, wybór skill vs funkcja i testy przed wdrożeniem; nie rozszerzać po cichu bieżącego M2.

- [ ] Rozszerzyć audyt/konwerter o jawny dowolny podkatalog, np. doc/, zgodnie z zakresem indeksowania profilem docs.
- [ ] Rozważyć semantyczny selector stanu i skill okf-curator, z zachowaniem [zasad kuracji](../specs/okf-integration/curation.md).
- [ ] Rozstrzygnąć opcjonalny hard guard projektu; obecnie brak AGENTS/konwersji nie blokuje pracy.
- [ ] Wrócić do niewdrożonego execute_command/command_policy i ADR 0003 oraz dalszych skilli dopiero po odbiorze OKF. Integracja zewnętrznych MCP została osobno zaakceptowana i jest opisana w aktywnej sekcji powyżej. [Wymagania i granice bezpieczeństwa komend](../specs/command-execution-proposal.md) muszą poprzedzić kod.
- [ ] Rozważyć krótkie oczekiwanie na decyzję w łącznym budżecie 8 s. Obecnie obowiązują natychmiastowy task_id i reguła jednego wait_task z AGENTS; bez zmiany kontraktu nie wprowadzać pollingu.
