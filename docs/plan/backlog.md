---
type: Implementation Plan
title: Aktualny backlog projektu sklr
description: Otwarte prace nad autonomią, przygotowanie przekazania na Windows i odłożone odbiory pozostałych funkcji.
tags: [sklr, backlog, planning, autonomy, windows]
---
# Aktualny backlog — 2026-10-23

Jedyna lista otwartych prac. Stan wdrożenia i potwierdzenia: [CURRENT](CURRENT.md). Zakończone etapy P1–P3 i M1–M5 oraz dawne plany przeniesiono do [historii backlogu](../archive/state-review-2026-10-21/backlog.md). Checkboxy oznaczają pozostałą pracę, nie brak istniejącej implementacji.

Priorytet: autonomia według kontraktu → kandydat do odbioru Windows → ręczny odbiór MCP/panelu na maszynie docelowej. Pozostałe potrzebne prace zachowujemy poniżej. Nie są automatycznie warunkami tej paczki, chyba że ujawnią błąd używanego przepływu, zgód lub poufności. Przegląd A2b3 zakończony, plan A2b4 zaakceptowany i lokalnie zaimplementowany; walidacja tej paczki i wskazana regresja Linux zaliczone. Teraz A2b5 i przygotowanie przekazania, bez poprzedzania ich zbiorczą naprawą D6–D8. D6–D8 wracają na główną ścieżkę tylko po wykazaniu blokady konkretnego scenariusza autonomii, zgód lub poufności. Plan i akceptacja przed zmianą kodu; pytest/serwery tylko na wyraźne polecenie.

## 1. Domknięcie fundamentu A1/A1b — sesje i wznowienie

- [ ] S6 — implementacja lokalna po akceptacji kontraktu i planu zakończona; [kontrakt sesji](../specs/work-session-pause.md) nie wymaga już checkpointu RAM. Paczka backendu według komendy w CURRENT zaliczona na Linux: **934 passed in 131.13s**. Pozostał rzeczywisty odbiór Windows: pauza → STOP bez zapisu → przygotowanie z potwierdzeniem liczby anulowań → potwierdzenie planu → działanie. Sprawdzić stary katalog/payload API, istniejącą bazę v1/v2 ze statusami pending/saved/unavailable, restart, niepewne potwierdzenie i brak istotnego kontekstu modelu. Node 8/8 i kontrole statyczne zaliczone; Windows nieodebrane. Bez automatycznego zapisu docs. P8/P9 oraz formularz P1/P3 pozostają osobnymi paczkami.

Pauza, panel, wznowienie i integracja main.py są wdrożone opt-in; nie implementować ich ponownie. Checkpoint sesji usunięto w S6. Zakres potwierdzeń: CURRENT.

- [ ] S2 — odbiór docelowy: rzeczywista odmowa narzędzia roboczego przed Startem i podczas pauzy; brak utworzenia nowego zadania przy odmowie; narzędzia sterujące nadal dostępne. Potwierdzić również poprawiony komunikat session_not_selected (S1 i jego paczka testów zakończone, wynik w CURRENT).
- [ ] S3 — odbiór docelowy: pauza podczas rozpoczętego działania, wynik i oddzielna metainformacja pauzy, spadek licznika do zera; brak nowego dispatch, brak automatycznego cancel/retry.
- [ ] S4 — odbiór docelowy: faktyczne awaiting_approval/queued, zgoda i jej wygaśnięcie podczas pauzy, licznik/potwierdzenie anulowań przy wznowieniu; stare zadania nie ruszają, nowe wymagają aktualnych zgód.
- [ ] S5 — regresja: pełny bieżący HEAD, rzeczywisty katalog/transport FastMCP i scenariusze graniczne kontraktu (rewizje, awarie RAM/SQLite, ponowna pauza/zmiana decyzji podczas resume_pending, niepewny confirm, restart). Testy odporności są już zapisane; nie otwierać ich jako niewdrożonej funkcji. Odróżnić testy logiki od odbioru harnessu.

## 2. Autonomia według kontraktu — dalsza implementacja

Użytkownik zaakceptował [kontrakt A2 v1](../specs/autopilot-contract.md) po korekcie timeoutów. [Kontrakt sesji](../specs/work-session-pause.md) pozostaje fundamentem; A3/A3b i A4 wymagają kolejnych kontraktów. Akceptacja A2 nie nadaje jeszcze uprawnień wykonawczych i nie zastępuje akceptacji planu paczki kodu. Kolejność: domknięcie A1/A1b → A2 → A3/A3b → A4. Odbiór Windows można przygotować etapowo; wersja z samą pauzą nie może być opisana jako ukończony autopilot.

- [x] A2b3 — przegląd wyników i granic zakończony jako wejście do A2b4. Lokalny kod i wcześniejsze 820 passed nie oznaczają pełnej zielonej regresji ani odbioru Windows; szczegóły w CURRENT i raporcie A2b3. D6–D8 nie blokują automatycznie następnej paczki.
- [x] A2b4 — implementacja i walidacja lokalna zakończone: dedykowana paczka 48 passed, szersza regresja 941 passed, DOM Node 4/4. Zakres i ograniczenia w CURRENT oraz [raporcie A2b4](../archive/state-review-2026-10-21/A2b4.md). Bez uruchomienia docelowej aplikacji i bez odbioru Windows; config.json bez zmian.
- [ ] A2b5 — odbiór częściowy Windows 2026-10-22: delegacja/panel/handshake, automatyczny zapis i odczyt w allowed oraz odmowa outside ze wstrzymaniem i bez zużycia budżetu potwierdzone w CURRENT. Nie powtarzać ich bez nowej przyczyny. Pozostałe scenariusze sekcji 10 kontraktu i instrukcji Windows, regresje zgód/trust/poufności/anulowania oraz pełna regresja HEAD nadal otwarte. Wskazana wcześniejsza regresja Linux 941 passed nie jest pełnym odbiorem. Oddzielić kontrolę sklr od zachowania harnessu i wewnętrznych pętli obcych wykonawców.
- [ ] A3: kontrakt konfiguracji testów per projekt: framework, zakres i uprawnienia osobno; zachować istniejący framework (unittest dla prostych skryptów/skilli, pytest dla API/MCP/złożonych aplikacji jako domyślne propozycje). Unit/integration/end-to-end niezależne od frameworka. Interpreter venv/conda i komendy dla platformy; bez automatycznych instalacji/tworzenia środowisk ani dostępu do produkcji. Venv nie jest sandboxem.
- [ ] A3b: ograniczony wykonawca testów według zaakceptowanego kontraktu poleceń, bez dowolnego shella. Etapy: szybkie kontrole i zmieniona logika → odpowiednia regresja. Limity czasu/wywołań/logów, przerwanie, jawne skip/truncation/timeout, poufne logi i użyteczny skrót błędów. Budżet tokenów po stronie harnessu, jeżeli sklr nie ma danych. Powiązać z [propozycją komend](../specs/command-execution-proposal.md); nie rozszerzać jej automatycznie do ogólnego execute_command.
- [ ] A4: osobne uprawnienie do poprawek, proponowany limit 3 cykli poprawka → test (wartość w kontrakcie). Brak postępu, niejasność lub przekroczenie zakresu wstrzymują pracę; nie zmieniać oczekiwań testów tylko dla zielonego wyniku.

### Uproszczenie autopilota — zaakceptowany kierunek, 2026-10-23

Odbiór sekwencji Windows 2026-10-23 zaliczony: pięć zapisów/edycji i trzy odczyty w jednej turze bez osobnych Allow ani wiadomości pomiędzy krokami, budżet used=8; wynik wskazanej paczki pytest podany przez użytkownika: 85 passed in 15.45s. Zakres/ograniczenia w CURRENT; nie zamyka pełnego A2b5/P7.

- [ ] P8 — polityka oczekiwania, osobna paczka: dla ręcznego awaiting_approval użytkownik zaakceptował jedno oczekiwanie do 6 s; jeśli nadal brak decyzji, podsumować i oddać sterowanie bez kolejnych wait, anulowania lub resubmit. Dla paczki jeden wspólny budżet, nie 6 s na plik. Po decyzji queued/running oznacza oczekiwanie na wykonanie, nie na człowieka. Uzgodnić skończony budżet oczekiwania modelu w autopilocie (30 s było propozycją, nie zatwierdzonym limitem); koniec oczekiwania nie oznacza końca/anulowania zadania. Zachować pauzę/zgody/timeouty i niepewne skutki. Ujednolicić opisy, hinty i instrukcje projektu; testy szybkiej zgody, paczki, braku decyzji, długiego wykonania i granic. Jeszcze niewdrożone. sklr nie gwarantuje dalszych tur LLM bez wsparcia harnessu.
- [ ] P9 — odebrać w rzeczywistym harnessie zadanie trwające ponad 8 s i kolejne wait tego samego task_id, a potem kolejny zależny krok, bez dodatkowej wiadomości/Allow i bez ponawiania zapisu. Test backendu zaliczony w paczce 85 testów nie zastępuje tego odbioru.

Cel użytkownika: wskazuję zapisany plan, katalogi pracy i dozwolone skille; zatwierdzam raz w panelu, informuję model w harnessie i model realizuje plan, wykonuje testy, poprawki i dozwolone działania Git bez pytań o każdy krok. Pyta tylko o istotną decyzję lub potrzebne rozszerzenie zgody. Priorytet: zachować tryb ręczny, OKF i pozostałe integracje. Użytkownik zaakceptował plan; rozpoczęto pierwszą paczkę dotyczącą blokującego preview (zakres i walidacja w CURRENT). Nie oznacza to wdrożenia nowego kontraktu ani rozszerzenia uprawnień obecnego A2; reszta poniższego zakresu pozostaje otwarta.

Zgłoszenie z tej sesji: po odblokowaniu formularza „Przygotuj podgląd” pozostawiło edycję zablokowaną przez dłuższy czas; użytkownik nie zauważył błędów w Network, przyczyna nieustalona. Użytkownik odłożył uruchamianie autopilota i wraca do pracy ręcznej. Powiązane D10/D11/D13; nie traktować zgłoszenia jako dowodu konkretnego deadlocku.

- [ ] P1 — kontrakt i jeden formularz. Pole „Plan” jako ścieżka/odnośnik do pliku w dostępnym root, wybór katalogów rw/ro i skilli oraz jeden przycisk „Włącz autopilota”. Serwer czyta i przypina konkretną treść/wersję planu, pokazuje ją użytkownikowi w tym samym formularzu bez ręcznego kopiowania celu/planu i składania reguł JSON. Pierwsza wersja: pliki lokalne, bez automatycznego pobierania dowolnych URL. Jeśli plan odwołuje się do innych dokumentów, model czyta je wyłącznie w dozwolonym zakresie; nie uzyskują statusu dodatkowej zgody. Zmiana planu wymaga ponownego zatwierdzenia. Zatwierdzenie obejmuje konkretny plan prac, testy i poprawki w granicach delegacji; ustalić zgodność z instrukcjami projektu przed kodem.
- [ ] P2 — deterministyczna aktywacja i szybki panel. Zdiagnozować wiszące preview. Usunąć pełne skany niepowiązanych skilli, wiedzy i profili ze ścieżki aktywacji; weryfikować wybrane zasoby i ich zależności. I/O poza globalnymi blokadami, krótki atomowy zapis decyzji po kontroli wersji. Ładowanie ma jawny etap, ograniczony czas i czytelny błąd, nigdy bezterminowo szary formularz. Limit czasu HTTP nie oznacza anulowania operacji backendu: identyfikator żądania, odczyt jego wyniku i unieważnianie spóźnionych przygotowań zapobiegają ukrytej aktywacji/podwójnej decyzji. Formularz zachowuje dane po błędzie. Bez zwiększania timeoutów jako naprawy.
- [ ] P3 — wznowienie ukryte za prostym przepływem. Włączenie z sesji ręcznej automatycznie zamyka bramkę nowych działań i czeka na rozpoczęte, pokazując postęp; zgoda przypisana do sesji i wersji zasobów. Wewnętrzne przygotowanie wznowienia i potwierdzenie modelu zachowane bez osobnych przycisków/technicznych kroków użytkownika. Po S6 checkpoint RAM jest usunięty; nie przywracać go w konfiguracji delegacji. Gdy są stare awaiting_approval/queued lub nierozstrzygnięte skutki, ich los musi być widoczny w zatwierdzanym podsumowaniu. Po komunikacie użytkownika model pobiera plan i ograniczenia, potwierdza bieżący kontekst i pracuje. Pauza/Wróć do ręcznego mają jednoznaczne działanie; wznowienie tego samego planu jednym kliknięciem, bez ukrytego resetowania limitów. Zmiany obecnego kontraktu pauzy/handshake wymagają akceptacji.
- [ ] P4 — wybór skilli zamiast edycji reguł. Panel pokazuje konkretne możliwości wybranych skilli i generuje ograniczenia wykonawcze. Przed implementacją sprawdzić rzeczywisty interfejs potrzebnych skilli, szczególnie git-ops. Same opisy SKILL.md nie są wykonawcą ani gwarancją skutków. Zaufanie/hash pozostają wymagane; zmiana implementacji odbiera zgodę. Zamiast obecnej dokładnej listy argumentów każdego wywołania dopuścić jawne profile działań z argumentami ograniczonymi do projektu/akcji. Uzgodnić nowe reguły; nie delegować dowolnego skryptu/shella pod nazwą skilla. Odczyty wybranych instrukcji skilli i wiedzy projektowej/centralnej potrzebnej do planu wynikają z widocznego zakresu dostępu, bez ręcznego dodawania reguły dla każdego narzędzia.
- [ ] P5 — testy, poprawki i Git w tej samej zgodzie. W panelu przy planie wskazać profil testów (projekt, istniejące środowisko, dozwolone komendy, timeout, logi/przerwanie) i limit cykli poprawka → test; argumenty i pliki robocze muszą respektować profil. Model sam uruchamia kontrole i odpowiednią regresję oraz poprawia błędy w zakresie planu. git-ops otrzymuje jawny zestaw działań i repozytorium; commit/push mogą być objęte zgodą początkową przy wskazanej gałęzi/remote i zapisach planu, bez ponownych pytań. Wybranie samej nazwy skilla nie daje nieograniczonych działań Git. Zachować cudze zmiany; force push, reset/clean z utratą danych, deploy, instalacje, nowe sekrety i zmiana uprawnień wymagają osobnej decyzji. Nie ponawiać operacji o niepewnym skutku. Realizuje rozszerzony zakres A3/A3b/A4, nie tylko kosmetykę A2.
- [ ] P6 — wyraźna izolacja zmian. Gdy autopilot jest wyłączony, dotychczasowy przepływ ręczny pozostaje bez nowych wymagań i kosztownych skanów. Wspólne guardy roots/Windows/junction, trust, poufność, polityka zapisu OKF, hooki po zapisie i lifecycle indeksu pozostają obowiązujące. Nie budować osobnej ścieżki zapisu omijającej OKF. Bez zmian zewnętrznych MCP, chyba że wskazana integracja jest potrzebna; nie dodawać jej konfiguracji do podstawowego formularza. Dostęp do plików przez sklr jest egzekwowany; uruchamiane skrypty działają z uprawnieniami użytkownika, więc nie przedstawiać roots jako sandboxa procesów. Restart odbiera aktywną zgodę.
- [ ] P7 — odbiór i wdrażanie etapami. Najpierw regresja trybu ręcznego/OKF/sesji jako punkt odniesienia i diagnoza blokady; potem prosta aktywacja dla plików; następnie testy/poprawki/git-ops. Każda paczka z planem i akceptacją przed kodem. Odbiór: jeden formularz i jedno zatwierdzenie bez wielokrotnego odświeżania; brak pytań w granicach planu; jasno uzasadniony STOP przy ważnej decyzji; responsywny status/pauza także przy wolnym zasobie, konflikcie, awarii i spóźnionej odpowiedzi; brak wykonania po odwołaniu zgody; zachowana regresja ręcznych zgód/OKF/skilli/MCP i Windows. Pytest/serwery tylko po wyraźnym poleceniu. Ustalić mierzalny budżet czasu na podstawie pomiarów, bez obiecywania czasu niezbadanego systemu.

Odbiór pierwszej paczki preview: Linux 201 passed i Node 8/8 w CURRENT. Na Windows po aktualizacji z zachowaniem lokalnej konfiguracji/środowiska i restarcie przez użytkownika: ręczny odczyt/zapis ze zwykłą zgodą i wyszukiwanie OKF; paused/0 działań → delegacja tylko plikowa → podgląd bez niezwiązanych skanów; sprawdzić czytelny postęp/błąd i zachowanie formularza po limicie (jeśli wystąpi); zatwierdzić i wykonać handshake, dozwolony zapis/odczyt bez Allow; pauza i jawny powrót do ręcznego, zwykłe zgody oraz aktualność OKF po zapisie. Przy opóźnieniu zebrać czas Network oraz Server-Timing preview_* bez tokenów i treści. Nie traktować limitu HTTP jako przerwania filesystem I/O ani tej paczki jako gotowego formularza P1/P3 lub obsługi testów/Gita.

Proponowane redukcje interfejsu: brak ręcznego celu obok wskazanego planu, reguł JSON, wyboru każdego narzędzia odczytu, osobnego zatwierdzania podglądu i osobnego przygotowywania handshake. Limity jako gotowy profil z opcjonalnymi szczegółami. Nie usuwać kontroli zakresu, trust ani pewności decyzji. Model pyta o sprzeczność/wymagania nieobjęte planem, ryzykowną zmianę lub brak postępu po limicie prób; zwykłe decyzje implementacyjne podejmuje sam. sklr nadal nie budzi LLM: po zakończeniu odpowiedzi/limicie harnessu kontynuację inicjuje użytkownik, chyba że sam harness zapewnia dalszą pętlę.

## 3. Przygotowanie publikacji i przekazania na Windows

Procedura i scenariusze: [Windows](../windows-okf.md). Publikacja kandydata umożliwia testy runtime na Windows; nie zastępuje ich ani nie oznacza pełnego odbioru autonomii.

- [x] W1: po akceptacji dodano config.example.json, ignorowanie lokalnej konfiguracji i migrację Windows z kopią poza checkoutem. config.json usunięty wyłącznie z indeksu, zawartość zachowana; loader bez zmian. Kontrole zgodności i migracji w tymczasowych repozytoriach zaliczone (CURRENT). Publikacja osobno w W4; wykonanie procedury PowerShell na Windows pozostaje w W5.
- [x] W2: potwierdzono, że plik `kills)…` to przypadkowy wydruk diff-stat z sekwencjami ANSI. Wyłączony ze śledzenia z dokładnym wpisem ignorowania; lokalna kopia zachowana.
- [x] W3 — dostępna walidacja Linux: kontrole statyczne i pip check poprawne; regresja A2b4 941 passed, uzupełniająca regresja plików/skilli/state 277 passed/2 skipped, panel Node 8/8 plików. Dokładne zakresy i wersje w CURRENT oraz raporcie przygotowania wydania. Nie jest to pełna regresja ani odbiór Windows; zależności Windows sprawdzić w W5, bez automatycznej instalacji.
- [ ] W4: przejrzeć diff i zakres plików, wykluczyć config/venv/bazy/klucze/poufne wyniki/smoke-testy, ustalić dokładny commit kandydata i opublikować na uzgodnionym remote/branch. Sprawdzić rzeczywisty stan remote przed deklaracją publikacji.
- [ ] W5: na Windows aktualizacja z zachowaniem lokalnej konfiguracji i środowiska, testy regresji, uruchomienie MCP/panelu przez użytkownika, odbiór S2–S5 i restartu. Wyniki/ograniczenia do CURRENT, pozostałe usterki tutaj. Przed określeniem „autonomia ukończona” odebrać także zakres A2–A4 faktycznie objęty wydaniem.

## 4. Zewnętrzne MCP — pozostałe odbiory, poza głównym rozwojem autonomii

M1–M5 są wdrożone, smoke katalogu i zaufanego list_open_tabs zaliczony. Zachować odbiory integracji używanej przez autonomię; nie deklarować pełnej ochrony SSRF/produkcji.

- [ ] M2f: autorytatywny zmienny DNS, NAT64/6to4, interfejsy VPN, koszt DNS/retencja połączeń i zgodność AnyIOBackend po aktualizacji httpcore2. Potwierdzić ponowione paczki po #592/#594; działający panel nie dostarcza liczników pytest. Kontrolowany resolver z TCP i fallback localhost już odebrane. Doprecyzować ograniczenia w panelu.
- [ ] M2g: rozróżnić przyczynę odmowy odpowiedzi 4 MiB, rzeczywisty TLS z własnym CA/błędnym certyfikatem, Bearer/nagłówki, długa sesja SSE, POSIX i zagnieżdżone joby Windows (również sklr w zewnętrznym jobie). Kontrola procesów nie jest sandboxem.
- [ ] M5a: osłonięty spawn/attach i total timeout klienta, regresje stdio po walidatorze; POSIX/job testować wspólnie z M2g. Bez deklaracji izolacji RAM.
- [ ] M6: HTTP/SSE/stdio przez model, zmiana projektu, pełny zapisany zbiór trust/cofnięcie/ponowne nadanie, nawigacja open_url, zmiana schematu, limiter, TTL, sekrety, konflikty i timeout/cancel. Restart/trwałość enabled/trust i jeden zaufany odczyt już odebrane. Pełna regresja wspólna z S5/W5.

## 5. OKF i indeks — potrzebne, odłożone poza główny zakres autonomii

- [ ] O1: ręczny odbiór polityki formatu: compatible/require_okf, zakres/wyjątki, restart/alias, zmiana polityki podczas oczekiwania na zgodę i scenariusze Windows.
- [ ] O2: automatyczne docs — panelowy przełącznik/trwałość, dodanie/usunięcie roota, zniknięcie/powrót docs, pierwszeństwo ręcznych wpisów i junctiony Windows.
- [ ] O3: pilot centralny/projektowy i pomiar kontekstu katalog → search → outline/section wobec pełnych źródeł; dokończyć walidację przekonwertowanych 22 dokumentów i 3 wcześniej poprawnych po ograniczeniach audit_budget. Standard/przykłady/archiwum poza konwersją; bez docs/knowledge/ i automatycznego require_okf. Regresje central/docs/legacy/mixed i junctionów według instrukcji Windows.
- [ ] I1: create_tree po committed zgłasza konkretne ścieżki; błąd indeksu nie zmienia wyniku zapisu. Test dostępności reszty RAM i jednego odczytu na zmieniony dokument.
- [ ] I2: rename ze starym/nowym zakresem, usunięcia i niepewny rollback bez cichego przywracania starych wpisów.
- [ ] I3: okresowa lekka inventory zmian IDE/git pull, stat jako filtr i force pełnego sprawdzenia; interwały/backoff, bez kasowania niewidzianych wpisów po częściowym skanie.
- [ ] I4: purge SQLite po usunięciu zbioru/dokumentów, retencja i ACL; DPAPI chroni klucz, nie bazę. Przebudowa/staging-generation po osobnym projekcie.
- [ ] I5: ręczny smoke create_file/convert, edycja podczas pełnego skanu, retry zaległych zmian, reconcile_required, spóźnione wyniki i awarie. Lokalny smoke-test z dawnych sesji nie istnieje w tym checkout; na docelowym Windows sprawdzić jego obecność i po odbiorze jawnie usunąć oraz potwierdzić brak wpisu po uzgodnieniu.
- [ ] I6 — odbiór powiązania OKF z sesją: implementacja i izolowane smoke w CURRENT; pytest i ręczny odbiór Windows nadal niewykonane. Uruchomić wskazaną tam regresję pytest, potem Windows: start bez sesji odświeża centralne zbiory bez REFRESH_UNAVAILABLE projektów, Start/wznowienie odświeżają projekty aktualnych folderów, pauza blokuje model, zmiana folderów nie przerywa niezwiązanego centralnego indeksu, zakończenie/nowa sesja odrzuca stary projektowy skan, cofnięcie i przywrócenie dostępu nie wznawia starej publikacji, wiedza centralna nadal wymaga zachowanego zatwierdzonego roota. Sprawdzić katalog autopilota i tryb work_sessions.enabled=false. Użytkownik zlecił publikację paczki na origin/main; publikacja nie zamyka I6. Pytest/serwery tylko na wyraźne polecenie.

## 6. Diagnostyka i potwierdzony dług

### D15 — pełne repo Windows po 58c7986: fail-first i przekazanie napraw

- [ ] Zdiagnozować i naprawić poniższe 20 przypadków po osobnym planie i akceptacji kodu. Wynik i zielone A/B/C: najnowsza sekcja CURRENT. Źródło: docs/full_tests_20261224.txt (oczyszczone cookie/token). Pełne repo 20 failed / 3231 passed / 31 skipped / 4960.54s. Ten punkt aktualizuje priorytet D14/D7/D8, nie zatwierdza zbiorczej implementacji.

#### 1. Tania izolacja D7/D8 oraz search

D7 — tests/test_edit_tools_registration.py:
- `test_defaults_forwarded`
- `test_explicit_arguments_forwarded`

Oba: NameError KNOWLEDGE w main.py:470. Odczyt fixture potwierdza brak KNOWLEDGE w namespace i stub invoke(*args) bez kwargs. Po dodaniu atrapy sprawdzić także keyword after_write i tożsamość callbacku dla wszystkich objętych wrapperów; nie usuwać produkcyjnego hooka OKF. Start: `python -m pytest tests/test_edit_tools_registration.py -q -rs --durations=0`.

D8 — `tests/test_okf_cache_backend.py::test_cancel_clock_does_not_leave_waiter_blocked`: RuntimeError PRIVATE_SENTINEL z cache_backend.py:90 zamiast DocumentError CACHE_REFRESH_FAILED. Zachować bezpieczny błąd, zwolnienie oczekującego i cleanup; deterministycznie kontrolować kolejność, nie akceptować surowego wyjątku. Start: nodeid, potem cały test_okf_cache_backend.py.

Search — tests/test_okf_search.py:
- `test_unchecked_cache_returns_fresh_section_and_revision`: odczytano a, brak trafienia a.
- `test_ranking_by_field_weights`: niepełne trafienia i tags_doc zamiast title_doc na początku.

Helper find przekazuje search(..., 2); ustalić znaczenie budżetu, partial/reasons i koszt ścieżek/odczytu. To trop, nie diagnoza. Oddzielić semantykę od przerwania po budżecie. Nie zmieniać rankingu ani budżetu dla zielonego wyniku bez dowodu/kontraktu. Start: oba nodeid z --durations=0, potem cały test_okf_search.py.

#### 2. Bezpieczeństwo external MCP: anulowanie i inspekcja

- `tests/test_external_mcp_client.py::test_thread_cancel_during_call_is_uncertain`: cancelled zamiast call_cancelled_uncertain. Fixture używa Timer(0.1); sprawdzić, czy call wystartował przed anulowaniem. Deterministyczny sygnał wejścia wykonawcy, rozróżnienie before dispatch / uncertain after dispatch, zamknięcie transportu i zakaz retry muszą pozostać.
- `tests/test_external_mcp_admin_chain.py::test_inspection_goes_through_the_real_client_and_pins_real_digests`: pending po 10 s. Mierzyć start/finish zadania, klienta, walidację schematu i cleanup; przykładowy URL atrapy nie dowodzi problemu sieci.

Start: dwa nodeid, potem test_external_mcp_client.py + test_external_mcp_admin_chain.py; dalsze cleanup/result/trust według zmienionej logiki. Nie ponawiać realnych operacji po niepewnym błędzie.

#### 3. D14: 12 błędów oczekiwania; A/B zielone, pełne repo czerwone

Autopilot (RUNNING po settle/wait(5)):
- `tests/test_autopilot_executors.py::test_central_trusted_skill_is_rechecked_and_charged_once`
- `tests/test_autopilot_integration.py::test_create_tree_is_one_logical_operation`

tests/test_knowledge_sessions.py (pierwsze osiem: RUNNING/QUEUED po wait(5)):
- `test_startup_refreshes_only_central_without_selecting_session`
- `test_start_indexes_all_session_projects_and_excludes_other_registered_projects`
- `test_central_is_preserved_and_can_refresh_while_paused_but_model_is_blocked`
- `test_folder_replacement_refreshes_only_after_confirmed_resume`
- `test_end_discards_projects_but_retains_central_for_new_session`
- `test_early_panel_start_before_cache_initialization_is_caught_by_bootstrap`
- `test_recovery_does_not_refresh_projects_before_resume_confirmation`
- `test_repeated_bind_does_not_schedule_more_refreshes`
- `test_pending_project_scan_cannot_publish_after_session_end`: scanned.wait(5) nie przechodzi, entered=True; nie potwierdzono końca original scan ani odrzucenia publikacji.

Profil projektu:
- `tests/test_project_knowledge.py::test_project_profiles_plain_markdown_search_get_and_exclusions[mixed--expected0]`: refresh RUNNING po wait(8); asercja wyjaśnia etap, nie dowodzi błędu wykluczeń. Legacy/docs nie zawiodły w tym przebiegu.

Start: central startup i central skill osobno, z pluginem oraz bez niego. Następnie mała grupa lub pięć plików D14, nie całe repo. Porównać kolejność/teardown: żywe workery/watchery, cache, monkeypatch/global state, SQLite commit, ścieżki i skan. Wersje Python/SQLite/pytest/SDK zebrać z istniejącego środowiska; raporty A/B pozostają lokalne. Plugin może zmieniać harmonogram i narzut, więc zielone z pluginem nie wyklucza wyścigu. Kontrolowane powtórzenia i małe grupy poprzedników dobierać według reprodukcji, nie losowo odpalać całe repo. Bez zwiększania wait/timeoutów jako naprawy.

#### 4. Stara publikacja po zapisie

- `tests/test_knowledge_write_tools.py::test_old_scan_cannot_publish_over_generation_after_write`: old_scan FAILED zamiast CANCELLED. Atrapa release.wait(5) działa podczas zapisu i nowego skanu; sprawdzić stderr/status i moment zwolnienia. Przekroczenie bramki testowej nie jest dowodem błędu anulowania generacji. Zachować odrzucenie starej publikacji. Start: nodeid, potem test_knowledge_write_tools.py i regresja cache/generacji.

#### Strategia i odbiór końcowy

- [ ] Przed naprawą: AGENTS, najnowszy CURRENT, D15, zrzut, HEAD/status. Nazwa 20261224 nie jest potwierdzeniem daty (timestampy 2026-10-24). Brak wersji Windows i diagnostyki pełnego przebiegu odnotować, nie zastępować wersjami Linux.
- [ ] Zgodnie z użytkownikiem: konkretne czerwone nodeid → zaakceptowana naprawa → test dedykowany → cały moduł → mała regresja między modułami. Krótkie fazy mogą używać -x, ale przed końcem sprawdzić wszystkie 20 przypadków.
- [ ] Małe paczki: -q -rs --durations=0; opcjonalnie -p scripts.pytest_diagnostics --windows-diagnostics, osobno bez pluginu. Nie sumować inkluzywnych metryk. Log od startu w CMD: `python -m pytest ... > pytest_targeted.log 2>&1`. Bez drugiego równoległego pytest.
- [ ] Dopiero gdy małe zakresy zielone, jedno pełne repo na końcu: `python -m pytest -q -rs --durations=30 > pytest_full.log 2>&1`. Pytest wyłącznie na wyraźne polecenie. Nie zaczynać od kolejnych 82 minut.
- [ ] Osobna diagnoza wydajności: directory_limit 80.55s (zaliczony), config concurrent 27.11s, cache 25.30s, rozkład wszystkich faz; top 30 nie wyjaśnia 82 minut. Bez osłabiania trwałości SQLite/bezpieczeństwa ścieżek.
- [ ] Skip: 10 pandas, 5 POSIX process groups, 1 POSIX mode, 1 case-sensitive filesystem, 14 symlink/link unavailable. Bez automatycznych instalacji/uprawnień; junctiony i panel/harness osobno. D6/POSIX nie jest naprawione, choć nie występuje na liście błędów tego Windows.

D6–D8 są zachowane jako odłożony dług; plan ich wspólnej naprawy nie został zaakceptowany. Nie są bezpośrednimi elementami kontraktu A2 ani domyślnym warunkiem implementacji A2b4. D8 wymaga ponownej oceny przed odbiorem bety oraz wcześniej, jeśli zablokuje używany przepływ.

- [ ] D1: wyjaśnić zmienność czasu testów sesji/panelu oraz pełnych uzgodnień wiedzy. Zebrać --durations z setup/call/teardown, w razie potrzeby osobno kolekcję/start. Wyniki porównywać tylko dla tego samego zakresu/środowiska. Nie zwiększać timeoutów ani zmieniać testów zamiast diagnozy. Dotychczasowe czasy zachowane w archiwum.
- [ ] D2: rozdzielić test_cost_centers_skill.py i test_skill_validator.py od serwera po osobnym planie; bez repo conftest i zależności serwera w środowiskach skilli.
- [ ] D3: starsza ścieżka TrustStore.trust/revoke „Ufaj i uruchom” — rollback pamięci po błędzie save; panelowy ConfigStore.update ma rollback.
- [ ] D4: automatyczne sprawdzenia filtrów źródeł Skille: localStorage/brak/uszkodzenie, wartości domyślne, licznik/pusty wynik, niezależność od dostępności/trust.
- [ ] D5 — ponowna ocena z użytkownikiem: zewnętrzne cost-centers SKILL.md po vibecoder-confirm=false, dev frontmatter/task #109, .ps1 w skill-writer validator/rules, ręczny deploy git-ops. To historyczne działania poza repo; brak dowodu bieżącej potrzeby. Nie wykonywać ani usuwać z listy jako zakończonych bez sprawdzenia.
- [ ] D6: uzgodnić poprawkę POSIX w wewnętrznych list_directory/search_code (ścieżki względem przypiętego roota) i zakres testów path_guard/file_tools/search_tools. Pełny przebieg Linux ujawnił 26 błędów tych plików. Zachować publiczne reguły Windows; nie dopuszczać automatycznie absolutnych ścieżek POSIX i nie usuwać asercji dla zielonego wyniku. Oddzielić błąd wewnętrznego wywołania od założeń testów Windows. Osobny plan przed kodem.
- [ ] D7: uzgodnić aktualizację atrap test_edit_tools_registration dla istniejącego KNOWLEDGE.after_file_write; dwa błędy NameError w pełnym przebiegu. Zachować sprawdzanie przekazania argumentów i tożsamości callbacku. main.py i ten test nie były zmieniane w A2b3.
- [ ] D8: usunąć wyścig anulowania CacheRefreshBackend._run_job: wyjątek clock w oczekującym wątku omija bezpieczny kod błędu workera. test_cancel_clock_does_not_leave_waiter_blocked zawiódł w pełnym przebiegu, osobno przeszedł. Dodać deterministyczną synchronizację scenariusza, bez zwiększania timeoutu i bez maskowania problemu oczekiwaniem surowego RuntimeError. Osobny plan przed kodem.

- [ ] D9 — częściowo naprawione 2026-10-23: statusowe hinty i opisy create_file/edit_file, ręczny STOP zachowany, sekwencja Windows bez dodatkowych Allow/wiadomości i paczka 85 testów zaliczone (CURRENT). Pozostaje przegląd komunikatów pozostałych zapisów i historycznego approval_reason, który nadal opisuje ręczną zgodę; nie zamykać całego D9 ani nie zmieniać zgód dla naprawy tekstu. Ręczne oczekiwanie 6 s to osobne P8.
- [ ] D10 — odbiór wydajności panelu Windows: zaakceptowana poprawka jest zaimplementowana (CURRENT): katalog autopilota na żądanie, odłączone skany poza globalnymi blokadami, kontrola generacji/rewizji i niezależne ładowanie folderów/statusu; Node i izolowane smoke zaliczone. Uruchomić zapisaną w CURRENT paczkę pytest, następnie zmierzyć rzeczywistą instalację Windows: pierwsze wejście, lista folderów, Start/pauza/Odśwież podczas wolnego katalogu, konfiguracja autopilota, konflikt/stara odpowiedź i cache przeglądarki. Network endpoint/status/Duration + Server-Timing (`app`, `roots`, `catalog_*`) pozwoli rozdzielić koszt skills/hash/PATH/OKF/roots, ewentualne SQLite i oczekiwanie poza backendem. Autorytatywne preview/approve/reconfirm nadal walidują żywe zasoby; osobno zmierzyć ich koszt i wpływ na sterowanie. Bez przypisywania wcześniejszych opóźnień wznowienia awarii handshake bez dowodu, bez tokenów/cookies/treści w diagnostyce i bez zwiększania timeoutów/pollingu zamiast diagnozy. Użytkownik zlecił publikację paczki na origin/main; commit identyfikuje historia Git. Publikacja nie zamyka odbioru D10, D11 ani D1.
- [ ] D11 — odbiór zakładki „Foldery i sesja”: uruchomić zapisaną w CURRENT paczkę backendu (pytest jeszcze niewykonany), następnie przeglądarka/Windows: jasny i ciemny motyw, wąski ekran, długie ścieżki i klawiatura; jedna lista, Start dla wszystkich dostępnych folderów, blokady zmian podczas pracy/kończenia działań/handshake, dodanie/usunięcie podczas pauzy i aktualizacja sesji, odmowa starej karty, przejście Ręczny/Autopilot z nową zgodą po zmianie folderów. Sprawdzić też konfigurację folderów przy wyłączonych sesjach oraz plan po zmianie roots. Lokalny zakres sprawdzeń w CURRENT. Nie zamyka D10. Pytest/serwery wyłącznie po wyraźnym poleceniu; publikacja osobno.

- [ ] D12 — odbiór zakończenia sesji bez zapisywania: uruchomić paczkę pytest z sekcji kończenia sesji w CURRENT; następnie przeglądarka/Windows: zakończenie aktywnej sesji, pauzy i przygotowanego handshake, anulowanie faktycznej kolejki, dokończenie rozpoczętego działania, zmiana folderów i świeży Start bez dziedziczenia planu/delegacji, stara karta/token, restart w trakcie kończenia oraz migracja lokalnej bazy v1 → v2. Implementacja i lokalna walidacja Node/smoke opisane w CURRENT; nie jest to potwierdzony odbiór backendu/Windows. Pytest/serwery wyłącznie po wyraźnym poleceniu; publikacja osobno.

- [ ] D13 — uprościć i przyspieszyć przejście Ręczny → Autopilot. Zgłoszenie użytkownika 2026-10-23: konfiguracja pozostawała nieaktywna także po zapisaniu checkpointu (saved), przy paused i 0 rozpoczętych działań; po „Odśwież delegację” pojawił się komunikat „Stan lub uprawnienia zmieniły się”, a formularz odblokował się dopiero po kolejnych kliknięciach. Przyczyna nieustalona; nie potwierdzono, czy wznowienie było już przygotowane. Powiązać diagnozę z D10/D11, następnie zaproponować możliwie krótki, przewidywalny przepływ z czytelną przyczyną blokady, bez wielokrotnego odświeżania. Użytkownik dopuszcza rozważenie rezygnacji z części funkcji; wybór i ewentualne zmiany kontraktu wymagają osobnego uzgodnienia. Prace odłożone: teraz kontynuujemy uruchomienie obecnego autopilota. Plan i akceptacja przed kodem.

### D14 — błędy regresji Windows 2026-10-23; przekazanie do naprawy

Plan zaakceptowany w kolejnej sesji na bazie `97dfc40`. Lokalnie poprawiono asercję panelu, sprawdzanie wyniku refresh, diagnostykę punktów synchronizacji i cleanup fixtures; ograniczono liczbę trwałych zapisów przygotowujących test retencji bez zmiany produkcyjnego SQLite. Dodano opt-in raport faz/SQLite/OKF/stosów i jego testy. Linux po osobnym poleceniu: mała paczka **7 passed/0.53s**, D14 z raportem **112 passed/12.15s**, szersza S6/sesje/autopilot/OKF **934 passed/131.13s**. Zakres i ograniczenia w CURRENT, komendy w [Windows](../windows-okf.md#diagnostyka-regresji-windows-d14). **Windows nadal nieodebrane**. Pozostałe kroki: docelowa mała paczka z raportem → wykazana przyczyna queued/running/pustego indeksu → ewentualna poprawka runtime → docelowe D14/S6/szersza regresja i panel. Nie powtarzać zielonych paczek Linux bez nowej przyczyny. Poniższe 17 przypadków to oryginalny raport Windows, nie wynik ponowienia po poprawkach.

- [ ] Odtworzyć i zdiagnozować 17 błędów poniżej na drugim komputerze; plan i akceptacja przed poprawkami. Wynik użytkownika: **17 failed, 993 passed, 1 skipped in 3277.31s (54:37)**. Baza checkoutu po przebiegu: `2a182bf`, main. Paczka odpowiada szerszej regresji sesje/autopilot/panel/OKF proponowanej w rozmowie, ale dokładnej komendy i wersji środowiska nie podano. Nie jest to wynik pełnego repo. Nie zapisujemy surowego tracebacku z cookies i lokalnymi ścieżkami.

**A. Autopilot — 4 błędy: zadanie nie jest terminalne po wait(5).**

Plik `tests/test_autopilot_executors.py`:
1. `test_central_trusted_skill_is_rechecked_and_charged_once` (:207): `Environment.settle` w `tests/autopilot_fixtures.py:55` widzi skill_script RUNNING po wait(5), bez finished_at/exit_code. Testowy run_command ma recheck_current i kontrole braku locków; z logu nie wynika, czy doszedł do tych asercji. Cały call 45.91 s.
2. `test_knowledge_refresh_does_not_charge_a_second_operation` (:262): knowledge_refresh nadal RUNNING zamiast DONE po wait(5). Nie potwierdzono docelowej asercji jednokrotnego naliczenia.
3. `test_knowledge_refresh_enters_manager_and_session_before_config` (:285): submit zaobserwowano zgodnie z oczekiwaniem, ale knowledge_refresh nie jest terminalne po wait(5).

Plik `tests/test_autopilot_integration.py`:
4. `test_create_tree_is_one_logical_operation` (:289; fixture :55): odpowiedź queued, reserved=1/used=0; po wait(5) tree_create RUNNING, brak finished_at/exit_code. Nie ma dowodu końcowego skutku na plikach ani błędu naliczania. Call 21.21 s.

**B. Panel — 1 błąd statyczny.**

5. `tests/test_panel_static.py::test_index_has_skills_tab_and_view` (:53): pętla oczekuje tasks/roots/skills, HTML nie ma `id="tab-roots"`. Istniejący panel łączy foldery i sesję. Sprawdzić aktualny kontrakt/nawigację i dopiero ustalić korektę testu lub UI; nie przywracać starej zakładki wyłącznie dla zielonego wyniku.

**C. OKF i sesje — 10 błędów w `tests/test_knowledge_sessions.py`.**

Osiem przypadków kończy się w helperze `settle` (:65): zadanie nie jest terminalne po wait(5).
6. `test_startup_refreshes_only_central_without_selecting_session` (:84): centralne general RUNNING.
7. `test_start_indexes_all_session_projects_and_excludes_other_registered_projects` (:96): projektowy knowledge_refresh QUEUED.
8. `test_central_is_preserved_and_can_refresh_while_paused_but_model_is_blocked` (:112): projektowy refresh RUNNING.
9. `test_folder_replacement_refreshes_only_after_confirmed_resume` (:133): projektowy refresh RUNNING.
10. `test_end_discards_projects_but_retains_central_for_new_session` (:155): projektowy refresh RUNNING.
11. `test_early_panel_start_before_cache_initialization_is_caught_by_bootstrap` (:170): projektowy refresh RUNNING.
12. `test_recovery_does_not_refresh_projects_before_resume_confirmation` (:179): centralne general RUNNING.
13. `test_repeated_bind_does_not_schedule_more_refreshes` (:188): projektowy refresh RUNNING.

Dwa przypadki nie otrzymują sygnału `entered` w 5 s:
14. `test_change_then_restore_cannot_publish_old_central_refresh[limits]` (:209).
15. `test_pending_project_scan_cannot_publish_after_session_end` (:264).
W obu atrapach entered jest ustawiane **po powrocie original(scan_bundle)**. Brak sygnału nie dowodzi, że worker nie wszedł do skanu; możliwy jest także wolny/zablokowany oryginalny skan. Przyczyna niepotwierdzona. Nie uznawać późniejszych asercji bezpieczeństwa publikacji za odebrane.

**D. Profile projektu — 2 błędy w `tests/test_project_knowledge.py:137`.**

16. `test_project_profiles_plain_markdown_search_get_and_exclusions[mixed--expected0]`: po cold search i wait(8) cached search zwraca pusty zbiór zamiast docs/plain oraz dwóch dokumentów .vibecoder.
17. `test_project_profiles_plain_markdown_search_get_and_exclusions[legacy--expected1]`: pusty zbiór zamiast dwóch dokumentów .vibecoder.
Test nie sprawdza terminalnego statusu zwróconego przez wait(8) przed search cached. Ustalić status/wynik odświeżenia i stan indeksu; nie zakładać od razu błędu profilu lub wykluczeń. Wariant docs nie jest na liście błędów (call 17.78 s).

**Czasy i ograniczenia diagnozy.**

Najwolniejsze call: audit `test_retention_caps_history_but_keeps_old_unresolved_operation` 157.98 s (nie na liście failed); skill 45.91 s; panel resume `test_complete_api_flow_counts_all_links_and_plan_has_no_token` 44.98 s; `test_changed_skill_in_queue_does_not_spawn_or_consume` 41.05 s; sekwencja delegowanych zapisów 31.74 s; anulowanie ponad 20 linków 30.91 s; kontrolowany eight_second_wait 21.00 s. Są to czasy całych call, nie pomiary konkretnego locka/I/O ani samych wait. Skip 1: `test_project_knowledge.py:322`, symlink unavailable. Wynik nie zawiera wersji środowiska, pełnych logów workerów ani osobnego raportu S6/Node.

**Plan wejścia na drugim komputerze (nie zgoda na implementację).**

- Odczytać AGENTS/CURRENT i ten punkt; sprawdzić hash/stan Git i istniejące środowisko. Zachować config/bazy i cudze zmiany. Nie instalować ani uruchamiać serwerów automatycznie.
- Na wyraźne polecenie odtworzyć pojedyncze przypadki oraz ich paczkę, np. `python -m pytest tests/test_autopilot_executors.py tests/test_autopilot_integration.py tests/test_panel_static.py tests/test_knowledge_sessions.py tests/test_project_knowledge.py -q -rs --durations=30`; izolować konkretny nodeid przy analizie zależności od kolejności.
- Zebrać wersje Python/pytest/SDK, statusy i czas queued/start/finish, logi błędów workerów, fazy skanu/publikacji, stosy wątków i czas I/O/SQLite. Oddzielić opóźnienie, deadlock, koszt dysku/środowiska i błędne założenia fixture; to hipotezy, nie diagnoza.
- Nie zwiększać timeoutów i nie usuwać asercji tylko dla zielonego wyniku. Zachować naliczanie budżetu, kolejność locków, pauzę/handshake, niepublikowanie starych skanów, wykluczenia i poufność.
- Po uzgodnionej naprawie wykonać dedykowane S6/text_validation, wskazaną szerszą regresję i Node; pełne repo oraz rzeczywisty panel/harness raportować osobno. D14 jest powiązane z D1/I6/S6, nie zamyka ani nie zastępuje ich odbioru.

## 7. Propozycje wymagające osobnego kontraktu i priorytetu

- [ ] Diagnostyka Python/SDK jako skill lub funkcja: wskazanie badanego środowiska (sklr/skill/projekt), wersje importlib.metadata, jawna lista symboli/sygnatur i ograniczone fragmenty implementacji. Bez eval/exec/instalacji/sieci/serwerów/sekretów; import wykonuje kod i wymaga polityki. Motywacja i szczegóły historyczne w archiwum backlogu.
- [ ] Audyt/konwerter dla jawnego dowolnego podkatalogu, np. doc/.
- [ ] Semantyczny selektor stanu i skill okf-curator według [zasad kuracji](../specs/okf-integration/curation.md); opcjonalny hard guard projektu.
- [ ] Ogólny execute_command/command_policy, ADR 0003 i dalsze skille — po osobnym kontrakcie; ograniczony runner A3b nie akceptuje automatycznie całej dawnej propozycji.
- [ ] Rozważyć krótkie oczekiwanie na decyzję w łącznym budżecie 8 s. Obecnie task_id i jedno wait_task według AGENTS; bez zmiany kontraktu żadnego pollingu.
