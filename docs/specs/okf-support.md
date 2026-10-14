# Obsługa OKF w sklr — kontrakt C0

Status: kontrakt C0 zaakceptowany w rozmowie po zapisie #153 i sprawdzeniu odczytem. Uzupełnienia domykające C0 wymagają zatwierdzenia ich diffu. Implementacja dopiero na nowym chat, po przedstawieniu konkretnej paczki do akceptacji. Ten dokument opisuje planowane zachowanie, nie istniejące narzędzia.

Źródła: docs/specs/okf/SPEC.md (OKF 0.2), docs/plan/okf.md, ADR 0004. Specyfikacja standardu pozostaje niemodyfikowana. Wymagania lokalne dla bezpiecznego zapisu nie są dodatkowymi warunkami zgodności samego OKF.

## 1. Zakres i architektura

Etap centralny dostarcza knowledge_bundles, knowledge_search, knowledge_get, knowledge_save oraz panel Wiedza. Bez auto-fetch URL, wykonywania computation, embeddingów, trwałej bazy indeksu i zapisywania rozmów. Etap projektowy rozszerza ten sam silnik o docs/ i konwersję.

Podział odpowiedzialności:
- okf_engine: modele, parser, bezpieczne ścieżki bundle, publiczny widok po maskowaniu, rewizje, sekcje, skan, indeks, wyszukiwanie i przygotowanie zapisu;
- server/knowledge_tools.py: adapter MCP oraz kolejki/transakcji;
- server/knowledge_admin.py: administracja bundles przez panel;
- main.py/state.py/panel: wstrzyknięcie zależności, defaults, rejestracja i UI.

Silnik nie zależy od FastMCP. Nie zastępuje istniejących roots, ConfigStore, TaskManager i FileTransaction. Dzielenie na pliki według jednej odpowiedzialności; dokładną paczkę plików ustalamy przed każdym krokiem.

## 2. Lokalizacje i tożsamość

Centralny katalog domyślny: ~/.agents/okf/. Zawiera jawnie zarejestrowane bundles, np. general/, redlake/. Nie zakładamy ich istnienia i nie tworzymy ich przy starcie serwera.

Bundle root jest katalogiem względem zatwierdzonego roota. Wszystkie koncepty mają klucz (bundle_id, concept_id). bundle_id: 1-32 znaki, małe litery/cyfry/_/-, pierwszy znak alfanumeryczny, unikalny w rejestrze. concept_id: względna ścieżka POSIX bez końcowego .md; np. tables/orders. API nie przyjmuje ścieżki absolutnej, backslash, .md, pustych segmentów, . lub .. w concept_id. Segmenty przechodzą również istniejącą kontrolę nazw Windows. Reserved index/log nie są concept_id.

Porównanie fizycznych ścieżek uwzględnia semantykę systemu plików. Na Windows nie tworzymy odrębnych konceptów z kolizją wielkości liter. API emituje kanoniczny identyfikator rozpoznanego pliku, a nie samowolnie wybraną kopię.

Nie rejestrujemy dwóch bundles o tym samym fizycznym katalogu ani nakładających się katalogach bundles: unikamy podwójnego indeksowania i niejednoznacznej tożsamości. Central_dir jest sugestią lokalizacji UI, nie uprawnieniem ani ograniczeniem odczytu zatwierdzonych innych lokalizacji.

Wartości linków OKF /tables/orders.md są bundle-relative. Normalizator łączy je z katalogiem bundle przed wywołaniem path guarda. W linkach dopuszczalne jest .. tylko jeśli wynik pozostaje wewnątrz bundle; w bezpośrednim concept_id API nie dopuszczamy .. w ogóle. Fragment #heading pozostaje fragmentem linku, nie jest automatycznie section_id API. URL i sources scope descriptors są odniesieniami, nie poleceniami otwarcia pliku lub sieci. Zerwane linki to warning, nie odrzucenie konceptu. Nie obiecujemy kompletnego parsera wszystkich wariantów linków Markdown w C1-C2.

## 3. Konfiguracja i panel

### 3.1 Defaults

Proponowana sekcja dodawana do DEFAULT_CONFIG, nie do lokalnego config.json w C0:

```json
{
  "knowledge": {
    "central_dir": "~/.agents/okf",
    "bundles": [],
    "limits": {
      "max_bundles": 20,
      "max_documents_per_bundle": 5000,
      "max_depth": 20,
      "max_document_bytes": 1048576,
      "max_frontmatter_bytes": 65536,
      "max_yaml_nodes": 10000,
      "max_yaml_depth": 32,
      "max_alias_references": 32,
      "max_index_text_bytes": 67108864,
      "refresh_after_s": 30,
      "scan_budget_s": 6,
      "search_budget_s": 2
    }
  }
}
```

Powyższe liczby są startowym profilem do testów; zmiany po pomiarze wymagają aktualizacji kontraktu. Limit indeksu 64 MiB dotyczy zsumowanego UTF-8 publicznego tekstu i metadanych, nie rzeczywistego RSS procesu. Osobny limit liczby dokumentów i sekcji ogranicza narzut obiektów. max_sections_per_document=2000 jest stałym limitem v1; przekroczenie daje częściowy outline/indeks z warning, nie pozornie pełny wynik.

Konfiguracja musi być walidowana przed użyciem: bool nie jest liczbą limitu, niepoprawny typ lub przekroczenie granic daje bezpieczną diagnostykę zamiast wyłączenia limitów. Twarde granice v1:
- max_bundles: 1-20;
- max_documents_per_bundle: 1-5000;
- max_depth: 1-20;
- max_document_bytes: 1024-1048576;
- max_frontmatter_bytes: 256-65536, nie więcej niż max_document_bytes;
- max_yaml_nodes: 1-10000;
- max_yaml_depth: 1-32;
- max_alias_references: 0-32;
- max_index_text_bytes: 1048576-67108864;
- refresh_after_s: 1-3600;
- scan_budget_s: 1-6;
- search_budget_s: 1-2.

Wszystkie wartości powyżej są int, bez bool. max_sections_per_document=2000 pozostaje stałe. Limity można obniżyć, ale nie wyłączyć ani podwyższyć ponad profil v1 bez aktualizacji specyfikacji. Niepoprawna sekcja limits blokuje operacje wiedzy z bezpieczną diagnostyką; nie blokuje startu całego sklr i nie jest zastępowana nieograniczonym profilem. Nieprawidłowy wpis bundle pozostaje w configu do naprawy, ale jest niedostępny dla MCP.

### 3.2 Przykładowy wpis bundle

```json
{
  "id": "central-general",
  "label": "Wiedza ogólna",
  "scope": "central",
  "root": "okf-central",
  "subpath": "general",
  "root_path": "C:/Users/USER/.agents/okf",
  "description": "Wiedza wykorzystywana między projektami.",
  "enabled": false
}
```

root_path jest przypiętą kanoniczną ścieżką roota zapisywaną przez administrację po walidacji. Zapobiega cichej zmianie przeznaczenia bundle po usunięciu/reutworzeniu aliasu roota pod inną ścieżką. UI przyjmuje root alias, nie dowolne root_path od modelu; serwer wylicza root_path. subpath jest względny POSIX; puste oznacza katalog roota. Id/label/description muszą być ograniczone (32/200/1000 znaków) i bez sterujących/bidi w polach jednoliniowych. Nowy wpis jest domyślnie wyłączony, użytkownik może jawnie włączyć go w panelu.

C1-C4 aktywnie obsługują scope=central. scope=project jest zarezerwowane i do P1 zgłasza unavailable, nie skanuje automatycznie projektu. Rejestr ma globalnie unikalne ids, bez reguły project > central.

### 3.3 Administracja

Proponowane endpointy panelu (w C3):
- GET /api/knowledge/bundles — wszystkie wpisy, dostępność, enabled i diagnostyka;
- POST /api/knowledge/bundles — dodanie wpisu po sprawdzeniu root/subpath, bez tworzenia folderów;
- POST /api/knowledge/bundles/{id}/update — label/description/enabled; zmiana ścieżki lub id wymaga usunięcia i ponownego dodania;
- POST /api/knowledge/bundles/{id}/delete — usuwa tylko konfigurację, nigdy pliki;
- POST /api/knowledge/bundles/{id}/refresh — uruchamia odczytowy skan, tylko przy dostępności i enabled.

Obowiązują sesja, Host, Origin i brak CORS jak dla obecnego panelu. Wyłącznie ConfigStore.update, rollback pamięci po błędzie zapisu. Frontend renderuje tekst jako dane, bez innerHTML z zawartości dokumentów. Brak pełnego edytora Markdown i podglądu grafu w MVP.

Root musi istnieć i być zatwierdzony osobno. Włączenie nie przyznaje rw i nie obejmuje innych zbiorów. Wyłączenie blokuje wszystkie operacje OKF dla bundle i usuwa jego indeks. Usunięcie roota/zmiana fizycznej ścieżki unieważnia wiązanie. Zmiana ro -> rw/rw -> ro zmienia bieżące writable i unieważnia przygotowane plany z innymi uprawnieniami; odczyt może działać nadal, jeśli wiązanie jest poprawne.

Każda operacja ponownie sprawdza rejestr i roots. Przed zwrotem wyników cache sprawdzamy aktualną dostępność. To nie jest uniwersalny ACL: zwykłe narzędzia plikowe nadal mają prawa nadane root, a skrypty nie są sandboxowane.

## 4. Parser i publiczny widok

### 4.1 Minimalny format

Koncept: plik .md w UTF-8, opcjonalny UTF-8 BOM tolerowany z warning, otwierające i zamykające --- na osobnych liniach, YAML mapping i niepusty string type. Nowe pliki: UTF-8 bez BOM i LF. CP1250 nie jest OKF; nie transkodujemy go przy zwykłym knowledge_save.

Parser nie wykonuje konstruktorów YAML, nie naprawia cicho błędnego YAML. Limity frontmatter/nodes/depth/aliases obowiązują przed kosztowną konstrukcją obiektów. Cykle i niebezpieczne tagi odrzucamy. Duplikaty kluczy dają błąd niejednoznacznego YAML. Klucze mapping są tekstowe; inne klucze, niestandardowe tagi i przekroczenia bezpiecznego profilu są raportowane jako unsupported/safety_limit, nie fałszywie jako brak type. Alias expansion musi być ograniczone również w zagnieżdżonych strukturach. Sklr nie obiecuje obsługi każdego możliwego typu YAML.

Rozdzielamy structural errors, unsupported profile i semantic warnings. Unknown type i dodatkowe klucze nie są błędem. Brak opcjonalnych rodzin nie odrzuca konceptu.

Profil scalarów v1: tekst pozostaje tekstem poza jednoznacznym null, true/false i zwykłymi liczbami dziesiętnymi. yes/no/on/off i daty nie stają się automatycznie bool lub obiektami datetime; daty sprawdzamy jawnie w polach OKF. Nie dopuszczamy NaN/Infinity ani niestandardowych obiektów w publicznym JSON. YAML merge key << i niestandardowe tagi są unsupported profile; nie rozwijamy ich cicho. Aliasy mogą współdzielić bezpieczne węzły, ale cykle są zabronione; limit nodes dotyczy również efektywnego rozwinięcia publicznej struktury, nie tylko liczby kotwic. Parser nie może przejść ograniczonego skanu, a potem wykonać nieograniczonego dump/rekurencji. Używamy istniejącego PyYAML z lokalnym bezpiecznym loaderem/profilem; nie dodajemy zależności ani auto-instalacji w C1. Testy opisują różnice tego bezpiecznego profilu wobec ogólnego YAML.

Reserved index.md/log.md nie są konceptami i nie są w wynikach wyszukiwania konceptów. W C1 raportujemy je osobno. Root index może mieć okf_version; brak index lub nieznana wersja daje best-effort consumption. Zwykły opis obliczenia jest konsumowany jak koncept; brak runtime daje semantic warning dla Attested Computation, bez wykonywania czegokolwiek.

### 4.2 Zaufanie, daty i status

- verified mapping -> lista jednoelementowa; poprawne zdarzenie ma by jako niepusty actor oraz at z jawnym offsetem.
- Poziom deklarowanego trust_tier: unverified bez poprawnych zdarzeń, machine-confirmed dla poprawnych nie-human, human-reviewed dla poprawnego human:actor. Niepoprawne wpisy są jawnie ostrzegane. Nie wykorzystujemy zamaskowanych lub nieczytelnych actorów do podwyższenia zaufania.
- generated.by opisuje autora; generated.at czas znaczącej zmiany. Nie wstawiamy automatycznie autora/modelu użytkownika ani czasu migracji.
- Gdy poprawne generated.at > ostatnie poprawne verified.at: warning verification_predates_content. Jest to tylko sygnał, nie dowód kryptograficzny.
- status absent -> stable. Nieznany/niepoprawny status -> null oraz warning, nie ciche stable.
- stale_after absent -> stale=null (granica nieznana), poprawna data -> bool według now >= stale_after; błędna data -> null/warning.
- Wszystkie daty OKF muszą mieć offset; porównania UTC. Pola źródłowe zachowujemy, publiczny JSON normalizuje poprawne daty do ISO 8601.
- sources wpis wymaga resource; scope descriptor jest dozwolony. Nie ustalamy wiarygodności źródła na podstawie popularności ani nie dopisujemy fikcyjnych źródeł.

### 4.3 Maskowanie OKF — warunek C1/C2

Istniejący server/secrets_mask.py:mask_text działa dla nazw rozpoznanych jako pliki sekretów. Dla ogólnego .md zwykle zwraca tekst bez maskowania. Samo wywołanie tego helpera NIE spełnia kontraktu wiedzy.

Potrzebny okf_engine/public_view.py lub równoważny moduł:
1. Prywatny, ograniczony parser/snapshot waliduje format; nie jest przekazywany do telemetrii, SQLite ani MCP.
2. Dedykowana transformacja maskuje tekst niezależnie od rozszerzenia: jawne przypisania sekretów w YAML/JSON/env (password, passwd, token, secret, credential, api_key/private_key i ich jednoznaczne warianty), URL z hasłem oraz bloki kluczy prywatnych. Zakres wzorców i false positives musi być opisany testami. Przykłady kodu też podlegają maskowaniu.
3. Publiczne metadane pochodzą z maskowanego widoku, nie z surowego parsera. Zagnieżdżone pola w rozszerzeniach i sources są również objęte transformacją. Placeholder ma postać cytowanego tekstu w YAML, aby nie psuć struktury. Jeśli nie można bezpiecznie utrzymać struktury, pole jest ukryte z warning.
4. Indeks, ranking, fragmenty, outline i diff używają wyłącznie widoku publicznego. Nie przechowujemy surowych wartości w lookupach ani używanych przez ranking sygnałach. Zamaskowane wartości nie są słowami wyszukiwania; zapytanie o ukrytą wartość nie może trafić po jej surowej treści.
5. Mapa linii maskowania pozwala powiązać fragment z fizycznymi liniami pliku. Zastąpienie multiline sekretu nie może przesunąć sekcji i podać błędnego zakresu. Sekcje wyznaczamy z maskowanego, zachowującego linie body, nie z frontmattera.

Heurystyka nie wykrywa wszystkich sekretów ani danych poufnych. Title/description/body mogą zawierać wrażliwą prozę nierozpoznawalną wzorcem. Zakaz utrwalania sekretów jest również zasadą kuracji; nie deklarujemy pełnego DLP. Ścieżki/ids powinny być niewrażliwe; wykryty jednoznaczny sekret w identyfikatorze blokuje zwrot wpisu z ogólną diagnostyką, zamiast emisji zniekształconego id.

## 5. Rewizje i sekcje

### 5.1 Opaque revision

Publiczna revision: r1.<base64url-HMAC-SHA256>. Klucz co najmniej 32 losowe bajty, generowany przy starcie i przechowywany tylko w pamięci. HMAC obejmuje domenę/version, bundle id, przypięte kanoniczne root/bundle/file paths oraz SHA-256 prywatnych bajtów pliku, z jednoznacznym kodowaniem pól. Publicznie nie zwracamy surowego digestu, identity, sekretnego klucza ani nie zapisujemy rewizji w payload kolejki/telemetrii. Porównanie constant-time.

Ten sam plik i wiązanie w tym procesie dają ten sam token. Zmiana dowolnych bajtów (także YAML/EOL) lub ścieżki daje inną rewizję. Restart zmienia klucz, więc stara rewizja jest nieaktualna; agent musi wykonać get. Token nie nadaje dostępu i nie podpisuje prawdziwości wiedzy. Równość tokenów może ujawniać zmianę pliku; ochrona dotyczy offline zgadywania treści na podstawie jawnego hasha, nie wszystkich kanałów bocznych.

Po przygotowaniu zapisu transakcja dodatkowo kontroluje prywatny snapshot (hash/identity/mode) jak obecnie. HMAC nie zastępuje kontroli konfliktów ani uprawnień.

### 5.2 Outline i section_id

Podział po ATX heading (# do ######) i Setext heading, z pominięciem fenced i indented code blocks. C1-C2 wymagają testów nagłówków w kodzie, powtarzających się nazw i CRLF. v1 nie obiecuje pełnego CommonMark; nierozpoznany wariant pozostaje treścią, nie jest zgadywanym nagłówkiem.

Sekcje mają ids s1, s2, ... w kolejności rozpoznanych nagłówków. Treść przed pierwszym nagłówkiem to s0. Są to lokalne identyfikatory związane z revision, nie trwałe URL. Outline zawiera id, heading, level, parent_id, start_line, end_line, subtree_end_line.

section_mode=direct zwraca od heading do następnego heading (bez dzieci); section_mode=subtree do następnego heading o równym/wyższym poziomie (z dziećmi). Zakresy liczone od 1 względem oryginalnego pliku. Pobranie section_id wymaga expected_revision, aby zmiana kolejności nagłówków nie pobrała innej sekcji po cichu.

## 6. Narzędzia centralne — API v1

Wspólny format odpowiedzi: {success: true, data: ...} albo {success: false, error: bezpieczny tekst, data: {code: ...}}. Bez tracebacków, surowych błędów YAML i odbijania wrażliwego query/metadata/body. Przykładowe kody: INVALID_ARGUMENT, BUNDLE_UNAVAILABLE, DOCUMENT_UNAVAILABLE, INVALID_OKF, UNSUPPORTED_PROFILE, SAFETY_LIMIT, REVISION_CONFLICT, RESULT_TOO_LARGE, QUEUE_UNAVAILABLE.

Wszystkie cztery narzędzia rejestrujemy register_confidential_tool. Pole enabled sprawdzane w backendzie, nie tylko w katalogu. Niedostępne/wyłączone wpisy nie ujawniają swoich dokumentów; przy jawnym id wspólny BUNDLE_UNAVAILABLE.

### 6.1 knowledge_bundles()

Bez parametrów, bez automatycznego skanu treści. Zwraca tylko enabled i dostępne bundles, w etapie C scope=central:

```json
{
  "count": 1,
  "bundles": [{
    "id": "central-general",
    "label": "Wiedza ogólna",
    "description": "Wiedza wykorzystywana między projektami.",
    "scope": "central",
    "writable": true,
    "index_state": "empty",
    "indexed_documents": 0,
    "indexed_at": null
  }]
}
```

index_state: empty, ready, partial, refreshing, error. W trakcie refresh podajemy też previous_state i refresh_task_id, jeśli istnieją. Diagnostyka wyłączonych/niepoprawnych wpisów pozostaje w panelu. Nie emituje się absolutnych ścieżek domowych w katalogu MCP.

### 6.2 knowledge_search

Sygnatura planowana:

```text
knowledge_search(
  query: str = "",
  bundle_id: str | null = null,
  scope: str = "all",
  type: str | null = null,
  tags: list[str] | null = null,
  status: str | null = null,
  query_mode: str = "terms",
  browse: bool = false,
  include_deprecated: bool = false,
  limit: int = 10,
  max_total_chars: int = 12000,
  refresh: str = "auto"
)
```

- scope: all/central/project; w C project niedostępne, all oznacza centralne. Jawny bundle musi być zgodny ze scope.
- query do 1000 znaków. terms: Unicode NFC + casefold, słowa alfanumeryczne, wszystkie niepuste terms muszą wystąpić w publicznym dokumencie (metadata lub body); brak AND/OR/SQL interpretowanych jako język zapytań. phrase: dosłowne dopasowanie całego query po NFC/casefold w danym polu/sekcji; nie scala granic pól w sztuczną frazę. Bez stemmingu, usuwania polskich diakrytyków i synonimów w v1.
- browse=true dopuszcza puste query; query niepuste z browse=true jest błędem. Browse działa tylko z jawnym bundle_id lub co najmniej jednym filtrem type/tags/status i zawsze ma limit.
- type/status/tags: dopasowanie dokładne po normalizacji/casefold; wszystkie podane tags wymagane. Tags max 20, tag do 100 znaków. Nieznany type jest dozwolony.
- Deprecated pomijane, chyba że include_deprecated=true lub status=deprecated. Draft i stale są dostępne z oznaczeniem; nie podwyższamy zaufania dla stable.
- limit 1-50, max_total_chars 1000-80000. Budżet jest liczony dla JSON results z ensure_ascii=false; JSON zawsze pozostaje poprawny. Najpierw skracamy excerpt z oznaczeniem, potem pomijamy wynik, który nie mieści się z obowiązkowymi ids/metadanymi. Nie przecinamy ids ani rewizji. Nadmierne metadata/snippet mają jawne content_truncated; budżet nie jest obietnicą rozmiaru całej otoczki odpowiedzi ani liczby tokenów.
- refresh: auto/cached/force. Reguły w sekcji 7. Wynik może wymagać odczytowego task bez dodatkowego narzędzia MCP.

Wynik ma jedno trafienie na koncept i jego najlepszą sekcję. Minimalne pola trafienia: bundle_id, scope, concept_id, title (fallback z nazwy pliku), description lub null, type, tags, status, trust_tier, stale, warnings (kody), section_id lub null, heading lub null, start_line/end_line lub null, excerpt, revision, masked, content_truncated. Metadane niezwiązane z dopasowaniem nie wypierają fragmentu; pełne źródła/rozszerzenia są dostępne w get.

Ranking terms, v1: dla każdego term suma wag pól, w których występuje (title 8, tags 6, description 4, heading najlepszej sekcji 3, body 1), każde pole punktowane najwyżej raz dla term. Najlepsza sekcja ma najwięcej dopasowanych terms, następnie heading/body score, następnie najwcześniejszą pozycję. Phrase stosuje te same wagi dla pełnej frazy. Nie mnożymy wyniku przez częstotliwość w długim dokumencie. Remisy: bundle_id, concept_id. Status/trust nie dodaje punktów v1; sygnały są jawne, nie wyrokiem o prawdzie. Browse sortuje po bundle_id/concept_id.

Data odpowiedzi: results, count (liczba zwróconych konceptów), partial (niepełny skan/zakres), truncated (limit odpowiedzi/wyników/czasu), reasons, policy (scope/deprecated/archive), index (stan per wybrany bundle), refresh_tasks. Nie zwracamy arbitralnej deklaracji total_matches, jeśli wyszukiwanie było częściowe. Nie odbijamy query w task payload.

Jawny niedostępny bundle daje BUNDLE_UNAVAILABLE. Brak jakichkolwiek dostępnych bundles dla wyszukiwania zbiorczego daje success=true, results=[], reasons=[no_available_bundles]; nie jest dowodem braku wiedzy na dysku. Brak gotowego indeksu daje partial=true i powód index_not_ready. Gotowy indeks bez trafień daje count=0 bez tych powodów. Te przypadki nie mogą mieć identycznej, nieopisanej odpowiedzi.

### 6.3 knowledge_get

```text
knowledge_get(
  bundle_id: str,
  concept_id: str,
  view: str = "outline",
  section_id: str | null = null,
  section_mode: str = "subtree",
  expected_revision: str | null = null,
  max_chars: int = 12000
)
```

view: outline/section/document. Domyślny outline oszczędza kontekst; cały dokument wymaga jawnego view=document. section_id wymagane wyłącznie dla section, wtedy expected_revision także wymagane. section_mode: direct/subtree. Pozostałe kombinacje nie mają skutków ubocznych, są walidowane jako argumenty.

Get odczytuje snapshot na nowo, waliduje format i maskuje przed zwrotem. Nie korzysta z cache do treści. Jeśli podano expected_revision, mismatch (także po restarcie) daje REVISION_CONFLICT, bez zwrotu nowej treści pod starym id sekcji.

Wspólne data: bundle_id, scope, concept_id, revision, metadata (pełny publiczny mapping), signals (status/trust_tier/stale/warnings), masked, view, truncated. Dla outline dochodzą entries; dla section/document content i zakres linii. max_chars 1000-80000, liczone na JSON metadata+outline/content. Skracamy content lub liczbę pełnych outline entries, nigdy surowy JSON. Jeśli same obowiązkowe/publiczne metadata nie mieszczą się, RESULT_TOO_LARGE z instrukcją większego budżetu, bez fragmentów surowych wartości.

Publiczne metadata to JSON-safe mapping; daty ISO, bez kodu, obiektów Python i YAML aliasów. Dodatkowe pola zachowujemy w widoku, ale nie zwracamy metadata_yaml z surowego pliku. Pełny snapshot może być potrzebny tylko do prywatnego zapisu; masked content nie jest wierną kopią surowych sekretów.

### 6.4 knowledge_save

API precyzuje ogólne metadata z planu jako tekst YAML, aby zachować rozszerzenia i uniknąć udawania bezstratnej serializacji ograniczonego modelu:

```text
knowledge_save(
  bundle_id: str,
  concept_id: str,
  mode: str,
  body: str,
  metadata_yaml: str | null = null,
  expected_revision: str | null = null,
  description: str = ""
)
```

- mode=create/update, bez upsert. body jest Markdown bez frontmattera, do złożenia z YAML. metadata_yaml to sam YAML mapping bez linii delimiterów. create wymaga metadata_yaml i braku expected_revision; update wymaga expected_revision.
- update z metadata_yaml=null zachowuje istniejący frontmatter verbatim. Podany metadata_yaml jest jawną pełną propozycją bloku, NIE patch; diff pokazuje wynik. C0 nie obiecuje zachowania komentarzy w bloku jawnie zastąpionym przez użytkownika/model. Konwersja P2 ma silniejszy kontrakt wąskiej zmiany.
- Przy pełnej propozycji update sprawdzamy rekurencyjnie obecność istniejących kluczy mapping, również nieznanych. Ciche usunięcie klucza jest błędem METADATA_KEY_LOSS. Elementy sources/verified oraz listy o różnym kształcie muszą być ocenione w diffie; nie ma uniwersalnego stabilnego klucza list OKF. W C1-C3 nie dostarczamy API usuwania metadanych. Jawne usunięcie wymaga osobnej zatwierdzonej edycji pliku, nie obejścia walidacji przez save.
- Aktualizacja masked dokumentu: nie dopuszczamy ślepego zastąpienia body/metadanych placeholderami z get. Jeśli prywatny snapshot wykrywa maskowane wartości w zastępowanym zakresie, save zwraca MASKED_UPDATE_UNSUPPORTED. Update body w dokumencie z zamaskowanym body jest zablokowany; jeśli maskowanie dotyczy tylko zachowanego frontmattera (metadata_yaml=null), aktualizacja niemaskowanego body może działać. Jawna naprawa takich dokumentów to osobna operacja, nie automatyczne ujawnienie.
- Create oraz cały wynik update muszą być poprawnym profilem OKF i mieścić się w max_document_bytes. Istniejący CP1250 jest błędem konwersji wymagającej P2/osobnej zgody. BOM/EOL UTF-8 istniejącego pliku zachowujemy tam, gdzie bezpieczne; mixed EOL wymagają jawnego warning/diff lub odmowy w v1. Body nie jest skracane do limitu odpowiedzi.
- Katalog rodzica musi istnieć. Brak auto-mkdir/rename, zapisu index/log i auto-generated indeksów/historii.
- Semantic warnings nie są automatyczną odmową, ale wrażliwe zmiany trust/lifecycle muszą być widoczne w podglądzie. Nie dopisujemy generated, verified, sources ani status poza propozycją użytkownika/modelu. Autorstwo i weryfikacja mają być prawdziwe; kliknięcie Allow nie oznacza human-reviewed.
- No-op: success=true, data.changed=false, brak task. Create collision i brak pliku update to błędy przed submit.

Przepływ:
1. Sprawdzenie enabled/root/subpath/rw, zwykłych plików i brak links/junctions.
2. Snapshot i expected_revision; pełna walidacja/limity i prywatny plan.
3. Dedykowany maskowany preview OKF. Dotychczasowy PreparedChanges.preview bez transformacji OKF nie jest wystarczający dla .md. Prywatne PreparedChanges nie są serializowane.
4. Task kind=knowledge_write, needs_approval=true, offer_trust=false; natychmiastowy task_id i status, brak blokującego wait. Payload ma bezpieczne identyfikatory, description, maskowany diff, warnings/truncated, bez body/metadata_yaml/tokenów/hashy.
5. Po decyzji oraz przed wykonaniem sprawdzenie wiązania bundle, enabled, uprawnień i snapshotu pod WRITE_LOCK oraz wspólnym ConfigStore.lock. Użycie FileTransaction nie zwalnia adaptera z kontroli enabled.
6. Sukces zapisu -> usunięcie starego publicznego wpisu i próba indeksowania nowej wersji. Błąd indeksu nie oznacza cofnięcia zapisu i nie może skłaniać do ponowienia create/update bez get.

Wynik początkowy: task_id, status, kind, bundle_id, concept_id, sensitive, truncated, hint o decyzji użytkownika. Get_task po sukcesie ma bezpieczne stdout z committed=true i index_state. Nie dodajemy surowych metadanych/revision do trwałego stdout.

FileTransaction zwraca committed/rollback_complete/cleanup_complete; adapter zachowuje ich rozróżnienie w bezpiecznym podsumowaniu. Cleanup failure może oznaczać committed=true i task failed: instrukcja sprawdzenia pliku, nie automatyczne powtórzenie. Restart unieważnia plan w pamięci; kolejka oznacza interrupted zgodnie z istniejącą logiką. Po :ping najpierw list_tasks, nie ponowienie zapisu w ciemno.

## 7. Skan, cache i odświeżanie

Indeks powstaje tylko z enabled/dostępnych bundles, wyłącznie z publicznego widoku. Skany pomijają symlinki/junctions i kontrolują wszystkie przodki katalogu oraz finalne ścieżki, nie tylko wynik scandir. HMAC/private digest może służyć wyłącznie kontroli rewizji, nie rankingowi. Snapshot/parse raw nie jest trwałym cache wiedzy.

C2 uruchamia pełne odświeżanie asynchronicznie przez TaskManager, kind=knowledge_refresh, needs_approval=false: użytkownik wcześniej włączył odczyt bundle. Zadanie nie uruchamia skryptu ani sieci. Do payload/stdout trafiają wyłącznie bezpieczne metadane stanu/liczniki. Query i znalezione treści nie są przechowywane w zadaniu. Wyniki wyszukiwania pobieramy ponownym knowledge_search po zakończeniu skanu, nie z trwałego stdout.

Reguły refresh w search:
- auto: dla empty lub skanu starszego niż refresh_after_s planuje odświeżanie (najwyżej jedno na bundle). Może zwrócić poprzedni publiczny indeks z indexed_at/refreshing. Jeśli brak indeksu, results=[], partial=true i task_id; agent czeka przez istniejące get_task/wait_task i ponawia search.
- cached: nie uruchamia skanu, jawnie pokazuje wiek/state i empty/partial. Nie omija kontroli dostępności.
- force: uruchamia lub dołącza do trwającego skanu; wynik oznacza refresh requested i nie jest deklarowany jako świeży. Bez pętli skanów dla tego samego bundle.

Po kompletnym skanie publikujemy nową generację atomowo w pamięci. Skasowane/ręcznie zmienione pliki znikają/zmieniają się w nowej generacji. Po niepełnym skanie można opublikować wyłącznie zweryfikowany podzbiór jako partial z powodem; nie mieszamy go z niezweryfikowaną resztą starej generacji jako ready. Get i save zawsze czytają plik na nowo, także jeśli search zwróciło starą rewizję.

Skan ma budżet kooperacyjny scan_budget_s i wszystkie limity rozmiaru/liczby. search ma search_budget_s. Zimny skan nie blokuje wywołania MCP do czasu zakończenia. Limity I/O nie dają twardego timeoutu dla zablokowanego filesystem; task i status pokazują ograniczenie. Duże bundles przekraczające profile MVP mają partial/error, a nie ukrytą obietnicę obsługi nieograniczonego zbioru. Brak watcherów i maksymalnego czasu wykrycia zmian bez wywołań API. Domyślne 30 s to próg zaplanowania skanu przy kolejnym auto, nie gwarancja wykrycia po 30 s.

Failed scan ma bezpieczny error i backoff 5 s przed kolejnym auto; force może jawnie ponowić. Queue full/panel down -> bezpieczna diagnostyka, żadnego synchronicznego obejścia. Disabled/utrata root -> usuwa indeks, anuluje skan jeśli możliwe; późna publikacja generacji musi ponownie sprawdzić wiązanie i enabled. W trakcie odpowiedzi ponownie sprawdzamy scope/dostępność; nie deklarujemy odporności na wszystkie zewnętrzne TOCTOU.

Po restart wszystkie indeksy są empty. Nie odtwarzamy ich z tasks.db. Nie zapisujemy treści lub klucza rewizji do logs/config. Trwały indeks może zostać zaprojektowany dopiero jako osobna decyzja po C4/P4.

## 8. Etap projektowy — zarezerwowany zakres P1-P4

Nie implementujemy teraz scope=project, audit, convert ani skilla. Ich detale są osobnym kontraktem przed P1/P2; pola centralnego API mają umożliwić rozszerzenie bez nowych reguł uprawnień.

Uzgodnione kierunki:
- bundle projektu wskazuje docs/ zatwierdzonego roota, bez konkurencyjnych kopii;
- domyślne all obejmuje bieżący, enabled projekt plus centralne bundles; jawny wybór innego projektu nie skanuje wszystkich roots;
- archiwum domyślnie poza search, ale w raporcie zgodności całego drzewa. Wykluczenia nie czynią całego docs zgodnym OKF;
- przejściowo zwykły Markdown jest format=markdown/unconverted, z null/inferred metadanymi jawnie oznaczonymi;
- project role jest lokalnym rozszerzeniem sklr; plan nie oznacza wykonania, accepted ADR nie jest human-reviewed;
- CURRENT wymaga jawnego selektora bieżącej sekcji; nie wybieramy historycznej sekcji na podstawie samego słowa Stan;
- knowledge_audit jest odczytowy, knowledge_convert przygotowuje paczkę do panelu na podstawie jawnych rewizji i metadanych;
- konwersja zachowuje body byte-for-byte, istniejące rozszerzenia, komentarze i niezmieniane fragmenty YAML. Gdy nie umiemy wykonać bezpiecznej wąskiej edycji, raportujemy wyjątek. CP1250->UTF-8 wymaga jawnej osobnej zgody;
- okf-curator powstaje w dev-skills po ustaleniu P2, z pomocą skill-writer, tests przy skillu i ręczną promocją. Nie używa skryptu omijającego knowledge_convert i zgody;
- reserved index/log, upstream SPEC, przykłady i archiwa wymagają świadomego wyboru, nie masowego dodawania type;
- generated.at nie jest datą konwersji, verified nie powstaje z walidacji YAML ani zatwierdzenia zapisu.

## 9. Odbiór i pierwsza paczka kodu

C0 obejmuje zapis i odczyt ADR/spec (#153 done/exit0), akceptację w rozmowie oraz zatwierdzoną aktualizację planu/CURRENT/backlog. Stan tej ostatniej aktualizacji trzeba sprawdzić odczytem po decyzji w panelu. Sam status zapisu dokumentacji nie oznacza wykonania C1.

Ustalony podział C1:

### C1a — bezpieczny dokument i rewizja

Zakres orientacyjny: okf_engine/__init__.py, models.py, parser.py, public_view.py, revisions.py oraz tests/test_okf_parser.py, test_okf_public_view.py, test_okf_revisions.py. Dodatkowy mały moduł helperów jest dopuszczalny po przedstawieniu zakresu, nie jako pretekst do integracji całego serwera.

- Dane wejściowe: ograniczone bajty UTF-8 i jawna tożsamość dokumentu. Bez skanowania dysku, konfiguracji bundles i produkcyjnej bazy wiedzy.
- Dane wyjściowe: prywatny model do walidacji, oddzielny publiczny widok po maskowaniu, bezpieczne warnings/errors oraz opaque revision.
- Maskowanie metadanych preferuje transformację ograniczonego drzewa; maskowanie body musi zachować liczbę i powiązanie linii. Nie wymagamy tekstowej naprawy dowolnego YAML jako warunku startu. Publiczny YAML potrzebny dla diffu może być syntetycznym widokiem z jawną informacją, że komentarze/format nie są kopią źródła; surowy blok nie może zostać użyty przez pomyłkę.
- Klucz rewizji jest zależnością wstrzykiwaną, utworzoną raz przez właściciela serwisu/procesu; nie nowy klucz przy każdym get. W testach wstrzykujemy jawne klucze i sprawdzamy ich rotację.
- Nowe testy mają być samodzielne, bez danych z ~/.agents/okf, skilli zewnętrznych, sieci i instancji main.py. Bez zmian zachowania server/secrets_mask.py dla starych narzędzi.

### C1b — bundles, ścieżki i defaults

Zakres orientacyjny: okf_engine/bundles.py, paths.py i konfiguracja limits (osobny moduł w razie potrzeby), defaults w state.py, tests/test_okf_bundles.py, test_okf_paths.py oraz testy defaults/migracji istniejącego configu.

- Rejestr central-only, przypięte root_path, walidacja overlap i enabled, kontrola ścieżek i przodków, brak nowych dostępów.
- Brak automatycznej migracji roots/config.json i tworzenia ~/.agents/okf. Defaults knowledge mają pustą listę bundles.
- C1b zaczynamy dopiero po odbiorze C1a. Szczegóły integracji istniejących helperów sprawdzamy odczytem kodu na nowym chat.

C1a/C1b nie zawierają skanera, indeksu, section outline, narzędzi MCP, nowych rodzajów tasks ani UI panelu. Integracja odczytu to C2, zapisu/panelu C3. Testy wykonuje użytkownik lub agent dopiero po wyraźnej zgodzie; nie deklarujemy green bez wyników.

Warunki przed C2:
- type-only i unknown fields/types działają;
- verified mapping/list, daty/status/semantic warnings zgodne z kontraktem;
- YAML tags/duplikaty/aliases/limity nie wykonują kodu i nie powodują nieograniczonej ekspansji;
- .md z zagnieżdżonym token/URL/private key daje maskowany publiczny widok bez przesunięcia linii i bez surowych sekretów w cache/errors;
- rewizja zmienia się po edycji i restarcie, nie ujawnia surowego digestu;
- bundle nie wychodzi poza root i nie daje dostępu do skills przez link/alias;
- disabled/niepoprawny rejestr nie zwraca treści.

C2: sections/search/index/refresh oraz trzy narzędzia odczytowe. C3: save, dedykowany maskowany diff i panel. C4: pilot, pomiar trafności i łącznej objętości zwróconego tekstu vs search_code/read_file, test ręcznej zmiany/restartu, odbiór przed projektem.

Nie uruchamiamy pytest/Node/serwera ani instalacji bez wyraźnego polecenia użytkownika. Użytkownik potwierdził commit create_tree oraz zielony pełny pytest, bez logu/liczby testów. Testy serwera sklr działają we własnym venv; nie korzystają ze środowisk conda skilli. Przed C1 pozostaje sprawdzenie commita dokumentacji C0 i aktualności baseline po kolejnych zmianach. Agent nie uruchamiał git ani testów. Nie commitujemy config.json, tasks.db*, logs i danych wiedzy do repo sklr. C0 nie tworzy ~/.agents/okf ani roota i nie wdraża żadnego narzędzia.
