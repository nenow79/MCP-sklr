> **Archiwum sprzed porządków 2026-10-15.** To historia, nie bieżący stan, instrukcje ani backlog. [Mapa archiwum](../../README.md). Treść źródłowa poniżej; poprawiono wyłącznie ścieżki odnośników.

# Plan wdrożenia OKF w sklr

## Aktualny kierunek: kandydat Windows

Po odbiorze C3b użytkownik autoryzował domknięcie wersji do przeniesienia na Windows,
bez kolejnych przerw na paczki i bez obowiązkowej migracji blokującej pracę.
Wdrożono C3c oraz minimalny przepływ P0/P1/P2: project profiles docs/legacy/mixed,
read/search plain Markdown, prepare_project, audyt i konwersję frontmattera po zgodzie.
To aktualizuje dawną kolejność, w której cały etap projektowy czekał na pilot C4.
Szczegóły bieżącego kontraktu: pierwsza sekcja docs/specs/okf-support.md.

Polityka migracji i AGENTS jest advisory. Nadal wymagamy zgody na zapis; brak
konwersji nie blokuje pozostałych narzędzi. Semantyczna kuracja historii, pełny
selektor aktualnej sekcji, hard guard gotowości i osobny skill kuratora są dalszymi
ulepszeniami, nie zależnościami uruchomienia. Nie utworzono produkcyjnych roots/bundles.

Przed publikacją: AST/importy/spójność HTML-JS, brak nowego wyniku pytest, Node lub
smoke serwera. Końcowe testy i pilot na Windows: docs/windows-okf.md. Agent nie ma
w tej sesji prawa zapisu do .git; manifest i skrypt przekazania pozwalają użytkownikowi
opublikować zmiany. Historia poniżej nie zastępuje tej sekcji i CURRENT.md.

Status: kierunek i kolejność zaakceptowane w rozmowie; plan zapisany #152 done/exit0 i sprawdzony odczytem. Kontrakt C0 (ADR 0004 i docs/specs/okf-support.md) zapisany #153 done/exit0, sprawdzony odczytem i zaakceptowany przez użytkownika. Uzupełnienia domykające C0 oraz stan przechodzą osobną decyzję w panelu. Implementacja dopiero na nowym chat, po przedstawieniu konkretnej paczki do akceptacji. Każdy zapis nadal wymaga standardowej zgody; nie zmieniono kodu, konfiguracji ani danych.

## 1. Cel i kolejność

1. Wiedza centralna: odczyt, wyszukiwanie i kontrolowany zapis bundles w ~/.agents/okf/.
2. Wiedza projektowa: ten sam silnik i narzędzia, z docs/ jako docelowym zbiorem dokumentacji, bez równoległych kopii w docs/knowledge/.
3. Konwersja istniejącego Markdown: inwentaryzacja, propozycja metadanych, walidacja i zbiorczy zapis po zgodzie.

Oszczędność kontekstu ma wynikać z przepływu katalog -> wyszukiwanie fragmentów -> pobranie sekcji -> ewentualnie cały dokument. Sam frontmatter nie gwarantuje lepszego wyszukiwania.

Źródła: docs/specs/OKF/SPEC.md (OKF 0.2), AGENTS.md, aktualna sekcja docs/plan/CURRENT.md, istniejące moduły roots, zapisów, kolejki i panelu.

## 2. Proponowane decyzje bazowe

- Pliki Markdown są źródłem prawdy; indeks jest odtwarzalnym dodatkiem.
- Natywne narzędzia MCP i oddzielny okf_engine; skill wspiera opracowanie wiedzy, nie zastępuje kontroli dostępu i zapisów serwera.
- Centralna lokalizacja domyślna: ~/.agents/okf/. To katalog bundles, np. general/, redlake/, sap/. Nie tworzymy automatycznie wszystkich przykładowych zbiorów.
- Projektowa lokalizacja domyślna: docs/ w bieżącym projekcie, wskazana w konfiguracji. Nie utrzymujemy drugiego magazynu tej samej wiedzy w .agents/okf/ lub docs/knowledge/.
- Tożsamość: bundle_id + concept_id. concept_id jest ścieżką POSIX bez .md, względem bundle. Ścieżki są stabilne; automatyczne rename i przepisywanie linków poza MVP.
- Bundles nie przesłaniają się jak skille. Wyniki pokazują źródło i zakres; podobne lub sprzeczne dokumenty mogą być zwrócone obok siebie.
- Zapis wymaga jawnego bundle, jawnego trybu create/update oraz rewizji przy update. Brak automatycznej promocji projektu do centralnej wiedzy.
- Brak wykonywania kodu, SQL, executors i attesters przez narzędzia wiedzy. Attested Computation można odczytać i wyszukać; nie udajemy implementacji protokołu atestacji.
- Bez embeddingów, usług zewnętrznych i trwałej bazy indeksu w pierwszym etapie. Indeks sekcji w pamięci. SQLite FTS5 jako późniejsza optymalizacja, tylko po pomiarze potrzeby i sprawdzeniu środowiska.
- Brak automatycznego zapisywania rozmów, wyników SAP/RedLake, sekretów i surowych danych finansowych.

## 3. Dostęp i konfiguracja

### 3.1 Roots pozostają granicą dostępu

W obecnym roots.py ochrona rw obejmuje centralny katalog skills, jego wnętrze oraz jego przodków. Przy skills w ~/.agents/skills/ sąsiedni ~/.agents/okf/ nie wymaga wyjątku. Nie nadajemy rw do całego ~/.agents/.

Użytkownik tworzy brakujący katalog i zatwierdza osobny, wąski root, np. okf-central. Nie zakładamy jego obecności ani automatycznej zgody. Central_dir wskazujący szerszy katalog lub istniejący nadrzędny root ro mogą zmienić wynik walidacji; konflikt jest błędem, nie powodem do obchodzenia guardów.

Bundle wiążemy z zatwierdzonym root i względnym katalogiem. Konfiguracja bundles nie nadaje nowych praw do systemu plików. Odczyt respektuje roots, zapis dodatkowo rw. Usunięcie roota, zmiana jego ścieżki, aliasu lub trybu musi unieważnić dostęp/cache i przygotowane operacje, jeśli wiązanie przestało być aktualne.

Root rw daje również dostęp przez zwykłe narzędzia plikowe po ich standardowej zgodzie. Włączenie bundle steruje dostępnością w narzędziach OKF, nie jest pełnym ACL dla innych narzędzi ani sandboxem skryptów skills.

### 3.2 Rejestr bundles

Proponowane pola lokalnej konfiguracji knowledge:
- central_dir: domyślna lokalizacja używana do konfiguracji, bez nadania dostępu;
- bundles: jawne wpisy id, label, scope (central/project), root alias, subpath, description, enabled;
- dla projektu: wykluczenia z domyślnego wyszukiwania i polityka ról/sekcji;
- limity skanowania, dokumentu, wyników i budżetu odpowiedzi.

JSON i startowy profil limitów określa docs/specs/okf-support.md (kontrakt C0). C1 wdraża i testuje ten kontrakt, nie zmienia go bez osobnego uzgodnienia. ConfigStore.update jest jedyną ścieżką zmian konfiguracji. Panel: lista bundles, dodanie/usunięcie wpisu i włączenie/wyłączenie. Brak narzędzia MCP do samodzielnego nadawania dostępu lub włączania bundles.

Wyłączenie bundle blokuje listowanie jego konceptów, wyszukiwanie, get i przygotowanie save. Po zgodzie na zapis kontrolujemy tę dostępność ponownie. Wyłączenie/utrata dostępu usuwa jego dane z aktywnego indeksu.

## 4. Kontrakt narzędzi MCP

Nazwy centralnych narzędzi ustalone w C0; dokładne sygnatury i semantyka w docs/specs/okf-support.md są nadrzędne wobec skrótu poniżej:

### knowledge_bundles

Zwraca dostępne i włączone bundles: id, opis, scope, możliwość zapisu oraz stan skanu. Nie zwraca ukrytych konceptów ani treści. Diagnostyka konfiguracji wyłączonych zbiorów jest w panelu.

### knowledge_search

Parametry: query, opcjonalne bundle/scope, type, tags, status, limit, max_total_chars oraz opcja objęcia archiwum w etapie projektowym. query może być puste tylko przy jawnej operacji listowania z filtrami i limitem.

Wynik: bundle_id, scope, concept_id, title, description, type, tags, status, trust_tier, stale, ostrzeżenia, section_id/heading, zakres linii, krótki fragment i opaque revision. Dla brakujących lub niepoprawnych metadanych wskazujemy unknown/warning, nie wymyślamy wartości.

Ranking: tytuł i tagi/opis ważniejsze niż przypadkowe dopasowanie w treści; uwzględniamy nagłówki i sekcje. Relewancja pierwsza, status i aktualność jako jawne sygnały. Lokalność nie jest dowodem prawdziwości. Opisujemy semantykę wielu słów, frazy i polskich znaków; MVP nie obiecuje polskiego stemmingu ani rozumienia synonimów.

Deprecated i archiwum pomijamy domyślnie z jawną informacją o polityce, ale można je włączyć. Stale nie znika po cichu: wynik z ostrzeżeniem. Draft pozostaje dostępny z oznaczeniem.

### knowledge_get

Parametry: bundle_id, concept_id, opcjonalna sekcja lub żądanie outline, max_chars, opcjonalnie expected_revision.

Zwraca metadane oraz outline, konkretną sekcję albo ograniczoną treść dokumentu. Sekcje identyfikujemy deterministycznie; powtarzające się nagłówki nie mogą powodować pobrania niewłaściwego fragmentu. Rewizja dotyczy całego pliku i wiąże również sekcje.

Linki do konceptów są normalizowane względem bundle. Ukośnik początkowy OKF oznacza bundle root, nie dysk/Windows root. Adapter zamienia go na bezpieczny subpath przed path guardem. Linki poza bundle lub do niedostępnych plików pozostają odniesieniami, nie rozszerzają dostępu. Scope descriptors w sources nie są automatycznie interpretowane jako pliki. Żadnego automatycznego pobierania URL.

### knowledge_save

Parametry: bundle_id, concept_id, mode=create/update, metadata, body, expected_revision dla update, opis operacji.

Walidacja i przygotowanie planu przed kolejką. Create odrzuca istniejący cel; update odrzuca brak pliku lub inną rewizję. Jedno zadanie, maskowany diff, zgoda wyłącznie w panelu, natychmiastowy task_id. Po zgodzie ponowna kontrola pliku, root, dostępu bundle i kolizji pod wspólnym lockiem zapisów.

Nowe katalogi wymagają uzgodnionego przygotowania; pierwszy MVP zapisuje do istniejących katalogów. Tworzenie bundle/struktury wykonujemy osobno przez zatwierdzone create_tree lub panel Foldery. Nie rozszerzamy ukradkiem odpowiedzialności knowledge_save.

Nieznane klucze zachowujemy. Przy update nie usuwamy ich przypadkowo przez pełną serializację ograniczonego modelu. Brak zmian ma jednoznaczny wynik bez dodatkowego zadania. Po sukcesie odświeżamy indeks; błąd odświeżenia nie zmienia udanego zapisu w polecenie ponowienia. Wynik rozróżnia committed, stan indeksu i rollback/cleanup.

### Narzędzia etapu konwersji

- knowledge_audit: odczytowa inwentaryzacja i raport zgodności dla jawnego bundle/subpath, bez zapisu.
- knowledge_convert: lista plików, oczekiwane rewizje i propozycje metadanych; deterministyczna walidacja i jeden zbiorczy plan zmian frontmattera do panelu.

Nie dodajemy ogólnego narzędzia knowledge(action=...) ani osobnego API dla każdej domeny.

## 5. Parser, format i sygnały jakości

- Oddzielny parser OKF; można wykorzystać sprawdzone helpery, ale nie importować tolerancyjnych napraw YAML ze skilli, które zmieniałyby znaczenie dokumentu.
- UTF-8, frontmatter na początku, niepusty string type; bez niebezpiecznych konstruktorów YAML. Limity rozmiaru, zagnieżdżenia/aliasów i czasu. Duplikaty kluczy oraz niejednoznaczne wartości zgłaszamy, zamiast wybierać cicho jedną wersję.
- Oddzielamy minimalną zgodność strukturalną od ostrzeżeń semantycznych. Brak opcjonalnych rodzin, nieznane typy/pola i zerwane linki nie odrzucają poprawnego konceptu.
- verified mapping normalizujemy do jednoelementowej listy. Przy pustych/niepoprawnych zdarzeniach nie przypisujemy wyższego zaufania.
- status bez pola oznacza stable zgodnie z OKF, nie oznacza verified. Niepoprawny status zgłaszamy.
- stale_after porównujemy z czasem UTC po sprawdzeniu jawnego offsetu. Brak stale_after oznacza brak deklarowanej granicy, nie gwarancję aktualności.
- verified to deklarowany sygnał, nie podpis i nie uprawnienie. Weryfikacja starsza od ostatniej znaczącej zmiany dostaje ostrzeżenie; sam timestamp nie dowodzi związku z bieżącą rewizją.
- Zgoda na zapis i poprawność YAML nie dopisują human-reviewed ani machine-confirmed.
- Konwersja samych metadanych nie ustawia daty migracji jako daty ostatniej znaczącej zmiany treści. generated i verified dopisujemy tylko na podstawie rzeczywistego, jawnego zdarzenia; bez zgadywania autorów/modeli/dat.
- Dla nowych dokumentów proponowanych przez agenta politykę generated opisujemy jawnie; nie przypisujemy autorstwa człowiekowi z powodu kliknięcia zgody.
- Zachowujemy reserved index.md/log.md oraz ich specjalne zasady. Outline jest widokiem serwera, nie automatycznie zapisanym index.md.
- Rozbieżności w SPEC (minor z breaking changes, fence vs blok wcięty w przykładach obliczeń) dokumentujemy; nie przeszkadzają w zwykłym odczycie, nie budujemy na nich runtime.

### 5.1 Konwencja kuracji i kontrola rozrostu typów

Uzgodniona polityka opracowywania wiedzy przez LLM: typy są otwarte, ale nowe lokalne typy wprowadzamy wyłącznie za jawną zgodą użytkownika. To konwencja kuracji, nie zamknięta lista wartości w parserze OKF ani nowy mechanizm API. Nieznane typy istniejących/importowanych dokumentów pozostają poprawne; nie zmieniamy ich automatycznie.

- type określa rodzaj i cel dokumentu, a tags oraz ścieżka jego temat. SAP, RedLake, Oracle, M-BO, fabryka i Bosch IT są tematami, nie powodem do tworzenia osobnych wariantów każdego typu.
- Mały słownik startowy: Reference (opis/fakty), Process Description (jak działa proces), Procedure (wykonanie zadania krok po kroku), Data Dictionary (znaczenie i relacje danych), Specification (wymagania/kontrakty), Troubleshooting Guide (diagnostyka i rozwiązania), Link Collection (kuratorowana lista odnośników). Typy projektowe, np. Architecture Decision, Implementation Plan, Project State i Playbook, pozostają dostępne obok nich.
- LLM najpierw sprawdza istniejące typy według znaczenia, unikając synonimów i typów tworzonych tylko dla nowej dziedziny. Przy niepewności proponuje szeroki Reference, jeśli pasuje; gdy nie pasuje, pozostawia klasyfikację do decyzji zamiast wymuszać wybór.
- Gdy żaden istniejący typ nie pasuje, LLM wnioskuje do użytkownika: proponowana nazwa i definicja, dlaczego istniejące typy są niewystarczające, przykłady dokumentów oraz korzyść dla struktury lub wyszukiwania. Nie ustanawia typu samodzielnie.
- Dopiero jawna zgoda użytkownika pozwala dodać nowy typ do lokalnej konwencji i proponować jego użycie w nowych dokumentach. Brak zgody oznacza brak wprowadzenia/użycia proponowanego typu. Zgoda na sam zapis dokumentu nie zastępuje zgody na nowy typ, jeśli wniosek nie został jawnie przedstawiony.
- Zmiany typów istniejących dokumentów i konsolidacja synonimów wymagają osobnego uzgodnienia oraz standardowej zgody na zapis; bez automatycznego przeklasyfikowania.
- Konwencję sprawdzamy na małej próbce rzeczywistych dokumentów, nie projektujemy pełnej taksonomii z góry. W przyszłym skillu kuracji należy stosować tę politykę; nie dodajemy klasyfikatora do C2 i nie tworzymy teraz produkcyjnych bundles/danych.

## 6. Indeks, wydajność i poufność

Pierwszy indeks w pamięci: metadane i fragmenty sekcji z tekstu po maskowaniu. Nie przechowujemy surowej treści w SQLite ani tasks.db. Prywatne bajty planu zapisu żyją jak obecnie w pamięci, a payload/history zawiera maskowany podgląd i bezpieczne metadane.

Przed indeksowaniem oraz dopasowaniem maskujemy tekst. Nie wyszukujemy po sekrecie, który dopiero potem zostałby ukryty w wyniku. Wyniki, outline, tags, descriptions, ścieżki i błędy również podlegają kontroli ujawnienia i bezpiecznemu renderowaniu. Maskowanie nie jest pełnym DLP dla danych biznesowych.

Narzędzia przetwarzające wiedzę rejestrujemy przez register_confidential_tool. To ogranicza telemetrię, nie usuwa ujawnienia przez MCP/czat lub maskowany panel.

Cache jest związany z fizyczną ścieżką, root i aktualną dostępnością bundle. Odczyt get i przygotowanie update sprawdzają bieżący plik. Skan wykrywa ręczne dodania/zmiany/usunięcia przez inwentaryzację i rewizje; stat to optymalizacja, nie pełna gwarancja poprawności. Ustalamy jawny mechanizm odświeżania i dopuszczalne opóźnienie search, a nie deklarację natychmiastowego wykrycia wszystkich zmian bez watcherów.

Skan i wyszukiwanie mają limity liczby plików, głębokości, rozmiaru i czasu. Kooperacyjny budżet nie przerywa blokującego I/O. Zimne odświeżanie dużego bundle oraz audyt mogą działać jako zadania z istniejącym get_task/wait_task, nie trzymają wywołania MCP ponad bezpieczne 8 s. Zwięzłe odpowiedzi pokazują partial/truncated, powód i stan odświeżenia.

Proponowane początkowe limity do pomiaru: do 5000 dokumentów na bundle, do 1 MiB na dokument do indeksowania, search limit 10 (max 50), budżet odpowiedzi search 12000 znaków (max 80000), get domyślnie 12000 znaków, skan foreground do 6 s. Ograniczamy również łączną pamięć indeksu; po osiągnięciu limitu jawny raport, bez cichego udawania pełnego wyszukania.

## 7. Etap C: wiedza centralna

### C0. Kontrakt i ADR

- Zaakceptować niniejszy plan i robocze nazwy narzędzi.
- Przygotować osobny ADR oraz docs/specs/okf-support.md z lokalnym kontraktem sklr. Oryginalny docs/specs/OKF/SPEC.md pozostaje źródłem standardu, bez modyfikacji przy okazji.
- Ustalić dokładne parametry, konfigurację, limits i semantykę rewizji.
- Potwierdzić stan commita poprzedniej pracy i punkt odniesienia testów; nie traktować pełnego pytest jako potwierdzonego.

Odbiór: zatwierdzony kontrakt przed kodowaniem.

### C1. Model, parser i rejestr

Pliki orientacyjne: okf_engine/models.py, parser.py, bundles.py, paths.py; state.py dla defaults. Bez zależności od FastMCP w silniku.

- Parser, reserved files, normalizacja metadanych i diagnostyka.
- Rejestr centralnych bundles związany z roots, domyślnie bez nowych dostępów.
- Testy sztucznych plików i niezależność od rzeczywistego ~/.agents/okf/.

Odbiór: poprawny odczyt minimalnego konceptu, zachowanie rozszerzeń, blokowanie wyjścia poza bundle i dostępu do skills przez alternatywną ścieżkę.

### C2. Katalog, wyszukiwanie i odczyt sekcji

Pliki orientacyjne: okf_engine/sections.py, scanner.py, index.py, search.py; server/knowledge_tools.py; rejestracja w main.py.

- knowledge_bundles, knowledge_search, knowledge_get.
- Indeks w pamięci i jawne odświeżanie; limity, ranking, outline, rewizje i ostrzeżenia.
- Wszystkie wyniki maskowane, źródło jawne, brak auto-fetch i execute.

Odbiór: trafne wyniki na małym bundle, pobranie wybranej sekcji bez pełnego dokumentu, brak dopasowania po zamaskowanym sekrecie, zgodny get po ręcznej zmianie.

#### Wynik C2a — odebrane testowo i zacommitowane

- Baseline C1b 22a3cd1 / przekazanie 342a5d9 potwierdzony przez użytkownika przed kodowaniem; brak niezależnej weryfikacji git przez agenta.
- Zakres: okf_engine/sections.py i tests/test_okf_sections.py. Zapis #204 allow/done/exit0, oba pliki sprawdzone odczytem.
- build_outline przyjmuje wyłącznie publiczne zamaskowane body i body_start_line. ATX, jednoliniowy Setext, pomijanie fenced/indented code; ids s0/s1/... i hierarchia parent_id; fizyczne zakresy start_line/end_line/subtree_end_line oraz get_section direct/subtree z zachowaniem EOL.
- Stały limit 2000 sekcji obejmuje s0 (tylko niepusty zakres przed pierwszym heading). Nadmiar daje partial=true i warning sections_limit; zakresy kończą się przed pierwszym pominiętym nagłówkiem, również Setext.
- Wynik użytkownika dla python -m pytest tests/test_okf_sections.py tests/test_okf_public_view.py -q: 101 passed in 0.39s. Agent nie uruchamiał testów; pełny pytest po C2a niewykonany.
- Nie obiecujemy pełnego CommonMark: brak wieloliniowego Setext, interpretacji kontenerów list/blockquote i inline Markdown. Kontrolę revision/expected_revision wdraża C2c.
- Bez I/O, indeksu, MCP, konfiguracji i danych wiedzy. config.json nietknięty przez agenta. OKF nadal nieoperacyjne. C2b/C2c nierozpoczęte.
- Commit wykonany przez git-ops na polecenie użytkownika: 1395a06 feat: add OKF sections (kod/testy C2a i dokumentacja, 4 pliki). Po commicie na main tylko lokalna zmiana config.json; nie dodano jej do commita. Push niewykonany.
- Użytkownik zaakceptował C2b: okf_engine/documents.py, scanner.py, index.py, search.py oraz cztery odpowiadające pliki tests/test_okf_*.py (documents/scanner/index/search). Zależności odczytane; kod jeszcze nie zapisany. Bez MCP, TaskManager, panelu, main.py i konfiguracji, z wstrzykiwanym dostępem/zegarem/signerem i bez trwałego surowego cache. Następny krok: przygotowanie tej paczki do standardowej zgody w panelu. Testów nie uruchamiać bez wyraźnego polecenia.

#### Wynik częściowego C2b — documents, pauza na polecenie użytkownika

- Zakres wykonany częściowo: okf_engine/documents.py i tests/test_okf_documents.py. Istniały już przy próbie create_tree; odmowa bez nadpisania. Pierwotnego pochodzenia zapisu nie ustalono; przegląd i punktowe poprawki zaakceptowane przez użytkownika. Scanner/index/search i ich testy pozostają do wykonania, C2c nierozpoczęte.
- #216/#217 allow/done/exit0, sprawdzone odczytem: świeży ograniczony odczyt, kontrola długości i snapshotów/ścieżki również po maskowaniu, publiczny wynik i sekcje, wstrzykiwane bieżące bundle/autoryzacja/zegar/signer. check_document kontroluje dostęp i wiązanie publicznego cache przed publikacją/zwrotem, ale nie odświeża treści. Wewnętrznych binding/_bundle i rewizji nie serializować do logów/payloadów; repr wyniku nie zawiera pól.
- Pierwszy wynik użytkownika dla python -m pytest tests/test_okf_documents.py tests/test_okf_paths.py tests/test_okf_public_view.py tests/test_okf_sections.py -q -rs: 3 failed, 213 passed, 2 skipped in 1.75s. Diagnostyka na pliku tymczasowym wykazała 998/1000 różnic lstat/fstat, pierwszy przykład tylko st_ctime_ns. Skips dotyczyły rzeczywistej kolizji case i niedostępnego tworzenia linku.
- #217 usuwa ctime ze snapshotu wyłącznie na Windows; dev/ino/size/mtime i inne kontrole pozostają, inne systemy sprawdzają również ctime. Dodano deterministyczne testy platformowe i odczytu z różnicą ctime. Bez retry i pełnej gwarancji TOCTOU.
- Po poprawce użytkownik potwierdził zielone ponowienie tej paczki bez logu/liczby/czasu; agent nie uruchamiał testów/diagnostyki, pełny pytest niewykonany. Nie deklarować wyliczonej liczby jako potwierdzonej.
- Praca nad OKF wstrzymana na polecenie użytkownika. C2b nie zamknięte i nie zacommitowane; ostatni potwierdzony commit C2a 1395a06, brak push. Aktualnego git status po #214/#216/#217 nie sprawdzano; config.json nietknięty przez agenta i wyłączony z przyszłych commitów.
- Przy wznowieniu odczytać nadrzędną sekcję CURRENT, kontrakt i bieżące moduły/testy, potwierdzić baseline/równoległe zmiany. Dokończyć zaakceptowane scanner/index/search i ich testy, bez MCP/TaskManager/panelu/main.py/config. Testy tylko po wyraźnym poleceniu; odbiór i osobny commit po domknięciu C2b. OKF nadal nieoperacyjne.

#### Wynik C2b — kompletne, odebrane testowo i zacommitowane

- Pliki: documents.py, scanner.py, index.py, search.py i cztery pliki testów. Zapisy #216/#217 (documents), #268 (scanner), #269 (index), #270 (search), wszystkie allow/done/exit0. Bez MCP, TaskManager, panelu, main.py i konfiguracji; OKF nadal nieoperacyjne.
- Wyniki użytkownika: paczka C2b 118 passed in 2.90s; pozostałe testy silnika 377 passed, 2 skipped in 3.09s (kolizja wielkości liter i tworzenie linku niedostępne w środowisku). Agent testów nie uruchamiał; pełny pytest niewykonany.
- Zrealizowane według kontraktu: świeży odczyt z kontrolą dostępu przed/po I/O i po maskowaniu; skan z limitami, bez linków i z osobnym raportem reserved; indeks publicznych dokumentów z generacjami, ticketem, budzetem tekstu i partial; wyszukiwanie terms/phrase/browse z rankingiem, filtrami, polityką deprecated, budżetem odpowiedzi i ponowną kontrolą dostępu.
- Decyzja: katalogi ukryte (kropka na początku) nie są skanowane. Search nie potwierdza świeżości treści; robi to get w C2c. Pozostałe metadane nie dodają punktów rankingu. Brak stemmingu, synonimów i usuwania polskich diakrytyków.
- Commit C2b potwierdzony odczytem Git: 4462aab feat: add OKF search index. Następną paczkę C2c użytkownik zaakceptował 2026-10-15.

#### Wynik C2c — wykonane i odebrane testowo, commit oczekuje (2026-10-15)

- Dodano service.py i server/knowledge_tools.py oraz trzy confidential tools w main.py: knowledge_bundles/search/get. Jeden signer na proces, świeży get, kontrola expected_revision, outline/section/document i budżety JSON zgodne z kontraktem 6.1-6.3.
- Odświeżenia przez TaskManager (knowledge_refresh, bez nowej zgody): auto/cached/force, jedno aktywne odświeżenie na bundle, backoff 5 s, anulowanie i bezpieczne błędy. Panel down/queue full nie uruchamiają skanu synchronicznie. Query, treści i rewizje pozostają poza trwałymi zadaniami/telemetrią; executor nie przekazuje surowych wyjątków do loggera kolejki.
- Konfiguracja i dostęp sprawdzane przy wywołaniach oraz publikacji. Zmiana wersji konfiguracji unieważnia cache i trwające skany; nie ma watchera. Guard końcowej publikacji dodany do index.py pozwala weryfikować dokumenty bez trzymania wspólnego locka configu przez cały czas przygotowania generacji.
- Testy: test_okf_service.py, test_knowledge_tools.py, test_knowledge_tools_registration.py; zaktualizowano wcześniejszy test rejestracji confidential. Obejmują przepływ od zimnego indeksu do sekcji, rewizje/restart, revocation podczas skanu/publikacji, budżety, maskowanie, deduplikację, backoff, anulowanie i rzeczywisty TaskManager na tymczasowym SQLite.
- Użytkownik potwierdził 79 passed in 1.36s dla paczki test_okf_service.py, test_okf_index.py, test_knowledge_tools.py, test_knowledge_tools_registration.py i test_secret_tools_registration.py. C2c odebrane testowo; agent pytest/serwera nie uruchamiał. Pełny pytest repo i ręczny test po restarcie niepotwierdzone. Bez produkcyjnych bundles/roots, edycji config.json, save, panelu Wiedza ani commita C2c. Kolejny krok: commit na polecenie użytkownika, potem uzgodnienie paczki C3.

### C3. Zapis i panel Wiedza

Pliki orientacyjne: okf_engine/writer.py; adapter istniejących PreparedChanges/FileTransaction i TaskManager; server/knowledge_admin.py; panel API/frontend.

- knowledge_save create/update z kontrolą expected_revision.
- Jedna zgoda, maskowany diff, ponowna walidacja po decyzji, brak prawa Ufaj dla zapisów.
- Panel: bundles, scope, root/subpath, enabled, diagnostyka i stan skanu; sesja/Host/Origin jak obecnie. Nie budujemy jeszcze pełnego edytora Markdown ani przeglądarki grafu.
- Testy odmowy, konfliktu, odebrania dostępu, restartu z oczekującym planem i rollback/cleanup.

Odbiór: stworzenie i aktualizacja konceptu przez panel; odrzucenie starej rewizji zarówno sprzed przygotowania, jak i po przygotowaniu; wyłączenie bundle działa backendowo.

#### Podział C3 (2026-10-15, C3a zaakceptowane)

- C3a — przygotowanie dokumentu i podglądu: okf_engine/writer.py, okf_engine/preview.py, tests/test_okf_writer.py i tests/test_okf_preview.py. Czyste operacje na przekazanych bajtach/tożsamości/limitach, bez I/O i serwera. Walidacja create/update, składanie UTF-8, zachowanie istniejącego frontmattera przy metadata_yaml=null, ochrona istniejących kluczy, odmowa nadpisania maskowanego zakresu, no-op i walidacja całego wyniku. Zachowanie BOM i jednorodnych EOL; proponowana polityka v1: odmowa mixed EOL. Oddzielny ograniczony diff publicznego body i syntetycznego publicznego YAML, z oznaczeniem, że YAML nie odwzorowuje komentarzy/formatowania źródła. Testy zachowania rozszerzeń, sekretów, limitów i braku automatycznego dopisywania generated/verified.
- C3b — zapis przez MCP i istniejącą kolejkę: server/knowledge_write_tools.py, integracja service/main/tasks oraz testy. knowledge_save sprawdza expected_revision i dostęp rw, przygotowuje prywatne PreparedChanges, zwraca task_id; jedna zgoda w panelu, bez Ufaj. Po zgodzie ponowna kontrola enabled/binding/revision pod WRITE_LOCK -> ConfigStore.lock i FileTransaction. Jawne committed/rollback_complete/cleanup_complete oraz stan indeksu; błąd indeksu nie oznacza ponowienia zapisu. Dokładne minimalne rozszerzenia istniejących helperów zostaną ustalone przed kodowaniem tej paczki.
- C3c — administracja w panelu: server/knowledge_admin.py, panel/app.py, istniejące pliki panel/static i testy API/UI. Zakładka Wiedza: lista, dodanie/usunięcie konfiguracji bundle, enabled, diagnostyka i stan indeksu. ConfigStore.update, istniejące zatwierdzone roots, zachowanie sesji/Host/Origin. Usunięcie wpisu nie usuwa plików; brak automatycznego tworzenia produkcyjnego roota/katalogu. Scope project pozostaje niedostępne do P1.
- Każda paczka ma osobny odbiór testowy. Użytkownik zaakceptował C3a; C3b i C3c wymagają doprecyzowania integracji i akceptacji przed ich kodowaniem. Pytest i serwer nadal wymagają wyraźnego polecenia.

#### Wynik C3a — wykonane i odebrane testowo, commit oczekuje (2026-10-15)

- Cztery pliki: okf_engine/writer.py i preview.py oraz test_okf_writer.py i test_okf_preview.py. prepare_write przyjmuje tożsamość, tryb, body, propozycję YAML, opcjonalne bajty istniejącego pliku, ParseLimits i jawny czas; zwraca prywatny PreparedWrite. Nie sprawdza rewizji/uprawnień i nie wykonuje I/O: to odpowiedzialność C3b.
- Create: LF/UTF-8 bez BOM. Update: zachowuje BOM, jednorodne EOL i, przy metadata_yaml=None, frontmatter verbatim. Normalizacja jednorodnych EOL propozycji oznaczona eol_adjusted; mixed EOL odrzucane. Katalogów ani danych produkcyjnych nie tworzy. Pełny wynik walidowany parserem OKF; brak uzupełniania autorstwa, dat, statusu lub weryfikacji.
- Ochrona istniejących kluczy rekurencyjnych mapping oraz MASKED_UPDATE_UNSUPPORTED przy zmianie maskowanego zakresu. Dokładny no-op dozwolony. Zgodnie z kontraktem listy są oceniane w diffie, bez zgadywania stabilnego klucza elementów; zmiana list oznaczana metadata_lists_changed. Zmiany generated/verified i status/stale_after/usage_window mają jawne ostrzeżenia.
- build_preview wykorzystuje wyłącznie publiczne widoki po maskowaniu; metadata_diff to syntetyczny YAML. Komentarze i format źródła pominięte z jawnym notice i flagą zmiany samych bajtów/formatowania. Limity pracy difflib i całego JSON preview, poprawna serializacja, jawne truncated/reasons; brak surowych bajtów i hashy/rewizji.
- Kontrola składni AST i git diff --check poprawne. Agent pytest/serwera nie uruchamiał. C3a niezacommitowane; C3b/C3c nierozpoczęte.
- Pierwszy wynik użytkownika: 1 failed, 208 passed in 2.03s. Poprawiono błędne założenie testu budżetu podglądu: długa tożsamość sama nie wymusza RESULT_TOO_LARGE. Oddzielono test dopuszczalnego skrócenia diffu od rzeczywiście nadmiernej otoczki z ostrzeżeniami, której rozmiar jest jawnie sprawdzany. Kod produkcyjny bez zmian. Wynik użytkownika po poprawce dla test_okf_preview.py: 21 passed in 0.22s. C3a odebrane testowo na podstawie obu przebiegów; nie wykonano ponownie całej paczki ani pełnego pytest repo. Następnie doprecyzowanie i akceptacja C3b.

#### Konkretna paczka C3b (zaakceptowana po odbiorze C3a)

- server/knowledge_write_tools.py: przygotowanie create/update z podpisaną rewizją aktualnego snapshotu, roots/rw/enabled, writer i preview z C3a. Prywatny plan wiąże snapshot odczytany do walidacji OKF z tym samym before w PreparedChanges. Zadanie knowledge_write wymaga zgody i zwraca task_id bez wait; brak Ufaj, no-op bez zadania. Payload korzysta z dotychczasowego pola diff panelu, wyłącznie publiczne dane, opis po maskowaniu, bez hashy/rewizji/body/metadata_yaml.
- okf_engine/paths.py: osobny resolver nowego konceptu w istniejącym katalogu rodzica; kontrola wszystkich przodków, linków/junctionów, kolizji wielkości liter/NFC i istniejących celów. Bez mkdir i dopuszczenia reserved index/log.
- server/file_changes.py: minimalny helper przygotowujący dokładne, zwalidowane bajty do PreparedChanges; ograniczony snapshot UTF-8 (bez fallback CP1250 w ścieżce OKF) oraz porównanie ze snapshotem użytym do rewizji. Istniejące edycje zachowują swoje domyślne limity i politykę kodowań.
- server/file_transaction.py: opcjonalny callback warunku dostępu/wiązania w verify i execute, wykonywany pod WRITE_LOCK -> ConfigStore.lock oraz ponownie przed publikacją. Domyślnie brak callbacka, dotychczasowe narzędzia działają jak wcześniej. Zapobiega wyłączeniu bundle pomiędzy decyzją a zapisem, którego sama kontrola root nie wykrywa.
- okf_engine/service.py i adapter knowledge_tools: wewnętrzne zależności zapisu korzystające ze wspólnego signera/locka/limitów; po committed unieważnienie generacji i trwającego skanu bundle oraz próba odświeżenia przez istniejącą kolejkę. Błąd odświeżenia nie zmienia committed ani nie zaleca ponowienia save. Bez surowych danych w odpowiedziach zadań; jawne rollback_complete/cleanup_complete/index_state.
- main.py i tasks/models.py: rejestracja confidential knowledge_save zgodnie z sygnaturą 6.4 oraz kind=knowledge_write. Istniejący panel zadań pokazuje maskowany diff; konfiguracja bundles i zakładka Wiedza dopiero w C3c.
- Testy: nowy test_knowledge_write_tools.py i test_knowledge_write_registration.py, uzupełnienie test_okf_paths.py, testów file_changes/file_transaction i rejestracji confidential. Create/allow -> get/search, update/rewizja, deny/cancel/no-op, zmiana pliku lub enabled/root/rw przed i po zgodzie, kolizje/linki, brak katalogu, brak sekretów w payload/stdout/logach, awaria odświeżania po committed oraz rollback/cleanup. Restart planu w pamięci sprawdzany przez istniejące interrupted kolejki, bez odtwarzania prywatnych bajtów z SQLite.
- Dokumentacja CURRENT/backlog/plan aktualizowana po implementacji i rzeczywistych wynikach. Pytest i serwer nieuruchamiane bez wyraźnego polecenia. Implementację wykonano po akceptacji użytkownika; wynik poniżej.

#### Wynik C3b — odebrane testowo na Linuksie, commit oczekuje (2026-10-15)

- Użytkownik doprecyzował platformy: development Linux, docelowe wdrożenie Windows. Reguły publicznego guarda nadal Windows. Pierwszy wynik C3b: 26 failed, 294 passed, 1 skipped in 4.06s (skip junction Windows). Wskazano wspólne źródło błędów: _check_temp przekazywał absolutne /tmp/... do guarda odrzucającego taki zapis. Zmieniono wewnętrzne wywołanie na ścieżkę względem przypiętego roota, zachowując alias/write=True i pozostałe kontrole stagingu. Dodano test regresyjny. Wynik użytkownika po poprawce dla test_file_transaction.py i test_knowledge_write_tools.py: 86 passed, 1 skipped in 2.17s; skip junction Windows. C3b odebrane testowo na Linuksie na podstawie obu przebiegów; testy na docelowym Windows pozostają do wykonania.

- knowledge_save udostępnione jako confidential, z prywatnym planem dokładnych bajtów, wspólnym signerem procesu i kontrolą rewizji przed submit. No-op bez zadania; create/update wymagają istniejącego rodzica i aktualnego dostępu rw. Publiczny, maskowany podgląd z C3a trafia do pola diff istniejącego panelu zadań, z oznaczeniem syntetycznego YAML, formatowania i ostrzeżeń. Brak surowych sekretów/hashów/rewizji w trwałym zadaniu.
- FileTransaction otrzymało opcjonalny guard dla reguł bundle, sprawdzany pod oboma lockami podczas verify i execute, również przed publikacją. Helper przygotowania dokładnych bajtów ponownie sprawdza snapshot z walidacji rewizji; odczyt UTF-8 ograniczony limitami OKF. Stare narzędzia zachowują domyślny odczyt UTF-8/CP1250 i limit 5 MiB. Dodano resolver nowego konceptu z kontrolą istniejących katalogów/kolizji/linków.
- Po committed stara generacja i trwający skan są unieważniane, a nowe odświeżenie trafia do kolejki. Błędy indeksu nie cofają zapisu. Bezpieczne stdout JSON odróżnia committed, rollback_complete, cleanup_complete, index_state i ewentualny błąd indeksu; awaria samej transakcji poza jej normalnym kontraktem oznacza committed=null i instrukcję sprawdzenia pliku.
- Nowe testy adaptera i rejestracji oraz uzupełnienia paths/file_changes/file_transaction/confidential. Obejmują konflikty rewizji/snapshotu, utratę dostępu po decyzji/przed publikacją, no-op, odmowę/anulowanie, poufność, cleanup/rollback, restart i ochronę nowej generacji przed spóźnionym skanem. Agent pytest/serwera nie uruchamiał. Brak nowego pełnego przebiegu paczki/repo i brak commita. Następny krok: konkretna paczka C3c do akceptacji; pilot C4 niewykonany.

### C4. Pilot centralny

- Użytkownik wybiera pierwszy bundle i jawnie zatwierdza root. Bez automatycznej instalacji lub przejęcia ~/.agents/.
- Kilkanaście konceptów wybranych z użytkownikiem. Pierwsze smoke testy na sztucznych/niewrażliwych danych; prawdziwa wiedza dopiero po wyborze zakresu i źródeł.
- Sprawdzić create -> search -> section get -> update -> search oraz ręczną zmianę i restart.
- Zebrać baseline zwykłego search_code/read_file oraz nowego przepływu: trafność, liczba wywołań, łączna objętość zwróconego tekstu, czas i udział pobrań pełnego dokumentu.

Odbiór: zatwierdzony praktyczny wynik, dokumentacja użytkowa, commity i zaktualizowany CURRENT/backlog. Dopiero potem etap projektowy.

## 8. Etap P: wiedza projektowa i konwersja

### Uzupełnienie wymagań: dwa układy dokumentacji i słaby harness

Użytkownik doprecyzował: docelowy MCP będzie pracował z harnessem znacznie uboższym od Codex. Projekty mają zarówno docs/, jak i dwa pliki w korzeniu: .vibecoder_context.md oraz .vibecoder_checkpoints.md. Oba układy muszą być obsługiwane przez audyt i konwersję; możliwy jest też układ mieszany. Przykład w docs/archive/vibecoder przeczytano wyłącznie jako materiał do projektu migracji, nie jako aktualne instrukcje tego repo.

- Bundle oznacza logiczny zbiór dokumentów, nie pojedynczy plik. Pilot centralny może zacząć się od jednego konceptu w jednym bundle. Liczba dokumentów nie zmienia granic dostępu.
- P1/P2 obejmują profile docs oraz legacy-root. Legacy-root wskazuje jawnie te dwa pliki w korzeniu; nie oznacza skanowania i publikowania wszystkich Markdown/kodu/sekretów z roota. Projekt może łączyć źródła docs i legacy w jednym jawnym zakresie, bez powielania treści lub konkurencyjnych nakładających się bundles. Dokładne rozszerzenie konfiguracji zakresu ustalamy przed P1.
- Audyt raportuje osobno instrukcje, streszczenie, bieżący stan, historię, sprzeczności i dokumenty nieprzekonwertowane. Checkpointy nie stają się aktualnymi instrukcjami z powodu daty lub samego słowa „najnowszy”. Przy mieszanym/historicznym dokumencie bieżący zakres musi być jawnie wskazany; nie zgadujemy ostatniej obowiązującej decyzji.
- Konwersja formalna obu profili zachowuje body byte-for-byte, ścieżki i nazwy. Utworzenie AGENTS.md na podstawie dawnych zasad, wydzielenie aktualnego stanu, podział checkpointów lub przeniesienie historii do docs to osobny plan zmian z diffem i zgodą. AGENTS.md pozostaje plikiem instrukcji, nie wymaga przekształcania w zwykły koncept OKF.

### P0. Przygotowanie projektu w ubogim harnessie (propozycja architektury)

Użytkownik oczekuje sygnalizowania obowiązku odczytu AGENTS.md, propozycji utworzenia brakującego pliku z szablonu oraz proponowania lub wymuszania konwersji dokumentacji. Poniższa polityka jest propozycją do uzgodnienia przed kodem; nie zmienia obecnych narzędzi, zgód ani kolejności odbioru centralnego C4.

- Instrukcje inicjalizacyjne serwera i opisy narzędzi kierują agenta do jednego narzędzia przygotowania projektu (roboczo prepare_project). MCP może przekazać instructions podczas initialize, ale nie może zakładać, że klient wprowadzi je do kontekstu modelu. Odwołania protokołu: https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle oraz https://modelcontextprotocol.io/specification/2025-11-25/server/tools.
- Prepare_project zwraca właściwy root, instrukcje AGENTS i ich rewizje, jawnie wskazany aktualny stan, rozpoznany układ docs/legacy/mixed oraz zwięzły raport migracji. Odczyt jest ograniczony i maskowany, skan dotyczy zatwierdzonego zakresu. Stosowanie zagnieżdżonych AGENTS musi uwzględniać katalog planowanej operacji.
- Brak AGENTS: jawny komunikat, gotowa propozycja szablonu z potwierdzonymi informacjami i zadanie do zgody w panelu. Nie zgadujemy komend, środowisk ani zasad użytkownika. Sam brak pliku nie upoważnia do zapisu.
- Proponowana polityka: wymagane dostarczenie instrukcji i audyt przed modyfikacjami projektu; konwersja automatycznie proponowana. Opcjonalna polityka wymagana przez użytkownika blokuje zwykłe modyfikacje do konwersji jawnie wskazanego aktywnego zakresu dokumentacji. Nie wymusza konwersji archiwów, kopii upstream lub wszystkich .md w repo. Utworzenie AGENTS i sama migracja muszą pozostawać dostępne jako ścieżka naprawy po zgodzie.
- Egzekwowanie wymaga wspólnego guarda również dla zwykłych zapisów, operacji katalogowych i skryptów skilli/komend; samo knowledge_save nie wystarczy. Nie blokujemy dostępu do odczytu, diagnostyki ani panelu. To rozszerzenie polityki, odrębne od obecnego C3c, wymaga osobnego kontraktu/testów.
- Stan przygotowania wiążemy z projektem, właściwymi instrukcjami i ich rewizjami. Brak globalnego „przeczytano AGENTS” dla całego procesu. Trzeba ustalić tożsamość sesji/pracy, ponieważ sesja transportu MCP nie musi odpowiadać jednej rozmowie/chatu. Serwer może potwierdzić dostarczenie instrukcji i odmówić własnych operacji, ale nie rozumienie/stosowanie tekstu przez LLM ani działania poza MCP.
- Sposób wymuszania konwersji (propozycja vs wymóg wybranego zakresu) pozostaje do decyzji użytkownika. Nie wprowadzamy blokady samym dopisaniem tego planu.

### P1. Projektowe docs i tryb migracyjny

- Projekt identyfikowany przez bieżący domyślny root; obsługa innych projektów tylko po jawnym wyborze, bez globalnego przeszukiwania wszystkich roots.
- Jawny wpis bundle projektu, subpath=docs lub inna zatwierdzona lokalizacja. Brak automatycznego włączania nieznanych zbiorów znalezionych przez skan.
- Domyślne search: bieżący włączony projekt + włączone centralne bundles. Jawne scope/bundle pozwala zawęzić wynik.
- Zmiana domyślnego projektu odświeża zakres, bez mieszania cache; przygotowane zapisy pozostają związane z konkretną tożsamością projektu/bundle i przechodzą ponowną walidację.
- Podczas migracji można wyszukiwać także zwykły Markdown, oznaczony format=markdown/unconverted; type i role są wtedy unknown albo jawnie inferred, nie udajemy frontmattera.
- docs/archive/ domyślnie poza wyszukiwaniem, ale objęte raportem zgodności całego docs. Inne wykluczenia ustalamy jawnie.
- AGENTS.md poza bundle docs, nadal nadrzędne zasady pracy. Plan/spec/ADR pozostają oryginałami, nie generujemy konkurencyjnych kopii.
- Obsługujemy również jawny profil legacy-root (.vibecoder_context.md i .vibecoder_checkpoints.md) i układ mieszany, według uzupełnienia wymagań powyżej.

Odbiór: centralne i projektowe wyniki mają jawne źródła, podobne ścieżki nie powodują przesłaniania, zmiana projektu nie zwraca treści poprzedniego zakresu.

### P2. Audit i bezpieczna konwersja serwera

- knowledge_audit raportuje poprawne OKF, brak frontmattera, błędny YAML, braki type, reserved conflicts, format/encoding, linki i wyjątki.
- Audit/convert obsługują zarówno docs/, jak i wskazane dwa pliki .vibecoder_* z korzenia projektu. Nie traktujemy historycznych checkpointów jako samodzielnego dowodu bieżącego stanu; reorganizacja treści pozostaje osobną operacją.
- knowledge_convert przyjmuje jawnie wskazane pliki, rewizje i metadane. Model może zaproponować semantykę, serwer nie używa LLM do fizycznego zapisu.
- Konwersja in place, wyłącznie frontmatter. Zachowujemy bajty body, ścieżki, linki, kodowania/końce linii, które można bezpiecznie zachować; niezgodne z UTF-8 pliki wymagają osobnej zgody na transkodowanie, nie są ogłaszane zgodnymi OKF.
- Nieznane klucze, komentarze i format istniejącego YAML nie są niszczone przez automatyczny dump. Jeśli nie umiemy bezpiecznie wykonać wąskiej zmiany, plik jest wyjątkiem do ręcznej decyzji.
- Idempotencja: ponowna konwersja nie dopisuje drugiego frontmattera ani nie zmienia poprawnego dokumentu bez potrzeby.
- Sugerowana paczka: do 10 plików, do 20 MiB łącznie; dokładne limity dostosować do obecnego planu transakcji. Jedna zgoda na paczkę; bez obietnicy pełnej atomowości przy awarii procesu/systemu.
- Reserved index.md/log.md nie dostają zwykłego type; błędne użycie tych nazw wymaga osobnej decyzji. Kopie upstream speców i przykłady innych formatów wymagają wyboru: pozostawić oryginał, wyłączyć z lokalnego skanu albo zmienić w osobnej operacji.
- Wykluczenie nie czyni całego docs conformant: raport jawnie odróżnia poprawne koncepty w aktywnym zakresie od zgodności całego drzewa. okf_version nie jest certyfikatem zgodności.

Odbiór: testy byte-identical body, zachowanych metadanych, powtórzenia konwersji, konfliktu rewizji, reserved files, cp1250, BOM i CRLF. Brak sfabrykowanych verified/generated/sources.

### P3. Skill do kuracji i konwersji

Robocza nazwa: okf-curator. Tworzony dopiero po stabilnym kontrakcie P2, z użyciem skill-writer.

- Develop w dev-skills, własne tests/ przy skillu, walidacja formatu. Promocja do centralnych skills ręcznie przez użytkownika.
- Instrukcja: audit -> odczyt wybranych dokumentów -> propozycja type/title/description/tags i lokalnych rozszerzeń sklr -> convert -> decyzja w panelu -> kontrola wyniku -> audit.
- Typy i ich rozwój zgodnie z sekcją 5.1: mały słownik preferowany, otwarty format OKF, nowy lokalny typ wyłącznie po wniosku LLM i jawnej zgodzie użytkownika. Skill nie tworzy typów ani nie przeklasyfikowuje dokumentów samodzielnie.
- Rozszerzenie sklr może opisywać role, stan decyzji i sekcję bieżącą. Status ADR accepted nie jest automatycznie OKF stable, a stable nie jest verified.
- Skill nie uruchamia zapisującego skryptu, który omija panel. Ewentualne skrypty są odczytowe; właściwa konwersja używa knowledge_convert.
- Konwersja dużego docs w małych paczkach, bez ładowania całego zbioru do jednej rozmowy i bez background auto-kuracji.

Odbiór: skill korzysta z rzeczywistych narzędzi, podaje źródła metadanych i wskazuje niepewność; walidator oraz testy nie wymagają produkcyjnej bazy wiedzy.

### P4. Pilot docs sklr i odbiór projektu

- Najpierw po jednym dokumencie: spec, ADR, plan i aktualny stan. Bez zmiany archiwów i oryginalnej specyfikacji OKF przy okazji.
- Metadane dokumentu nie rozwiązują mieszaniny historii i bieżącego stanu w CURRENT.md. Ustalamy jawny selector sekcji bieżącej lub stabilny nagłówek; brak pasującego selektora daje warning, nie cichy wybór historycznej treści.
- Przeniesienie historii lub zmiana struktury body to osobna zaakceptowana operacja, nie konwersja frontmattera. Istniejąca nadrzędność CURRENT i AGENTS pozostaje.
- Sprawdzić pytania: dlaczego jest panel, co jest wdrożone, co tylko zaplanowane, jakie są ograniczenia transakcji, gdzie znajduje się bieżąca instrukcja.
- Porównać pobrane fragmenty z oryginałami. Testy muszą wykrywać zwrócenie zastąpionej sekcji jako bieżącej oraz traktowanie planu jak dowodu wykonania.
- Dopiero po próbie szersza migracja aktywnych docs, potem świadoma decyzja o archiwach/przykładach i ewentualnej pełnej zgodności całego drzewa.

Odbiór: trafność i objętość kontekstu lepsza w ustalonym zestawie pytań, bez nieuzasadnionego deklarowania poprawy o konkretny procent przed pomiarem.

## 9. Minimalna macierz testów

- Parser: type-only, unknown fields/types, verified mapping/list/empty, daty z offsetem i błędne daty, status absent, duplicate YAML keys, niebezpieczne tagi, aliasy/limity, reserved files.
- Paths: ../, slash bundle-relative, Windows drives/UNC/ADS, symlink/junction i zmiana wiązania root, link poza bundle, descriptor zamiast path.
- Search: ranking tytuł/opis/tags/body, polskie znaki, repeated headings, partial budgets, draft/stale/deprecated, zimny indeks, ręczne zmiany/usunięcia, maskowanie przed dopasowaniem i brak wycieku z cache.
- Write: create collision, update revision, zmiana przed prepare i przed execute, ro/disabled, decyzja deny, restart, cancellation, rollback/cleanup, udany zapis z błędem odświeżenia indeksu, zachowanie unknown metadata.
- Panel: sesja/Host/Origin, backend enabled, rollback config po błędzie, tekst jako dane (bez wykonywania HTML/instrukcji).
- Project/conversion: zmiana projektu, brak przesłaniania, archive policy, mixed-vs-conformant, byte-identical body, BOM/encoding/EOL, dry audit, idempotencja, reserved conflicts i brak fałszywej weryfikacji.

Testy silnika/MCP w repo, na sztucznych danych i tymczasowych katalogach. Testy skilla przy skillu. Agent nie uruchamia pytest/Node/serwera ani instalacji bez wyraźnego polecenia; po każdym kroku podaje użytkownikowi komendy. Wyniki i ręczne smoke zapisujemy dopiero po rzeczywistym potwierdzeniu.

## 10. Poza zakresem pierwszego wdrożenia

- Trwała baza wektorowa, embeddingi, automatyczny model do streszczeń.
- Wykonywanie/atestacja computation i modyfikacja sanctioned code przez agenta.
- Zaufanie kryptograficzne do deklaracji verified, podpisy dokumentów.
- Automatyczna promocja/synchronizacja projektu do centralnego bundle.
- Automatyczne commits/push, migracja archiwów i zmiana ról istniejących AGENTS/CURRENT/ADR.
- Ogólny sandbox, pełne DLP i pełna odporność na zewnętrzne wyścigi TOCTOU.
- Wyszukiwanie wszystkich projektów naraz, grafowa przeglądarka i rozbudowany edytor wiedzy w panelu.

## 11. Dokumentacja i następny krok

C0 domknięte: dokumenty planu #152, ADR/spec #153 oraz statusy/limity/przekazanie #154 zapisane (allow/done/exit0), wyniki i zmienione sekcje sprawdzone odczytem. Użytkownik zaakceptował kierunek i kontrakt. Implementacja nie rozpoczęta.

Na nowym chat zaczynamy od AGENTS.md, bieżącej sekcji CURRENT, tego planu, ADR 0004 i docs/specs/okf-support.md oraz list_roots. Pierwsza paczka to C1a: modele/parser/public_view/revisions i samodzielne testy, bez integracji MCP, state.py, panelu, skanera i danych produkcyjnych. Po odbiorze C1a dopiero C1b: rejestr/paths/limits/defaults. Dokładny podział i kryteria w sekcji 9 kontraktu.

Użytkownik potwierdził commit create_tree i zielony wynik pełnego pytest (bez logu/liczby testów). Testy serwera sklr wykonuje we własnym venv, nie w conda. Przed kodowaniem pozostaje potwierdzenie commita dokumentacji C0 i aktualności baseline po ewentualnych kolejnych zmianach. Agent nie wykonywał git ani pytest. Brak produkcyjnego roota OKF nie blokuje C1a/C1b testowanych na sztucznych danych. Wybór rzeczywistego pierwszego bundle i jego danych pozostawiamy C4; nie jest potrzebny do opracowania parsera.

Każdy ukończony krok ma: listę plików, wynik testów/smoke, znane ograniczenia, aktualizację bieżącej sekcji CURRENT i backlog oraz propozycję krótkiego Conventional Commit. Nie commitujemy config.json, tasks.db*, logs, danych centralnych bundles ani artefaktów testowych do repo sklr.

Punkt odbioru C4 zamyka działającą wiedzę centralną przed rozpoczęciem P1. P4 zamyka praktyczną integrację docs przed decyzją o indeksie trwałym lub semantycznym.
