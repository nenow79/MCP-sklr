# Plan wdrożenia OKF w sklr

Status: kierunek i kolejność zaakceptowane w rozmowie; plan zapisany #152 done/exit0 i sprawdzony odczytem. Kontrakt C0 (ADR 0004 i docs/specs/okf-support.md) zapisany #153 done/exit0, sprawdzony odczytem i zaakceptowany przez użytkownika. Uzupełnienia domykające C0 oraz stan przechodzą osobną decyzję w panelu. Implementacja dopiero na nowym chat, po przedstawieniu konkretnej paczki do akceptacji. Każdy zapis nadal wymaga standardowej zgody; nie zmieniono kodu, konfiguracji ani danych.

## 1. Cel i kolejność

1. Wiedza centralna: odczyt, wyszukiwanie i kontrolowany zapis bundles w ~/.agents/okf/.
2. Wiedza projektowa: ten sam silnik i narzędzia, z docs/ jako docelowym zbiorem dokumentacji, bez równoległych kopii w docs/knowledge/.
3. Konwersja istniejącego Markdown: inwentaryzacja, propozycja metadanych, walidacja i zbiorczy zapis po zgodzie.

Oszczędność kontekstu ma wynikać z przepływu katalog -> wyszukiwanie fragmentów -> pobranie sekcji -> ewentualnie cały dokument. Sam frontmatter nie gwarantuje lepszego wyszukiwania.

Źródła: docs/specs/okf/SPEC.md (OKF 0.2), AGENTS.md, aktualna sekcja docs/plan/CURRENT.md, istniejące moduły roots, zapisów, kolejki i panelu.

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
- Przygotować osobny ADR oraz docs/specs/okf-support.md z lokalnym kontraktem sklr. Oryginalny docs/specs/okf/SPEC.md pozostaje źródłem standardu, bez modyfikacji przy okazji.
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

### C3. Zapis i panel Wiedza

Pliki orientacyjne: okf_engine/writer.py; adapter istniejących PreparedChanges/FileTransaction i TaskManager; server/knowledge_admin.py; panel API/frontend.

- knowledge_save create/update z kontrolą expected_revision.
- Jedna zgoda, maskowany diff, ponowna walidacja po decyzji, brak prawa Ufaj dla zapisów.
- Panel: bundles, scope, root/subpath, enabled, diagnostyka i stan skanu; sesja/Host/Origin jak obecnie. Nie budujemy jeszcze pełnego edytora Markdown ani przeglądarki grafu.
- Testy odmowy, konfliktu, odebrania dostępu, restartu z oczekującym planem i rollback/cleanup.

Odbiór: stworzenie i aktualizacja konceptu przez panel; odrzucenie starej rewizji zarówno sprzed przygotowania, jak i po przygotowaniu; wyłączenie bundle działa backendowo.

### C4. Pilot centralny

- Użytkownik wybiera pierwszy bundle i jawnie zatwierdza root. Bez automatycznej instalacji lub przejęcia ~/.agents/.
- Kilkanaście konceptów wybranych z użytkownikiem. Pierwsze smoke testy na sztucznych/niewrażliwych danych; prawdziwa wiedza dopiero po wyborze zakresu i źródeł.
- Sprawdzić create -> search -> section get -> update -> search oraz ręczną zmianę i restart.
- Zebrać baseline zwykłego search_code/read_file oraz nowego przepływu: trafność, liczba wywołań, łączna objętość zwróconego tekstu, czas i udział pobrań pełnego dokumentu.

Odbiór: zatwierdzony praktyczny wynik, dokumentacja użytkowa, commity i zaktualizowany CURRENT/backlog. Dopiero potem etap projektowy.

## 8. Etap P: wiedza projektowa i konwersja

### P1. Projektowe docs i tryb migracyjny

- Projekt identyfikowany przez bieżący domyślny root; obsługa innych projektów tylko po jawnym wyborze, bez globalnego przeszukiwania wszystkich roots.
- Jawny wpis bundle projektu, subpath=docs lub inna zatwierdzona lokalizacja. Brak automatycznego włączania nieznanych zbiorów znalezionych przez skan.
- Domyślne search: bieżący włączony projekt + włączone centralne bundles. Jawne scope/bundle pozwala zawęzić wynik.
- Zmiana domyślnego projektu odświeża zakres, bez mieszania cache; przygotowane zapisy pozostają związane z konkretną tożsamością projektu/bundle i przechodzą ponowną walidację.
- Podczas migracji można wyszukiwać także zwykły Markdown, oznaczony format=markdown/unconverted; type i role są wtedy unknown albo jawnie inferred, nie udajemy frontmattera.
- docs/archive/ domyślnie poza wyszukiwaniem, ale objęte raportem zgodności całego docs. Inne wykluczenia ustalamy jawnie.
- AGENTS.md poza bundle docs, nadal nadrzędne zasady pracy. Plan/spec/ADR pozostają oryginałami, nie generujemy konkurencyjnych kopii.

Odbiór: centralne i projektowe wyniki mają jawne źródła, podobne ścieżki nie powodują przesłaniania, zmiana projektu nie zwraca treści poprzedniego zakresu.

### P2. Audit i bezpieczna konwersja serwera

- knowledge_audit raportuje poprawne OKF, brak frontmattera, błędny YAML, braki type, reserved conflicts, format/encoding, linki i wyjątki.
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
- Zalecane typy: Specification, Architecture Decision, Implementation Plan, Project State, Playbook, Reference. To lokalna konwencja, nie zamknięta lista typów OKF.
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
