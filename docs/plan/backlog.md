---
type: Implementation Plan
title: Aktualny backlog projektu sklr
description: Otwarte prace nad autonomią, przygotowanie przekazania na Windows i odłożone odbiory pozostałych funkcji.
tags: [sklr, backlog, planning, autonomy, windows]
---
# Aktualny backlog — 2026-10-21

Jedyna lista otwartych prac. Stan wdrożenia i potwierdzenia: [CURRENT](CURRENT.md). Zakończone etapy P1–P3 i M1–M5 oraz dawne plany przeniesiono do [historii backlogu](../archive/state-review-2026-10-21/backlog.md). Checkboxy oznaczają pozostałą pracę, nie brak istniejącej implementacji.

Priorytet: autonomia według kontraktu → kandydat do odbioru Windows → ręczny odbiór MCP/panelu na maszynie docelowej. Pozostałe potrzebne prace zachowujemy poniżej. Nie są automatycznie warunkami tej paczki, chyba że ujawnią błąd używanego przepływu, zgód lub poufności. Przegląd A2b3 zakończony, plan A2b4 zaakceptowany i lokalnie zaimplementowany; walidacja tej paczki i wskazana regresja Linux zaliczone. Teraz A2b5 i przygotowanie przekazania, bez poprzedzania ich zbiorczą naprawą D6–D8. D6–D8 wracają na główną ścieżkę tylko po wykazaniu blokady konkretnego scenariusza autonomii, zgód lub poufności. Plan i akceptacja przed zmianą kodu; pytest/serwery tylko na wyraźne polecenie.

## 1. Domknięcie fundamentu A1/A1b — sesje i wznowienie

Pauza, checkpoint RAM, panel, wznowienie i integracja main.py są wdrożone opt-in; nie implementować ich ponownie. Kontrakt: [sesje pracy](../specs/work-session-pause.md).

- [ ] S2 — odbiór docelowy: rzeczywista odmowa narzędzia roboczego przed Startem i podczas pauzy; brak utworzenia nowego zadania przy odmowie; narzędzia sterujące nadal dostępne. Potwierdzić również poprawiony komunikat session_not_selected (S1 i jego paczka testów zakończone, wynik w CURRENT).
- [ ] S3 — odbiór docelowy: pauza podczas rozpoczętego działania, wynik i oddzielna metainformacja pauzy, spadek licznika do zera; brak nowego dispatch, brak automatycznego cancel/retry.
- [ ] S4 — odbiór docelowy: faktyczne awaiting_approval/queued, zgoda i jej wygaśnięcie podczas pauzy, licznik/potwierdzenie anulowań przy wznowieniu; stare zadania nie ruszają, nowe wymagają aktualnych zgód.
- [ ] S5 — regresja: pełny bieżący HEAD, rzeczywisty katalog/transport FastMCP i scenariusze graniczne kontraktu (rewizje, awarie RAM/SQLite, zmiana checkpointu podczas resume_pending, niepewny confirm, restart). Testy odporności są już zapisane; nie otwierać ich jako niewdrożonej funkcji. Odróżnić testy logiki od odbioru harnessu.

## 2. Autonomia według kontraktu — dalsza implementacja

Użytkownik zaakceptował [kontrakt A2 v1](../specs/autopilot-contract.md) po korekcie timeoutów. [Kontrakt sesji](../specs/work-session-pause.md) pozostaje fundamentem; A3/A3b i A4 wymagają kolejnych kontraktów. Akceptacja A2 nie nadaje jeszcze uprawnień wykonawczych i nie zastępuje akceptacji planu paczki kodu. Kolejność: domknięcie A1/A1b → A2 → A3/A3b → A4. Odbiór Windows można przygotować etapowo; wersja z samą pauzą nie może być opisana jako ukończony autopilot.

- [x] A2b3 — przegląd wyników i granic zakończony jako wejście do A2b4. Lokalny kod i wcześniejsze 820 passed nie oznaczają pełnej zielonej regresji ani odbioru Windows; szczegóły w CURRENT i raporcie A2b3. D6–D8 nie blokują automatycznie następnej paczki.
- [x] A2b4 — implementacja i walidacja lokalna zakończone: dedykowana paczka 48 passed, szersza regresja 941 passed, DOM Node 4/4. Zakres i ograniczenia w CURRENT oraz [raporcie A2b4](../archive/state-review-2026-10-21/A2b4.md). Bez uruchomienia docelowej aplikacji i bez odbioru Windows; config.json bez zmian.
- [ ] A2b5 — odbiór częściowy Windows 2026-10-22: delegacja/panel/handshake, automatyczny zapis i odczyt w allowed oraz odmowa outside ze wstrzymaniem i bez zużycia budżetu potwierdzone w CURRENT. Nie powtarzać ich bez nowej przyczyny. Pozostałe scenariusze sekcji 10 kontraktu i instrukcji Windows, regresje zgód/trust/poufności/anulowania oraz pełna regresja HEAD nadal otwarte. Wskazana wcześniejsza regresja Linux 941 passed nie jest pełnym odbiorem. Oddzielić kontrolę sklr od zachowania harnessu i wewnętrznych pętli obcych wykonawców.
- [ ] A3: kontrakt konfiguracji testów per projekt: framework, zakres i uprawnienia osobno; zachować istniejący framework (unittest dla prostych skryptów/skilli, pytest dla API/MCP/złożonych aplikacji jako domyślne propozycje). Unit/integration/end-to-end niezależne od frameworka. Interpreter venv/conda i komendy dla platformy; bez automatycznych instalacji/tworzenia środowisk ani dostępu do produkcji. Venv nie jest sandboxem.
- [ ] A3b: ograniczony wykonawca testów według zaakceptowanego kontraktu poleceń, bez dowolnego shella. Etapy: szybkie kontrole i zmieniona logika → odpowiednia regresja. Limity czasu/wywołań/logów, przerwanie, jawne skip/truncation/timeout, poufne logi i użyteczny skrót błędów. Budżet tokenów po stronie harnessu, jeżeli sklr nie ma danych. Powiązać z [propozycją komend](../specs/command-execution-proposal.md); nie rozszerzać jej automatycznie do ogólnego execute_command.
- [ ] A4: osobne uprawnienie do poprawek, proponowany limit 3 cykli poprawka → test (wartość w kontrakcie). Brak postępu, niejasność lub przekroczenie zakresu wstrzymują pracę; nie zmieniać oczekiwań testów tylko dla zielonego wyniku.

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

## 6. Diagnostyka i potwierdzony dług

D6–D8 są zachowane jako odłożony dług; plan ich wspólnej naprawy nie został zaakceptowany. Nie są bezpośrednimi elementami kontraktu A2 ani domyślnym warunkiem implementacji A2b4. D8 wymaga ponownej oceny przed odbiorem bety oraz wcześniej, jeśli zablokuje używany przepływ.

- [ ] D1: wyjaśnić zmienność czasu testów sesji/panelu oraz pełnych uzgodnień wiedzy. Zebrać --durations z setup/call/teardown, w razie potrzeby osobno kolekcję/start. Wyniki porównywać tylko dla tego samego zakresu/środowiska. Nie zwiększać timeoutów ani zmieniać testów zamiast diagnozy. Dotychczasowe czasy zachowane w archiwum.
- [ ] D2: rozdzielić test_cost_centers_skill.py i test_skill_validator.py od serwera po osobnym planie; bez repo conftest i zależności serwera w środowiskach skilli.
- [ ] D3: starsza ścieżka TrustStore.trust/revoke „Ufaj i uruchom” — rollback pamięci po błędzie save; panelowy ConfigStore.update ma rollback.
- [ ] D4: automatyczne sprawdzenia filtrów źródeł Skille: localStorage/brak/uszkodzenie, wartości domyślne, licznik/pusty wynik, niezależność od dostępności/trust.
- [ ] D5 — ponowna ocena z użytkownikiem: zewnętrzne cost-centers SKILL.md po vibecoder-confirm=false, dev frontmatter/task #109, .ps1 w skill-writer validator/rules, ręczny deploy git-ops. To historyczne działania poza repo; brak dowodu bieżącej potrzeby. Nie wykonywać ani usuwać z listy jako zakończonych bez sprawdzenia.
- [ ] D6: uzgodnić poprawkę POSIX w wewnętrznych list_directory/search_code (ścieżki względem przypiętego roota) i zakres testów path_guard/file_tools/search_tools. Pełny przebieg Linux ujawnił 26 błędów tych plików. Zachować publiczne reguły Windows; nie dopuszczać automatycznie absolutnych ścieżek POSIX i nie usuwać asercji dla zielonego wyniku. Oddzielić błąd wewnętrznego wywołania od założeń testów Windows. Osobny plan przed kodem.
- [ ] D7: uzgodnić aktualizację atrap test_edit_tools_registration dla istniejącego KNOWLEDGE.after_file_write; dwa błędy NameError w pełnym przebiegu. Zachować sprawdzanie przekazania argumentów i tożsamości callbacku. main.py i ten test nie były zmieniane w A2b3.
- [ ] D8: usunąć wyścig anulowania CacheRefreshBackend._run_job: wyjątek clock w oczekującym wątku omija bezpieczny kod błędu workera. test_cancel_clock_does_not_leave_waiter_blocked zawiódł w pełnym przebiegu, osobno przeszedł. Dodać deterministyczną synchronizację scenariusza, bez zwiększania timeoutu i bez maskowania problemu oczekiwaniem surowego RuntimeError. Osobny plan przed kodem.

- [ ] D9 — komunikat delegowanego zapisu: rzeczywisty create_file #904 miał status queued i wykonał się done bez Allow, ale hint mówił „czeka na decyzję użytkownika” i STOP. Dopasować hint do rzeczywistej ścieżki/statusu/autoryzacji, zachowując STOP dla awaiting_approval i zasady nieponawiania niepewnych skutków. Sprawdzić wspólne komunikaty pozostałych zapisów i regresję trybu ręcznego. Osobny plan/akceptacja przed kodem; nie zmieniać zgód w celu naprawy tekstu.
- [ ] D10 — wydajność panelu Windows: użytkownik 2026-10-22 zgłosił długie czasy wznowienia i odświeżania; podczas smoke kilka razy deklarowane przygotowanie nie było jeszcze widoczne w stanie serwera. Brak pomiarów i nieustalona przyczyna; nie zakładać, że to jeden błąd lub awaria handshake. Zebrać czasy żądań panelu (Network: endpoint/status/duration), odróżnić backend, renderowanie i oczekiwanie na potwierdzenie modelu, sprawdzić ewentualne blokady/SQLite i odświeżanie zasobów. Bez eksportowania tokenów panelu, cookies, preparation_token ani poufnych danych. Powiązać z D1 tylko po potwierdzeniu przyczyny; bez zwiększania timeoutów i pollingu LLM zamiast diagnozy. Osobny plan przed zmianami kodu.
- [ ] D11 — odbiór nowego układu „Sesja pracy”: lokalna implementacja po akceptacji i testy DOM opisane w CURRENT. Sprawdzić w przeglądarce/Windows jasny i ciemny motyw, wąski ekran, długie ścieżki, klawiaturę, start/pauzę/wznowienie, powrót z Folderów z zachowaniem wyboru oraz osobne podsumowania zatwierdzonej i nowej delegacji. Nie jest to diagnoza ani zamknięcie D10. Pytest/serwery wyłącznie po wyraźnym poleceniu; publikacja osobno.

## 7. Propozycje wymagające osobnego kontraktu i priorytetu

- [ ] Diagnostyka Python/SDK jako skill lub funkcja: wskazanie badanego środowiska (sklr/skill/projekt), wersje importlib.metadata, jawna lista symboli/sygnatur i ograniczone fragmenty implementacji. Bez eval/exec/instalacji/sieci/serwerów/sekretów; import wykonuje kod i wymaga polityki. Motywacja i szczegóły historyczne w archiwum backlogu.
- [ ] Audyt/konwerter dla jawnego dowolnego podkatalogu, np. doc/.
- [ ] Semantyczny selektor stanu i skill okf-curator według [zasad kuracji](../specs/okf-integration/curation.md); opcjonalny hard guard projektu.
- [ ] Ogólny execute_command/command_policy, ADR 0003 i dalsze skille — po osobnym kontrakcie; ograniczony runner A3b nie akceptuje automatycznie całej dawnej propozycji.
- [ ] Rozważyć krótkie oczekiwanie na decyzję w łącznym budżecie 8 s. Obecnie task_id i jedno wait_task według AGENTS; bez zmiany kontraktu żadnego pollingu.
