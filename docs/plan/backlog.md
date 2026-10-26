---
type: Implementation Plan
title: Aktualny backlog projektu sklr
description: Wyłącznie otwarte prace i dług projektu, obejmujące odbiór, uproszczenie autopilota, ograniczony runner testów i odłożone usprawnienia.
tags: [sklr, backlog, planning, autonomy, windows]
---
# Backlog — prace do wykonania

Jedyna lista długu projektu: potrzebne prace, niekoniecznie do realizacji teraz. Zawiera wyłącznie otwarte zadania; wykonane usuwamy, wyniki zapisujemy w [CURRENT](CURRENT.md), historię w [archiwum przeglądu](../archive/backlog-review.md). Identyfikatory zachowano dla zgodności z odnośnikami; scalone wpisy nie są osobnymi zadaniami.

Backlog nie nadaje uprawnień wykonawczych. Plan i akceptacja przed kodem; pytest i serwery tylko na wyraźne polecenie. Nie ponawiać zaliczonych testów bez nowej przyczyny; nowa paczka kodu wymaga odpowiedniej walidacji. Zielona regresja nie zastępuje odbioru panelu/harnessu ani pominiętych scenariuszy.

## 1. Sesje i obecny autopilot — pozostały odbiór

Kontrakty: [sesje](../specs/work-session-pause.md), [autopilot A2](../specs/autopilot-contract.md). Scenariusze: [Windows](../windows-okf.md). Nie implementować ponownie istniejącej pauzy, wznowienia ani usuniętego checkpointu.

- [ ] **S2 — blokada pracy:** rzeczywista odmowa przed Startem i podczas pauzy, bez tworzenia zadania; dostępność sterowania i komunikat session_not_selected.
- [ ] **S3 — pauza w trakcie działania:** wynik i osobna metainformacja pauzy, licznik do zera; brak nowego dispatch i automatycznego cancel/retry.
- [ ] **S4 — kolejka i zgody:** rzeczywiste awaiting_approval/queued, decyzja i wygaśnięcie zgody w pauzie, widoczne anulowania przy wznowieniu; stare zadania nie ruszają, nowe wymagają aktualnych zgód.
- [ ] **S5/S6 — rzeczywisty katalog/transport i wznowienie:** pauza → STOP bez zapisu docs → przygotowanie z liczbą anulowań → potwierdzenie planu → działanie. Odebrać rewizje, ponowną pauzę/zmianę decyzji w resume_pending, brak istotnego kontekstu modelu, niepewny confirm, restart/utratę RAM, awarie SQLite, stare payloady/katalog API i migrację bazy v1/v2 z dawnymi pending/saved/unavailable. Nie przywracać checkpointu. Testy logiki nie są odbiorem harnessu.
- [ ] **A2b5 — domknięcie odbioru A2:** pozostałe scenariusze sekcji 10 kontraktu, rzeczywiste zgody/trust/poufność/anulowanie. Nie powtarzać bez przyczyny potwierdzonych w CURRENT: delegacji/handshake, zapisów i odczytów w allowed, odmowy outside bez zużycia budżetu oraz szybkiej sekwencji w jednej turze. Oddzielić kontrolę sklr od pętli harnessu/obcych wykonawców.
- [ ] **P9 — długie zadanie w harnessie:** ponad 8 s, kolejne wait tego samego task_id, potwierdzony sukces i następny zależny krok bez dodatkowej wiadomości/Allow oraz bez ponowienia zapisu. Odbiór backendu nie zastępuje tego scenariusza.
- [ ] **D11 — Foldery i sesja:** przeglądarka Windows, oba motywy, wąski ekran, długie ścieżki, klawiatura; jedna lista i Start dostępnych folderów, blokady podczas pracy/kończenia/handshake, zmiany podczas pauzy, aktualizacja sesji, odmowa starej karty, nowa zgoda po zmianie folderów i planu; również konfiguracja przy wyłączonych sesjach.
- [ ] **D12 — zakończenie bez zapisywania:** aktywna sesja/pauza/przygotowany handshake, anulowanie faktycznej kolejki, dokończenie rozpoczętego działania, świeży Start bez starego planu/delegacji, zmiana folderów, stara karta/token, restart w trakcie kończenia i migracja bazy v1 → v2.

## 2. Uproszczenie autopilota — dalsza implementacja

Cel: wskazany lokalny plan, katalogi i skille, jedna zgoda w panelu, informacja do modelu i realizacja dozwolonych prac/testów/poprawek/Git. Pytania tylko o istotną decyzję lub rozszerzenie zakresu. Kierunek nie jest wdrożonym kontraktem ani zgodą obecnego A2 na testy/Git.

- [ ] **P1 — plan i jeden formularz:** ścieżka do planu w dostępnym root, katalogi rw/ro i skille, jeden przycisk aktywacji; serwer przypina treść/wersję i pokazuje podsumowanie bez kopiowania celu/planu lub reguł JSON. Bez pobierania dowolnych URL. Odnośniki w planie nie nadają dodatkowych zgód; zmiana planu wymaga ponownego zatwierdzenia. Limity jako profil z opcjonalnymi szczegółami; uzgodnić kontrakt i zgodność z instrukcjami projektu.
- [ ] **P2/D10 — wydajność i niezawodna aktywacja (pozostały zakres D1/D13):** zmierzyć Windows: pierwsze wejście, foldery, status/Start/pauza/Odśwież przy wolnym katalogu, preview/approve/reconfirm, konflikt, cache i spóźniona odpowiedź. Network + Server-Timing app/roots/catalog_*/preview_* bez tokenów/cookies/treści. Istniejących optymalizacji nie implementować ponownie; ustalić pozostałą przyczynę blokad formularza i koszt walidacji żywych zasobów. Domknąć I/O poza globalnymi blokadami i krótki atomowy zapis po kontroli wersji tam, gdzie nadal potrzebne. Jawny postęp, skończony czas, czytelny błąd i zachowanie danych formularza; identyfikacja/wynik żądania i unieważnianie spóźnionych przygotowań bez ukrytej aktywacji/podwójnej decyzji. HTTP timeout nie anuluje filesystem I/O. Bez diagnozy deadlocku/awarii handshake bez dowodu i bez zwiększania timeoutów/pollingu jako naprawy.
- [ ] **P3/D13 — prosty przepływ Ręczny → Autopilot i wznowienie:** automatyczne zamknięcie bramki nowych działań i oczekiwanie na rozpoczęte z postępem; zgoda przypisana do sesji/wersji zasobów. Przygotowanie i potwierdzenie modelu bez osobnych technicznych przycisków; widoczny los starej kolejki i niepewnych skutków. Po informacji użytkownika model pobiera plan/ograniczenia i potwierdza kontekst. Jednoznaczna pauza/powrót do ręcznego, wznowienie tego samego planu jednym kliknięciem bez resetu limitów, jasna przyczyna blokady bez wielokrotnego odświeżania. Zmiany handshake wymagają kontraktu; checkpointu nie przywracać.
- [ ] **P4 — profile skilli:** wybór konkretnych możliwości zamiast ręcznej listy argumentów/reguł. Sprawdzić rzeczywiste interfejsy, zwłaszcza git-ops; opisy SKILL.md nie gwarantują skutków. Jawne profile akcji/argumentów ograniczone do projektu, z hash/trust i utratą zgody po zmianie implementacji; bez dowolnego skryptu/shella. Odczyt potrzebnych instrukcji i wiedzy wynika z widocznego zakresu dostępu, bez reguły dla każdego odczytu.
- [ ] **P5 — testy, poprawki i Git w jednej zgodzie:** poniższe etapy tworzą jeden zakres, nie dodatkowe duplikaty A3/A3b/A4:
  - **A3 — kontrakt profilu testów:** projekt, framework, zakres, istniejący interpreter venv/conda, komendy i uprawnienia osobno. Zachować framework projektu; unittest/pytest jako propozycje zależne od projektu, a unit/integration/end-to-end niezależne od frameworka. Bez instalacji, tworzenia środowisk i produkcji; venv nie jest sandboxem.
  - **A3b — ograniczony runner:** jawne komendy/argumenty bez dowolnego shella; szybkie kontrole/zmieniona logika → odpowiednia regresja. Limity czasu/wywołań/logów, przerwanie, jawne skip/truncation/timeout, poufne logi i skrót błędów. Budżet tokenów po stronie harnessu, jeśli sklr nie ma danych. Ogólny execute_command nie jest potrzebny ani objęty realizacją; dawna [propozycja komend](../specs/command-execution-proposal.md) nie nadaje uprawnień.
  - **A4 — poprawki:** osobne uprawnienie i uzgodniony limit cykli poprawka → test (3 jako propozycja, nie zatwierdzony limit); brak postępu/niejasność/przekroczenie zakresu wstrzymują pracę. Nie zmieniać oczekiwań testów dla zielonego wyniku.
  - **Git:** jawny profil git-ops, repo/akcje, branch/remote dla commit/push w zgodzie początkowej; sama nazwa skilla nie daje dowolnego Git. Zachować cudze zmiany. Force push, reset/clean z utratą danych, deploy, instalacje, nowe sekrety i zmiana uprawnień wymagają osobnej decyzji; nie ponawiać niepewnych skutków.
- [ ] **P6/P7 — izolacja i etapowy odbiór:** ręczny tryb bez nowych wymagań/kosztownych skanów; wspólne guardy roots/Windows/junction, trust, poufność, OKF/hooki/lifecycle indeksu, bez bocznej ścieżki zapisu. Restart odbiera zgodę; roots nie są sandboxem procesów. Bez zmian/config zewnętrznych MCP w podstawowym formularzu, chyba że wykazano potrzebę. Etapy: odnieść się do potwierdzonej regresji i zdiagnozować pozostałe blokady → prosta aktywacja plików → testy/poprawki/Git; każda paczka z planem i akceptacją. Odbiór jednej zgody, pracy bez pytań w zakresie, uzasadnionego STOP, responsywności przy błędzie/wolnym zasobie/konflikcie, braku działań po cofnięciu zgody i zachowania ręcznych zgód/OKF/skilli/MCP/Windows. Mierzalny budżet czasu na podstawie pomiarów. sklr nie budzi LLM; dalsze tury wymagają użytkownika lub wsparcia harnessu.
- [ ] **P8 — oczekiwanie autopilota:** uzgodnić skończony budżet oczekiwania modelu na wykonanie w autopilocie (30 s było propozycją, nie zatwierdzonym limitem), granice sekwencji i sposób oddania sterowania. Koniec oczekiwania nie anuluje zadania ani nie uprawnia do retry; śledzić ten sam task_id, zachować pauzę/zgody/timeouty/TTL/poufność i niepewne skutki. Po kontrakcie ujednolicić hinty/opisy/instrukcje, dodać testy długiego wykonania i granic oraz rzeczywisty odbiór harnessu; P9 pozostaje osobnym scenariuszem. sklr nie gwarantuje dalszych tur LLM. Obowiązująca ręczna polityka jednego oczekiwania do 6 s na osobny task_id i zakres jej potwierdzeń: CURRENT.
- [ ] **D9 — komunikaty zapisów:** pozostałe narzędzia i historyczny approval_reason mają opisywać rzeczywisty status ręczny/delegowany; nie zmieniać uprawnień dla naprawy tekstu. Polityka oczekiwania wyłącznie w P8.

## 3. Bezpieczeństwo i dług techniczny

Odłożone prace nie blokują automatycznie autopilota; wracają na główną ścieżkę po wykazaniu konkretnej blokady zgód, poufności lub używanego przepływu.

- [ ] **D3 — rollback trust:** starsza ścieżka TrustStore.trust/revoke „Ufaj i uruchom”: odtworzenie stanu pamięci po błędzie save; uzgodnić failure paths i testy.
- [ ] **D16 — cleanup fixtures:** make_env w tests/conftest.py zamyka SQLite bez join; test_knowledge_tools nie zamyka service/nie potwierdza terminalności przed close bazy; test_external_mcp_admin_chain pomija close przy nieudanej asercji join. Osobny plan odpowiedzialności i failure paths; bez automatycznego ubijania działań i bez przypisywania lukom dawnego spowolnienia.
- [ ] **D6 — POSIX:** uzgodnić poprawkę wewnętrznych list_directory/search_code (ścieżki względem przypiętego roota) i testów path_guard/file_tools/search_tools. Rozdzielić błąd wywołania od założeń testów Windows; zachować publiczne reguły Windows, nie dopuszczać automatycznie absolutnych POSIX i nie usuwać asercji dla zielonego wyniku.
- [ ] **D2 — izolacja testów skilli:** rozdzielić test_cost_centers_skill.py/test_skill_validator.py od repo conftest i zależności serwera; osobny plan.
- [ ] **D4 — filtry źródeł Skille:** automatyczne sprawdzenia localStorage (brak/uszkodzenie), domyślnych wartości, licznika/pustych wyników i niezależności od dostępności/trust.

## 4. Zewnętrzne MCP — pozostały odbiór i utwardzenie

Kontrakt: [zewnętrzne MCP](../specs/external-mcp-support.md). Nie deklarować pełnej ochrony SSRF ani gotowości produkcyjnej.

- [ ] **M2f — sieć:** zmienny autorytatywny DNS, NAT64/6to4, VPN, koszt DNS/retencja połączeń i zgodność AnyIOBackend po aktualizacji httpcore2; sprawdzić pokrycie ponowionych paczek względem CURRENT bez odtwarzania zaliczonych zakresów. Doprecyzować ograniczenia w panelu.
- [ ] **M2g — transport i procesy:** przyczyna odmowy odpowiedzi 4 MiB, rzeczywisty TLS z własnym CA/błędnym certyfikatem, Bearer/nagłówki, długa SSE, POSIX i zagnieżdżone joby Windows (również sklr w obcym jobie). Kontrola procesów nie jest sandboxem.
- [ ] **M5a — lifecycle klienta:** osłonięty spawn/attach i total timeout, stdio po walidatorze; POSIX/job wspólnie z M2g. Bez deklaracji izolacji RAM.
- [ ] **M6 — rzeczywista integracja przez model:** HTTP/SSE/stdio, zmiana projektu, pełny zbiór trust/cofnięcie/ponowne nadanie, open_url, zmiana schematu, limiter, TTL, sekrety, konflikty, timeout/cancel. Nie powtarzać bez przyczyny odebranego restartu/trwałości enabled/trust i pojedynczego zaufanego odczytu. Zakres automatyczny oceniać względem CURRENT, nie otwierać ponownie zielonej pełnej regresji.

## 5. OKF i indeks — odłożone prace

- [ ] **O1 — polityka formatu:** ręczny compatible/require_okf, zakres/wyjątki, restart/alias, zmiana polityki podczas oczekiwania na zgodę, Windows.
- [ ] **O2 — automatyczne docs:** panelowy przełącznik/trwałość, dodanie/usunięcie roota, zniknięcie/powrót docs, pierwszeństwo ręcznych wpisów i junctiony Windows.
- [ ] **O3 — pilot wiedzy:** pomiar kontekstu katalog → search → outline/section wobec pełnych źródeł; dokończyć audyt 22 przekonwertowanych i 3 wcześniej poprawnych dokumentów po audit_budget. Bez konwersji standardu/przykładów/archiwum, docs/knowledge/ i automatycznego require_okf. Odebrać brakujące central/docs/legacy/mixed/junctiony względem CURRENT.
- [ ] **I1 — create_tree i indeks:** konkretne ścieżki po committed, błąd indeksu nie zmienia wyniku zapisu; dostępność pozostałej wiedzy RAM i jeden odczyt na zmieniony dokument.
- [ ] **I2 — rename/usunięcia:** stary/nowy zakres i niepewny rollback bez cichego przywracania starych wpisów.
- [ ] **I3 — zmiany poza sklr:** lekka okresowa inventory IDE/git pull, stat jako filtr i force pełnego sprawdzenia, interwały/backoff; nie kasować niewidzianych wpisów po częściowym skanie.
- [ ] **I4 — retencja:** purge SQLite po usunięciu zbioru/dokumentów i ACL; DPAPI chroni klucz, nie bazę. Przebudowa/staging-generation wymaga osobnego projektu.
- [ ] **I5 — rzeczywisty smoke:** create_file/convert, edycja podczas pełnego skanu, retry zaległych zmian, reconcile_required, spóźnione wyniki/awarie. Sprawdzić ewentualny lokalny plik smoke na Windows; usuwać dopiero po uzgodnieniu i potwierdzić brak wpisu indeksu.
- [ ] **I6 — sesja i wiedza w panelu/harnessie:** bez sesji odświeżane centralne zbiory bez projektowego REFRESH_UNAVAILABLE; Start/wznowienie odświeża aktualne projekty, pauza blokuje model, zmiana folderów nie przerywa niezwiązanego centralnego skanu, koniec/nowa sesja odrzuca stary projektowy skan, cofnięcie/przywrócenie dostępu nie wznawia starej publikacji. Centralna wiedza wymaga zachowanego zatwierdzonego roota; katalog autopilota i work_sessions.enabled=false. Pozostałe skipy osobno, bez ponowienia zaliczonej regresji bez przyczyny.

## 6. Publikacja, migracja i brakujące scenariusze Windows

- [ ] **W4 — ustalenie pozostałego zakresu publikacji:** sprawdzić bieżący Git/remote i późniejsze publikacje; ustalić czy dawna paczka kandydata nadal ma niewysłane zmiany. Jeśli tak, osobno uzgodnić commit/remote/branch i publikację. Wykluczyć config/venv/bazy/klucze/poufne wyniki/logi/smoke i zachować cudze zmiany. Nie deklarować publikacji na podstawie samego zlecenia.
- [ ] **W5 — domknięcie przekazania:** sprawdzić niewykonane kroki procedury PowerShell/migracji z zachowaniem lokalnej konfiguracji i środowiska, restart/aktualizację katalogu przez użytkownika i zależności bez automatycznej instalacji. Odbiory S2–S6/A2b5/I6/D10–D12 w ich zadaniach, nie jako duplikaty. Przed „autonomia ukończona” odebrać faktycznie wydany zakres testów/poprawek/Git.
- [ ] **W6 — pominięte scenariusze:** uzgodnić potrzebny odbiór pandas, POSIX process groups/mode, case-sensitive filesystem i symlink/link/junction. Skip nie jest potwierdzeniem ochrony. Nie instalować zależności ani zmieniać uprawnień automatycznie; wyniki i ograniczenia do CURRENT.

## 7. Małe usprawnienia i odłożone kierunki

- [ ] **K1 — minimalna diagnostyka środowiska:** mały skill jako proponowana forma; jawnie wybrane istniejące środowisko sklr/skilla/projektu, wersja Pythona/platforma i wersje wskazanych pakietów przez importlib.metadata, z raportem braków. Bez importowania ich kodu, analizy symboli/implementacji, eval/exec, instalacji, sieci, serwerów, sekretów i zmiennych środowiskowych. Osobny plan skilla przed implementacją.
- [ ] **K2 — okf-curator, niski priorytet:** skill proponujący porządkowanie typów/tagów, duplikatów/sprzeczności i odnośników według [konwencji kuracji](../specs/okf-integration/curation.md). Bez automatycznego usuwania/przeklasyfikowania; nowe typy i zmiany dokumentów wymagają uzgodnienia.
- [ ] **K3 — warunkowy, odłożony:** semantyczny selektor bieżącego stanu i opcjonalny hard guard projektu. Nie projektować/implementować przed wykazaniem konkretnej potrzeby i uzgodnieniem kontraktu; nie wybierać aktualnego stanu automatycznie z historycznych checkpointów.

Nie realizujemy ogólnego execute_command ani rozszerzenia audytu/konwertera na dowolne katalogi. Zakres docs/ obejmuje podkatalogi; porządki nie usuwają istniejących kontraktów legacy. Historyczne D5 usunięto, nie uznano za wykonane. Decyzje i zakończone zakresy: [archiwum](../archive/backlog-review.md).
