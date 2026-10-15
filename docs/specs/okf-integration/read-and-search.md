# OKF: katalog, wyszukiwanie i odczyt

Kontrakt lokalny sklr dla implementacji w `e6cee29`; odbiór i ograniczenia testów: [CURRENT](../../plan/CURRENT.md).
Mapa i pozostałe kontrakty: [obsługa OKF](../okf-support.md). Standard formatu: [OKF 0.2](../OKF/SPEC.md).

## 6. Narzędzia centralne i projektowe — API v1

Wspólny format odpowiedzi: {success: true, data: ...} albo {success: false, error: bezpieczny tekst, data: {code: ...}}. Bez tracebacków, surowych błędów YAML i odbijania wrażliwego query/metadata/body. Przykładowe kody: INVALID_ARGUMENT, BUNDLE_UNAVAILABLE, DOCUMENT_UNAVAILABLE, INVALID_OKF, UNSUPPORTED_PROFILE, SAFETY_LIMIT, REVISION_CONFLICT, RESULT_TOO_LARGE, QUEUE_UNAVAILABLE.

Wszystkie cztery narzędzia rejestrujemy register_confidential_tool. Pole enabled sprawdzane w backendzie, nie tylko w katalogu. Niedostępne/wyłączone wpisy nie ujawniają swoich dokumentów; przy jawnym id wspólny BUNDLE_UNAVAILABLE.

### 6.1 knowledge_bundles()

Bez parametrów, bez automatycznego skanu treści. Zwraca tylko enabled i dostępne bundles, scope=central oraz project:

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

Sygnatura:

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

- scope: all/central/project; all obejmuje centralne zbiory i bieżący projekt. Jawny bundle musi być zgodny ze scope i może wskazywać inny włączony projekt.
- query do 1000 znaków. terms: Unicode NFC + casefold, słowa alfanumeryczne, wszystkie niepuste terms muszą wystąpić w publicznym dokumencie (metadata lub body); brak AND/OR/SQL interpretowanych jako język zapytań. phrase: dosłowne dopasowanie całego query po NFC/casefold w danym polu/sekcji; nie scala granic pól w sztuczną frazę. Bez stemmingu, usuwania polskich diakrytyków i synonimów w v1.
- browse=true dopuszcza puste query; query niepuste z browse=true jest błędem. Browse działa tylko z jawnym bundle_id lub co najmniej jednym filtrem type/tags/status i zawsze ma limit.
- type/status/tags: dopasowanie dokładne po normalizacji/casefold; wszystkie podane tags wymagane. Tags max 20, tag do 100 znaków. Nieznany type jest dozwolony.
- Deprecated pomijane, chyba że include_deprecated=true lub status=deprecated. Draft i stale są dostępne z oznaczeniem; nie podwyższamy zaufania dla stable.
- limit 1-50, max_total_chars 1000-80000. Budżet jest liczony dla JSON results z ensure_ascii=false; JSON zawsze pozostaje poprawny. Najpierw skracamy excerpt z oznaczeniem, potem pomijamy wynik, który nie mieści się z obowiązkowymi ids/metadanymi. Nie przecinamy ids ani rewizji. Nadmierne metadata/snippet mają jawne content_truncated; budżet nie jest obietnicą rozmiaru całej otoczki odpowiedzi ani liczby tokenów.
- refresh: auto/cached/force. Reguły w [kontrakcie indeksu](index-and-refresh.md). Wynik może wymagać odczytowego task bez dodatkowego narzędzia MCP.

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
