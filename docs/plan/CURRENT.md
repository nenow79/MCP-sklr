---
type: Project State
title: Aktualny stan projektu sklr
description: Zakres implementacji, ograniczenia odbioru i potwierdzone sprawdzenia projektu sklr.
tags: [sklr, project-state, testing]
---
# Aktualny stan — 2026-10-18

Baza funkcji OKF: `e6cee29` — `feat: complete OKF knowledge workflow`; porządki dokumentacji: `39cc074`. Bieżące rozszerzenia obejmują automatyczne docs i politykę formatu opisane poniżej. Development: Linux; docelowy runtime: Windows.

## Dostępne

- Panel Wiedza; knowledge_bundles/search/get/save dla centralnych i projektowych bundles.
- Automatyczne <root>/docs przy starcie serwisu i przed użyciem wiedzy/panelu; bez trwałego wpisu bundle, konwersji i watcherów. Ręczne wiązania mają pierwszeństwo. Panel pozwala wyłączyć automatykę dla ścieżki projektu; usunięcie roota/docs usuwa źródło i indeks przy następnym użyciu wiedzy.
- Profile projektu docs/legacy/mixed, odczyt i wyszukiwanie zwykłego Markdown przed konwersją; prepare_project, knowledge_audit i knowledge_convert.
- Zapisy po zgodzie w panelu, maskowane podglądy, rewizje i kontrola konfliktów. AGENTS i migracja są advisory: ich brak nie blokuje pracy.
- Polityka formatu dokumentacji: domyślny compatible pozwala na stary Markdown, a poprawny OKF jest chroniony przed degradacją. Panel umożliwia jawny require_okf z zakresem i wyjątkami, przypięty do ścieżki projektu; konwersja nie zmienia trybu. Wspólna walidacja create/edit/exact/tree działa przed zgłoszeniem i po zgodzie. Rename przecinający zakres require_okf jest blokowany. Kontrakt: [polityka zapisu](../specs/okf-integration/write-policy.md).
- Lokalny podpisany cache SQLite → RAM, ładowany przy starcie przed uzgodnieniem źródeł w tle. knowledge_index.sqlite3 i knowledge_cache.key obok tasks.db, poza Git. Klucz oddzielny od rewizji API: DPAPI CurrentUser na Windows, 0600 na POSIX; baza nie jest szyfrowana. Schemat v2, limity cache i HMAC źródła/payloadu. Nie powstaje index.md; źródłem prawdy pozostają pliki.
- Cache przy starcie ma cache_unchecked i pustą rewizję. Search świeżo odczytuje kandydatów przed zwrotem sekcji/rewizji, ponownie sprawdza dopasowanie i dostęp; wynik jest jawnie partial do uzgodnienia zbioru. Get/save czytają źródła. Nowe dopasowania poza starym cache mogą być niewidoczne do odświeżenia.
- Knowledge_save, konwersja i zwykłe create_file/edit_file po committed zgłaszają konkretne dokumenty do aktualizacji SQLite/RAM. Zaległe cele są scalane, stare cele usuwane z RAM na czas aktualizacji, reszta indeksu pozostaje dostępna. Tickets/anulowanie chronią przed starszą publikacją; niepewny rollback zachowuje ostrożną invalidację. Starszy pełny scan może zostać przerwany z reconcile_required.
- Dedykowany worker wiedzy, dokumenty przetwarzane partiami. Pełna inventory nadal ograniczona 90 s; dla backendu osobny kooperacyjny budżet publikacji 120 s od zakończenia odczytu. Timeout publikacji odrzuca całą niedokończoną generację i zachowuje poprzednią RAM ze stanem error. Przy niezmienionej konfiguracji kontrole dokumentów oceniają tylko wybrany zbiór, z zachowaniem guardów. Panel i zadania pokazują czasy/liczniki kontroli i bezpieczne kody błędów.

## Ograniczenia i odbiór

Konwerter dodaje brakujące pola frontmattera, zachowując body; nie reorganizuje historii. Audyt/konwersja obejmują docs/ i dwa root legacy pliki. Dowolny inny podkatalog, np. doc/, można indeksować profilem docs, lecz konwerter go jeszcze nie obsługuje. Archiwum nie trafia do indeksu projektu; audyt domyślnie je raportuje.

Baza kodu cache: commit 63dd542; szybki start i przyrostowe knowledge_save/convert: cabaec4. Zwykłe create/edit podłączono w task 417 i odebrano testowo po restarcie. Potwierdzono start z 35 wpisami RAM podczas refreshing/cache_unchecked, potem ready/35. W zbiorze centralnym ready/2. W projekcie obecnie ready/36: dodatkowy docs/incremental-smoke-test.md pozostaje nieśledzonym plikiem lokalnym, nie kontraktem i nie częścią commita.

Ręczny smoke: create przez knowledge_save (#404/#405) dodał 1 dokument i zachował 35; update v1→v2 (#406/#407) oraz edit_file v2→v3 (#420) przetworzyły po 1 dokumencie, zachowując 36 wpisów. Cached search potwierdził nowe znaczniki i brak starych, bez nowego skanu. Aktualizacje trwały około 5 s. Pełne skany zależnie od przebiegu trwały od około 0,6 s do ponad 2 minut; przyczyny tej zmienności nie ustalono. Wcześniejsze pomiary wskazały dominację ocen zbiorów i rozwiązywania ścieżek; optymalizacja nie jest gwarancją stałej latencji.

Create_tree/rename nie są podłączone do przyrostowego indeksu. Nie ma okresowej lekkiej inventory/watchera dla zmian z IDE/git pull; obecnie uzgodnienie następuje po starcie, ręcznym refresh lub auto search według refresh_after_s. Pełny refresh nadal odczytuje wszystkie pliki (cache pomija parser); nie usuwa niewidzianych wpisów SQLite. Purge API istnieje, ale nie jest podłączone do usunięcia zbioru. Brak osobnego przycisku Przebuduj, staging-generation SQLite i kompletnego odbioru retencji/ACL bazy. Ręczny smoke nie objął edycji podczas aktywnego pełnego skanu, bo ten zakończył się przed edycją. Kontrakt i dalszy cel: [trwały indeks](../specs/okf-integration/persistent-index.md).

Konwersja wybranego aktywnego zakresu docs do OKF: zapisano metadane 22 dokumentów w taskach 339, 341, 343–345; 3 wcześniej poprawne dokumenty pozostawiono bez zmian. Archiwum, standard upstream i przykłady nie były konwertowane. Treść nie była reorganizowana; kompletność audytu nadal ogranicza audit_budget. Nie włączano require_okf.

Hard guard gotowości projektu, semantyczny selektor stanu i skill okf-curator nie są wdrożone. Maskowanie jest heurystyczne; transakcje mają best-effort rollback, bez pełnej atomowości przy awarii systemu. Szczegóły ochrony: [kontrakty OKF](../specs/okf-support.md).

## Potwierdzone sprawdzenia

Bieżące paczki uruchomił użytkownik na Windows (agent nie uruchamiał pytest ani serwera):

- Szybki start: cache_restore/backend/bootstrap/index/search/service/knowledge_tools/project_knowledge — 182 passed, 1 skipped (symlink); ręczny start z cache i uzgodnienie ready potwierdzone.
- Przyrostowe knowledge_save/convert: knowledge_write_tools/project_knowledge/service/index/cache_backend/knowledge_tools — 172 passed, 2 skipped. Podstawa patch RAM: 142 passed. Dodawanie i edycja knowledge_save potwierdzone smoke.
- Zwykłe create/edit: edit_tools/file_transaction/project_knowledge/service/knowledge_write_tools/cache_backend — 234 passed, 3 skipped. Ręcznie odebrano edit_file; create_file ma testy, ale jeszcze nie osobny ręczny smoke.
- Cache hit/store/key/pipeline/worker/adapter/documents: 83 passed, 3 skipped; pominięcia symlinków i polityki POSIX nie są testami zaliczonymi na Windows.

Nie wykonano ani nie potwierdzono pełnego pytest bieżącego HEAD. Wyniki paczek nie oznaczają pełnego odbioru platformy ani wszystkich junctionów. Poniżej zachowano wcześniejsze ograniczenia potwierdzeń.

Wyniki użytkownika dotyczą wcześniejszych etapów, nie pełnego bieżącego HEAD:

| Zakres | Potwierdzenie |
|---|---|
| C2c: service/index/read tools/rejestracja | 79 passed |
| C3a: writer/preview/parser/public_view | 208 passed i 1 błąd testu; po poprawce wyłącznie preview: 21 passed |
| C3b: paczka zapisu | 294 passed, 26 failed, 1 skipped; po poprawce stagingu wyłącznie file_transaction + knowledge_write_tools: 86 passed, 1 skipped (junction Windows) |

Nie potwierdzono ponowienia pełnych paczek C3a/C3b ani pełnego pytest po najnowszych rozszerzeniach panel/projekty/migracja. Poprzednia sesja raportowała dla rozszerzeń kontrolę AST 35 plików Python, rejestracji 27 narzędzi (10 confidential), importów i zgodności HTML/JS; nie uruchamiała pytest, Node ani serwera. Starsze wyniki, pełne komendy i ich ograniczenia zachowano w [historii stanu](../archive/pre-okf-2026-10-15/plan/CURRENT.md).

Poprzednie porządki dokumentacji: bez zmian kodu, konfiguracji i danych; bez konwersji OKF i uruchamiania testów runtime. Kontrola dokumentacji: 143 odnośniki nawigacyjne bez błędów, zachowanie treści archiwum i standardu oraz git diff --check (także nowe pliki) poprawne.

Rozszerzenie automatycznych docs: AST zmienionych plików Python i `node --check panel/static/app.js` poprawne. Przed dodaniem polityki formatu użytkownik potwierdził zielony wynik paczki test_okf_discovery, test_project_knowledge, test_knowledge_tools, test_okf_service i test_panel_static po poprawce oczekiwanego typu wyjątku w teście odłączenia roota (KnowledgeError). Ręczny odbiór panelu i odbiór Windows pozostają otwarte. Agent nie uruchamiał pytest ani serwera.

Polityka formatu: AST 13 zmienionych/nowych plików Python, importy, `node --check panel/static/app.js` oraz zgodność identyfikatorów HTML/JS poprawne. Paczka użytkownika test_okf_write_policy, test_project_knowledge, test_file_changes, test_file_transaction, test_edit_tools, test_tree_tools, test_folder_tools i test_panel_static dała 331 passed, 1 błąd testu autoryzacji i 2 skipped (junction i case Windows). Po poprawce oczekiwania dla braku Origin użytkownik potwierdził zielony wynik ponowionego test_project_knowledge. Ręczny odbiór i scenariusze Windows pozostają otwarte. Agent nie uruchamiał pytest ani serwera. Wewnętrzne ścieżki rename/create_tree przekazywane względem przypiętego roota, zgodnie z guardem Windows także na POSIX.

## Następny krok

Po przerwie: podłączyć create_tree do zgłaszania committed konkretnych ścieżek, potem zaprojektować lekkie uzgodnienie zmian zewnętrznych (stat jako filtr, bez pełnego odczytu niezmienionych źródeł). Zachować dostępność RAM i ochronę przed spóźnioną publikacją. Osobno domknąć rename/usunięcia/retencję, ręczny smoke współbieżności oraz pełne regresje Windows. Smoke-test v3 pozostaje lokalnie do jawnego usunięcia; nie dodawać do Git. Jedyna lista prac: [backlog](backlog.md).
