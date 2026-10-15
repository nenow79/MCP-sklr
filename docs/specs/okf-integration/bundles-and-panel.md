# OKF: bundles, konfiguracja i panel

Kontrakt lokalny sklr (baza `e6cee29`, rozszerzona o automatyczne docs); odbiór i ograniczenia testów: [CURRENT](../../plan/CURRENT.md).
Mapa i pozostałe kontrakty: [obsługa OKF](../okf-support.md). Standard formatu: [OKF 0.2](../OKF/SPEC.md).

## 1. Zakres i architektura

Implementacja dostarcza knowledge_bundles, knowledge_search, knowledge_get, knowledge_save oraz panel Wiedza. Bez auto-fetch URL, wykonywania computation, embeddingów, trwałej bazy indeksu i zapisywania rozmów. Ten sam silnik obsługuje wiedzę projektową i konwersję; zakres opisuje [kontrakt projektu](project-and-conversion.md).

Podział odpowiedzialności:
- okf_engine: modele, parser, bezpieczne ścieżki bundle, publiczny widok po maskowaniu, rewizje, sekcje, skan, indeks, wyszukiwanie i przygotowanie zapisu;
- server/knowledge_tools.py i server/knowledge_write_tools.py: adaptery MCP odczytu i zapisu oraz kolejki/transakcji;
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

Domyślny profil sekcji knowledge (przykład, nie polecenie zmiany lokalnej konfiguracji):

```json
{
  "knowledge": {
    "central_dir": "~/.agents/okf",
    "bundles": [],
    "project_docs_disabled": [],
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

Scope central i project są aktywne zgodnie z [kontraktem projektu](project-and-conversion.md). Dla zatwierdzonych roots wykrywamy wyłącznie bezpośredni `<root>/docs`; nie szukamy rekurencyjnie projektów ani innych katalogów docs. Rejestr ma globalnie unikalne ids, bez reguły project > central.

### Automatyczna dokumentacja projektu

Przy utworzeniu serwisu oraz przed operacjami wiedzy i odczytem panelu powstaje efektywny rejestr: wpisy ręczne z konfiguracji, a następnie wykryte projektowe docs. Wykrywanie sprawdza istnienie ścieżki, bez czytania dokumentów. Automatyczny wpis ma scope=project, profile=docs, subpath=docs i domyślnie enabled=true. Nie zapisujemy go do `knowledge.bundles`, nie tworzymy katalogów i nie konwertujemy plików. Indeksowanie pozostaje odczytowym zadaniem na żądanie.

Id jest wyliczane z przypiętej ścieżki roota; zmiana aliasu zachowuje tożsamość projektu. Kolizja z ręcznym id dostaje deterministyczny alternatywny identyfikator. Ręczne wiązanie z aktualnym root_path ma pierwszeństwo w całym nakładającym się zakresie, również gdy jest wyłączone. Usunięcie ręcznego wpisu może ponownie odsłonić automatyczne docs.

Panel oznacza takie źródła jako automatic i używa klucza `auto:<bundle_id>`. Dostępne operacje to enabled i refresh; nie ma remove automatycznego wpisu. Przełącznik utrwala jedynie listę ścieżek projektów w `knowledge.project_docs_disabled`. Wyłączenie przeżywa restart, zmianę aliasu i czasowe zniknięcie docs. Inny katalog pod dawnym aliasem nie dziedziczy wyłączenia. Niepoprawny typ listy/elementu wstrzymuje automatyczne wykrywanie, zachowując obsługę wpisów ręcznych.

Usunięcie roota lub docs usuwa automatyczny wpis i jego indeks przy następnej operacji wiedzy; przed nią nie ma gwarancji wykrycia zmiany. Ponowne pojawienie się docs przywraca wpis z pustym indeksem. Nadal obowiązują limity, ochrona skills, zakaz symlinków/junctionów, maskowanie, tryby roots i osobna zgoda na zapis. Nie ma watcherów. Przy braku docs zachowana preferencja wyłączenia nie jest wyświetlana jako osobne źródło.

### 3.3 Administracja

Endpointy panelu:
- GET /api/knowledge/bundles — wszystkie wpisy, dostępność, enabled i diagnostyka;
- POST /api/knowledge/bundles — dodanie wpisu po sprawdzeniu root/subpath, bez tworzenia folderów;
- POST /api/knowledge/bundles/{id}/update — label/description/enabled; zmiana ścieżki lub id wymaga usunięcia i ponownego dodania;
- POST /api/knowledge/bundles/{id}/delete — usuwa tylko konfigurację, nigdy pliki;
- POST /api/knowledge/bundles/{id}/refresh — uruchamia odczytowy skan, tylko przy dostępności i enabled.

Obowiązują sesja, Host, Origin i brak CORS jak dla obecnego panelu. Wyłącznie ConfigStore.update, rollback pamięci po błędzie zapisu. Frontend renderuje tekst jako dane, bez innerHTML z zawartości dokumentów. Brak pełnego edytora Markdown i podglądu grafu w MVP.

Root musi istnieć i być zatwierdzony osobno. Włączenie nie przyznaje rw i nie obejmuje innych zbiorów. Wyłączenie blokuje wszystkie operacje OKF dla bundle i usuwa jego indeks. Usunięcie roota/zmiana fizycznej ścieżki unieważnia wiązanie. Zmiana ro -> rw/rw -> ro zmienia bieżące writable i unieważnia przygotowane plany z innymi uprawnieniami; odczyt może działać nadal, jeśli wiązanie jest poprawne.

Każda operacja ponownie sprawdza rejestr i roots. Przed zwrotem wyników cache sprawdzamy aktualną dostępność. To nie jest uniwersalny ACL: zwykłe narzędzia plikowe nadal mają prawa nadane root, a skrypty nie są sandboxowane.


Operacje konfiguracji wymagają aktualnej opaque revision konfiguracji. Remove usuwa tylko wpis, nie pliki. Projektowe profile docs/legacy/mixed opisano w [kontrakcie projektu](project-and-conversion.md).
