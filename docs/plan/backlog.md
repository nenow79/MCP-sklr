---
type: Implementation Plan
title: Aktualny backlog projektu sklr
description: Otwarte prace nad autonomią, przygotowanie przekazania na Windows i odłożone odbiory pozostałych funkcji.
tags: [sklr, backlog, planning, autonomy, windows]
---
# Aktualny backlog — 2026-10-21

Jedyna lista otwartych prac. Stan wdrożenia i potwierdzenia: [CURRENT](CURRENT.md). Zakończone etapy P1–P3 i M1–M5 oraz dawne plany przeniesiono do [historii backlogu](../archive/state-review-2026-10-21/backlog.md). Checkboxy oznaczają pozostałą pracę, nie brak istniejącej implementacji.

Priorytet: autonomia według kontraktu → kandydat do odbioru Windows → ręczny odbiór MCP/panelu na maszynie docelowej. Pozostałe potrzebne prace zachowujemy poniżej. Nie są automatycznie warunkami tej paczki, chyba że ujawnią błąd używanego przepływu, zgód lub poufności. Plan i akceptacja przed zmianą kodu; pytest/serwery tylko na wyraźne polecenie.

## 1. Domknięcie fundamentu A1/A1b — sesje i wznowienie

Pauza, checkpoint RAM, panel, wznowienie i integracja main.py są wdrożone opt-in; nie implementować ich ponownie. Kontrakt: [sesje pracy](../specs/work-session-pause.md).

- [ ] S2 — odbiór docelowy: rzeczywista odmowa narzędzia roboczego przed Startem i podczas pauzy; brak utworzenia nowego zadania przy odmowie; narzędzia sterujące nadal dostępne. Potwierdzić również poprawiony komunikat session_not_selected (S1 i jego paczka testów zakończone, wynik w CURRENT).
- [ ] S3 — odbiór docelowy: pauza podczas rozpoczętego działania, wynik i oddzielna metainformacja pauzy, spadek licznika do zera; brak nowego dispatch, brak automatycznego cancel/retry.
- [ ] S4 — odbiór docelowy: faktyczne awaiting_approval/queued, zgoda i jej wygaśnięcie podczas pauzy, licznik/potwierdzenie anulowań przy wznowieniu; stare zadania nie ruszają, nowe wymagają aktualnych zgód.
- [ ] S5 — regresja: pełny bieżący HEAD, rzeczywisty katalog/transport FastMCP i scenariusze graniczne kontraktu (rewizje, awarie RAM/SQLite, zmiana checkpointu podczas resume_pending, niepewny confirm, restart). Testy odporności są już zapisane; nie otwierać ich jako niewdrożonej funkcji. Odróżnić testy logiki od odbioru harnessu.

## 2. Autonomia według kontraktu — dalsza implementacja

Użytkownik zaakceptował [kontrakt A2 v1](../specs/autopilot-contract.md) po korekcie timeoutów. [Kontrakt sesji](../specs/work-session-pause.md) pozostaje fundamentem; A3/A3b i A4 wymagają kolejnych kontraktów. Akceptacja A2 nie nadaje jeszcze uprawnień wykonawczych i nie zastępuje akceptacji planu paczki kodu. Kolejność: domknięcie A1/A1b → A2 → A3/A3b → A4. Odbiór Windows można przygotować etapowo; wersja z samą pauzą nie może być opisana jako ukończony autopilot.

- [ ] A2b3: dziennik metadanych i retencja, koordynacja z sesją/handshake oraz TaskManager i bezpośrednimi narzędziami, jednokrotne liczenie i kontrola przed skutkiem, cofnięcie/pauza/wygaśnięcie. Ustalić kolejność blokad i obsługę niepewnego commitu; żaden adapter/trust nie może ominąć bramki. Osobny plan przed kodem.
- [ ] A2b4: uwierzytelnione API panelu i UI zatwierdzenia/odwołania kontraktu, podgląd zakresu/budżetów, opt-in konfiguracja/bootstrap i bezpieczne zamknięcie; rzeczywista aktywacja dopiero po poprzednich paczkach. Osobny plan przed kodem.
- [ ] A2b5: odbiór granic/regresji zgód, trust, poufności i anulowania oraz ręczne scenariusze Windows/MCP/panelu z sekcji 10 kontraktu. Oddzielić kontrolę sklr od zachowania harnessu i wewnętrznych pętli obcych wykonawców.
- [ ] A3: kontrakt konfiguracji testów per projekt: framework, zakres i uprawnienia osobno; zachować istniejący framework (unittest dla prostych skryptów/skilli, pytest dla API/MCP/złożonych aplikacji jako domyślne propozycje). Unit/integration/end-to-end niezależne od frameworka. Interpreter venv/conda i komendy dla platformy; bez automatycznych instalacji/tworzenia środowisk ani dostępu do produkcji. Venv nie jest sandboxem.
- [ ] A3b: ograniczony wykonawca testów według zaakceptowanego kontraktu poleceń, bez dowolnego shella. Etapy: szybkie kontrole i zmieniona logika → odpowiednia regresja. Limity czasu/wywołań/logów, przerwanie, jawne skip/truncation/timeout, poufne logi i użyteczny skrót błędów. Budżet tokenów po stronie harnessu, jeżeli sklr nie ma danych. Powiązać z [propozycją komend](../specs/command-execution-proposal.md); nie rozszerzać jej automatycznie do ogólnego execute_command.
- [ ] A4: osobne uprawnienie do poprawek, proponowany limit 3 cykli poprawka → test (wartość w kontrakcie). Brak postępu, niejasność lub przekroczenie zakresu wstrzymują pracę; nie zmieniać oczekiwań testów tylko dla zielonego wyniku.

## 3. Przygotowanie publikacji i przekazania na Windows

Procedura i scenariusze: [Windows](../windows-okf.md). Publikacja kandydata umożliwia testy runtime na Windows; nie zastępuje ich ani nie oznacza pełnego odbioru autonomii.

- [ ] W1: rozdzielić śledzony config.json od konfiguracji lokalnej zgodnie z AGENTS: przygotować przenośny przykład/defaulty, reguły ignorowania i bezpieczne przejście istniejącego Windows checkoutu. Zachować lokalne roots/profile/trust; nie przenosić konfiguracji Linuksa i nie usuwać lokalnego configu przy aktualizacji. Osobny plan i akceptacja przed zmianą.
- [ ] W2: obejrzeć i wycofać z repo przypadkowy śledzony plik w korzeniu o nazwie zaczynającej się od `kills)`; bez usuwania innych danych użytkownika.
- [ ] W3: po zatwierdzonych poprawkach wykonać dostępne kontrole statyczne i uzgodnione testy, zapisać dokładny zakres wyników. Zweryfikować zależności/interpreter docelowy (FastMCP/MCP/httpx2/httpcore2 mają wrażliwe API); brak pełnego pinningu nie jest gwarancją powtarzalności. Nie instalować automatycznie.
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

- [ ] D1: wyjaśnić zmienność czasu testów sesji/panelu oraz pełnych uzgodnień wiedzy. Zebrać --durations z setup/call/teardown, w razie potrzeby osobno kolekcję/start. Wyniki porównywać tylko dla tego samego zakresu/środowiska. Nie zwiększać timeoutów ani zmieniać testów zamiast diagnozy. Dotychczasowe czasy zachowane w archiwum.
- [ ] D2: rozdzielić test_cost_centers_skill.py i test_skill_validator.py od serwera po osobnym planie; bez repo conftest i zależności serwera w środowiskach skilli.
- [ ] D3: starsza ścieżka TrustStore.trust/revoke „Ufaj i uruchom” — rollback pamięci po błędzie save; panelowy ConfigStore.update ma rollback.
- [ ] D4: automatyczne sprawdzenia filtrów źródeł Skille: localStorage/brak/uszkodzenie, wartości domyślne, licznik/pusty wynik, niezależność od dostępności/trust.
- [ ] D5 — ponowna ocena z użytkownikiem: zewnętrzne cost-centers SKILL.md po vibecoder-confirm=false, dev frontmatter/task #109, .ps1 w skill-writer validator/rules, ręczny deploy git-ops. To historyczne działania poza repo; brak dowodu bieżącej potrzeby. Nie wykonywać ani usuwać z listy jako zakończonych bez sprawdzenia.

## 7. Propozycje wymagające osobnego kontraktu i priorytetu

- [ ] Diagnostyka Python/SDK jako skill lub funkcja: wskazanie badanego środowiska (sklr/skill/projekt), wersje importlib.metadata, jawna lista symboli/sygnatur i ograniczone fragmenty implementacji. Bez eval/exec/instalacji/sieci/serwerów/sekretów; import wykonuje kod i wymaga polityki. Motywacja i szczegóły historyczne w archiwum backlogu.
- [ ] Audyt/konwerter dla jawnego dowolnego podkatalogu, np. doc/.
- [ ] Semantyczny selektor stanu i skill okf-curator według [zasad kuracji](../specs/okf-integration/curation.md); opcjonalny hard guard projektu.
- [ ] Ogólny execute_command/command_policy, ADR 0003 i dalsze skille — po osobnym kontrakcie; ograniczony runner A3b nie akceptuje automatycznie całej dawnej propozycji.
- [ ] Rozważyć krótkie oczekiwanie na decyzję w łącznym budżecie 8 s. Obecnie task_id i jedno wait_task według AGENTS; bez zmiany kontraktu żadnego pollingu.
