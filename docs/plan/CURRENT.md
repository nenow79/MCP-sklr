# Aktualny stan — 2026-10-15

Punkt odniesienia: HEAD `e6cee29` — `feat: complete OKF knowledge workflow`. Przed porządkami dokumentacji drzewo było czyste. Development: Linux; docelowy runtime: Windows.

## Dostępne

- Panel Wiedza; knowledge_bundles/search/get/save dla centralnych i projektowych bundles.
- Profile projektu docs/legacy/mixed, odczyt i wyszukiwanie zwykłego Markdown przed konwersją; prepare_project, knowledge_audit i knowledge_convert.
- Zapisy po zgodzie w panelu, maskowane podglądy, rewizje i kontrola konfliktów. AGENTS i migracja są advisory: ich brak nie blokuje pracy.
- Indeks sekcji jest odtwarzany w pamięci; nie powstaje index.md na dysku. Pliki źródłowe pozostają źródłem prawdy.

## Ograniczenia i odbiór

Konwerter dodaje brakujące pola frontmattera, zachowując body; nie reorganizuje historii. Audyt/konwersja obejmują docs/ i dwa root legacy pliki. Dowolny inny podkatalog, np. doc/, można indeksować profilem docs, lecz konwerter go jeszcze nie obsługuje. Archiwum nie trafia do indeksu projektu; audyt domyślnie je raportuje.

Użytkownik potwierdził ręcznie dodanie wiedzy centralnej. Bundle projektu `sklr-okf` pokazał **partial i 3 dokumenty**; przyczyna pozostaje nieustalona. To nie jest pełny odbiór pilota ani dowód kompletności indeksu. Pełny odbiór Windows i pomiar kontekstu pozostają otwarte.

Hard guard gotowości projektu, semantyczny selektor stanu i skill okf-curator nie są wdrożone. Maskowanie jest heurystyczne; transakcje mają best-effort rollback, bez pełnej atomowości przy awarii systemu. Szczegóły ochrony: [kontrakty OKF](../specs/okf-support.md).

## Potwierdzone sprawdzenia

Wyniki użytkownika dotyczą wcześniejszych etapów, nie pełnego bieżącego HEAD:

| Zakres | Potwierdzenie |
|---|---|
| C2c: service/index/read tools/rejestracja | 79 passed |
| C3a: writer/preview/parser/public_view | 208 passed i 1 błąd testu; po poprawce wyłącznie preview: 21 passed |
| C3b: paczka zapisu | 294 passed, 26 failed, 1 skipped; po poprawce stagingu wyłącznie file_transaction + knowledge_write_tools: 86 passed, 1 skipped (junction Windows) |

Nie potwierdzono ponowienia pełnych paczek C3a/C3b ani pełnego pytest po najnowszych rozszerzeniach panel/projekty/migracja. Poprzednia sesja raportowała dla rozszerzeń kontrolę AST 35 plików Python, rejestracji 27 narzędzi (10 confidential), importów i zgodności HTML/JS; nie uruchamiała pytest, Node ani serwera. Starsze wyniki, pełne komendy i ich ograniczenia zachowano w [historii stanu](../archive/pre-okf-2026-10-15/plan/CURRENT.md).

Porządki dokumentacji w tej sesji: bez zmian kodu, konfiguracji i danych; bez konwersji OKF i uruchamiania testów runtime. Kontrola dokumentacji: 143 odnośniki nawigacyjne bez błędów, zachowanie treści archiwum i standardu oraz git diff --check (także nowe pliki) poprawne.

## Następny krok

Wyjaśnić `partial`/3 dokumenty na podstawie reasons, diagnostyki i zakresu bundle, następnie wykonać odbiór na Windows według [instrukcji](../windows-okf.md). Dopiero po świadomym wyborze zakresu osobno zdecydować o konwersji uporządkowanych źródeł. Jedyna lista otwartych prac: [backlog](backlog.md). Do bieżącej pracy wystarczą AGENTS, ten plik i właściwy temat z [mapy dokumentacji](../README.md).
