# OKF: projekt, przygotowanie i konwersja

Kontrakt lokalny sklr dla implementacji w `e6cee29`; odbiór i ograniczenia testów: [CURRENT](../../plan/CURRENT.md).
Mapa i pozostałe kontrakty: [obsługa OKF](../okf-support.md). Standard formatu: [OKF 0.2](../OKF/SPEC.md).


- knowledge_bundles pokazuje dostępne scope=central oraz project. Search all
  wybiera centralne zbiory plus projekt domyślnego roota; scope=project tylko
  ten projekt. Jawny bundle_id może wskazywać inny włączony projekt; niezgodność
  jawnego scope daje SCOPE_MISMATCH. Get/save działają na obu scope.
- Bundle project ma profile=docs/legacy/mixed. Docs wymaga niepustego subpath
  (zwykle docs). Legacy/mixed wymagają subpath="" i obejmują dokładnie dwa pliki
  .vibecoder_context.md/.vibecoder_checkpoints.md; mixed dodatkowo docs/.
  Katalogi ukryte, archiwum i AGENTS są poza indeksem projektu. Nadal obowiązuje
  fizyczna kontrola overlap bundles/skills oraz przypięte root_path.
- Projektowy Markdown bez frontmattera jest czytelny/wyszukiwalny z format=markdown,
  type=unknown, metadata={}, status=null i warning unconverted. Brak automatycznego
  zapisu metadanych. Błędny istniejący YAML pozostaje błędem, nie jest ignorowany.
- Panel /api/knowledge używa dotychczasowej sesji/Host/Origin. Operacje konfiguracji
  wymagają aktualnej opaque revision konfiguracji; root_path jest przypinane przez
  serwer, nie nadawane przez LLM. Remove usuwa tylko wpis, nie pliki. Refresh jest
  zadaniem odczytowym istniejącej kolejki. Domyślnie nadal brak nowych bundles/roots.
- prepare_project(root=null, subpath="", max_chars=12000, create_agents=false)
  dostarcza root/nested AGENTS, rewizje, audyt, kandydatów stanu i propozycję
  brakującego AGENTS. Nie zgaduje aktualnego checkpointu. Flagi truncated i
  instructions_complete wymagają doczytania brakującej treści. create_agents=true
  przygotowuje tylko brakujący plik z szablonu, zawsze jako task do zgody.
- knowledge_audit(root=null, include_archive=true, max_files=500,
  max_total_chars=12000) bada docs/ i dwa root legacy pliki. Zwraca metadane stanu,
  revision, convertible i propozycję type: Reference dla dokumentów bez type.
  Nie zwraca surowych treści. Reserved index/log i AGENTS nie są konwertowane.
- knowledge_convert(changes, root=null, description="") przyjmuje do 10 rekordów
  filepath/expected_revision/metadata_yaml. Rewizja pochodzi z audytu, jest związana
  z root/path i kluczem ProjectTools w pamięci procesu. Samego body nie zmieniamy.
  Dopisujemy brakujące top-level pola frontmattera, zachowując oryginalny YAML,
  komentarze, BOM i EOL. Podana istniejąca wartość musi być równa starej; zmiana
  wymaga osobnego knowledge_save/edycji, nie ukrytej konwersji. Błędny YAML,
  mixed EOL, CP1250 i kolizje wymagają osobnej decyzji. Brak automatycznego autorstwa,
  generated, verified, dat, reorganizacji historii i transkodowania.
- Zapis setup/convert: sensitive, bez Ufaj, jedna zgoda, natychmiastowy task_id,
  prywatne PreparedChanges, guard pod WRITE_LOCK -> ConfigStore.lock i publiczny
  maskowany podgląd. Payload/stdout nie zawierają raw/body/YAML/hashów/rewizji.
  Committed, rollback i cleanup mają oddzielne znaczenie; indeks jest unieważniany
  i odświeżany po zapisie. Restart nie odtwarza prywatnego planu.
- Polityka przygotowania i migracji to advisory: instructions inicjalizacyjne,
  opisy narzędzi i prepare_project prowadzą LLM, ale nie blokują istniejących
  narzędzi przy braku AGENTS/OKF. Hard guard gotowości projektu nie jest wdrożony.
  Nie deklarujemy dowodu przeczytania instrukcji lub zgodności całego repo.

Wyniki i pozostałe testy Windows: docs/plan/CURRENT.md, docs/windows-okf.md.

## Granice zakresu

Profil docs może wskazywać dowolny istniejący podkatalog, np. doc/, do indeksowania i odczytu. Audyt/konwerter obecnie rozpoznają wyłącznie docs/ i dwa pliki legacy w korzeniu; nie obsługują jeszcze dowolnego subpath. Profil mixed oznacza docs/ plus te dwa pliki. Brak AGENTS lub konwersji nie blokuje pracy. Archiwum pozostaje poza indeksem projektu, choć audyt domyślnie je raportuje.

Formalna konwersja nie porządkuje historii, nie dzieli plików ani nie ustala, które twierdzenia są aktualne. Wymaga osobnego świadomego wyboru zakresu; reserved index/log, upstream SPEC, przykłady i archiwa nie są kandydatami do masowego dopisywania type. Rewizje konwersji pochodzą z audytu ProjectTools, nie z knowledge_get.

## Propozycje, których nie wdrożono

Semantyczny selektor aktualnego stanu, project role, hard guard gotowości projektu i osobny skill okf-curator wymagają osobnego kontraktu. Skill miałby powstawać w dev-skills z pomocą skill-writer, mieć własne testy i ręczną promocję; nie może omijać knowledge_convert ani zgód. Wykluczenie archiwum z indeksu nie oznacza zgodności całego docs/ z OKF. Zadania i priorytety są wyłącznie w [backlogu](../../plan/backlog.md).
