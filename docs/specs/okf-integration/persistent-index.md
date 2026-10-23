---
type: Specification
title: "OKF: trwały cache i indeksowanie przyrostowe"
description: Docelowy kontrakt SQLite → RAM, aktualizacji po zapisach i kontroli zmian zewnętrznych.
tags: [sklr, okf, index, sqlite]
---
# OKF: trwały cache i indeksowanie przyrostowe

## Status i zakres

Kierunek zaakceptowany przez użytkownika: lokalny cache SQLite, RAM do wyszukiwania, dedykowany worker i aktualizacje zmienionych dokumentów. Wdrożone: CacheStore v2, cache_key/hit/restore/backend, start SQLite → RAM i przyrostowe knowledge_save/convert/create_file/edit_file. Sekcje poniżej zawierają również wymagania docelowe; nie są deklaracją ukończenia wszystkich rozszerzeń.

Niewdrożone: create_tree/rename hooks, okresowa lekka kontrola zmian zewnętrznych, purge przy usunięciu zbioru, staging-generation SQLite i osobna akcja Przebuduj. Surowe sekcje nie są osobno przechowywane w DB: outline odtwarzany z publicznego body. Retencja/ACL bazy i pełne testy platformowe pozostają otwarte. Aktualny stan i wyniki testów pozostają w [CURRENT](../../plan/CURRENT.md), zadania wyłącznie w [backlogu](../../plan/backlog.md).

W chwili przygotowania kontraktu indeks jest nadal wyłącznie w RAM. Ostatni sprawdzony wynik zbioru sklr-okf: 26 dokumentów, 9 pominiętych, scan_budget i refresh_budget; task 351 trwał około 122 s. Zwiększenie budżetu nie rozwiązało kosztu pełnych skanów. Dotychczasową implementację opisuje [indeks i odświeżanie](index-and-refresh.md).

## Źródło prawdy i magazyny

Pliki źródłowe pozostają nadrzędne. SQLite jest odtwarzalnym cache, nie drugim autorskim magazynem wiedzy; nie tworzymy docs/knowledge/ ani index.md.

Jedna lokalna baza knowledge_index.sqlite3, oddzielna od tasks.db, obecnie obok tasks.db w katalogu sklr, ze ścieżką bootstrapu względem main.py (nie cwd). Klucz knowledge_cache.key jest osobnym plikiem. Nie uruchamiać zbioru wiedzy obejmującego pliki danych runtime; parser przyjmuje jedynie dokumenty Markdown. Baza oraz pliki pomocnicze SQLite nie trafiają do Git ani do dystrybucji między systemami. Biblioteka sqlite3 ze standardowego Pythona; FTS, embeddingi i zmiana rankingu nie należą do pierwszej wersji.

RAM przechowuje przygotowaną generację wyszukiwarki z zachowaniem istniejących terms/phrase/browse i wag. Ładowanie z SQLite nie oznacza potwierdzenia aktualności wobec filesystem.

## Zawartość cache i poufność

Przechowujemy identyfikatory i wiązania zbiorów, publiczną treść po maskowaniu, dozwolone metadane, sekcje, informacje o pliku do wykrywania zmian, wersje schematu/parsera/maskowania i stan generacji. Wszystkie deserializowane rekordy podlegają walidacji i limitom rozmiaru; cache nie jest zaufaną instrukcją ani dowodem weryfikacji.

Nie utrwalamy surowego frontmattera/body, zapytań użytkownika, klucza RevisionSigner, zgód dostępu ani starych rewizji API do ponownego użycia. Prywatny fingerprint zawartości nie jest publiczną rewizją ani polem rankingu. Fingerprint źródła i tag publicznego payloadu to domenowo oddzielone HMAC-SHA256 z osobnym trwałym kluczem cache, nigdy kluczem RevisionSigner. Auth_tag obejmuje identity, binding, wersje, digest i publiczny payload; powstaje wyłącznie po kontrolowanym parsowaniu. Migracja schematu v1→v2 pozostawia stare wpisy bez tagu, wymagające parsowania. Klucz: DPAPI CurrentUser na Windows, właściciel/0600 na POSIX; brak bazy-klucza wymaga świadomej odbudowy, bez cichej regeneracji.

Publiczny widok nie oznacza danych niepoufnych: maskowanie jest heurystyczne, nie DLP. Baza ma dostęp ograniczony do lokalnego użytkownika w granicach platformy; nie deklarujemy szyfrowania at rest. Treść i sekrety nie trafiają do telemetrii ani stdout zadań. Kopie, WAL i backupy mogą zachowywać usunięte dane; zwykły DELETE nie gwarantuje ich bezpiecznego wymazania. Politykę retencji i czyszczenia należy określić przed produkcyjnym włączeniem cache.

## Dedykowany worker i spójność

Jeden zarządzany worker wiedzy, niezależny od workerów zapisów i skryptów, przetwarza ograniczone partie. Stan inwentaryzacji i pending jest zachowywany między partiami; limit partii nie kończy całego przebiegu ani nie uruchamia go od pierwszego pliku. Kolejka jest ograniczona, deduplikuje ścieżki i łączy szybkie zmiany; przeciążenie oznacza dirty/reconcile_required, nigdy ciche zgubienie aktualizacji.

Anulowanie, shutdown i zmiana wiązania/config-version unieważniają przebieg. Każdy opóźniony wynik ma ticket/version i musi zostać odrzucony po nowszej zmianie. Czynności filesystem odbywają się poza długimi blokadami konfiguracji. Budżety I/O są kooperacyjne, nie stanowią twardego przerwania zablokowanej operacji.

Dokument, jego metadane i sekcje zmieniamy w jednej transakcji SQLite. Dopiero po commit publikujemy spójną generację RAM. Brak pełnej atomowości SQLite + RAM: jeśli publikacja RAM zawiedzie, stan otrzymuje błąd i jest odtwarzany z bazy; nie raportujemy gotowości. Awaria przed commit nie usuwa poprawnej poprzedniej generacji. Pełna przebudowa używa staging-generation i dopiero końcowej podmiany, nie miesza starej i nowej zawartości jako ready.

## Zdarzenia zapisów MCP

Po committed zgłaszamy konkretne zmienione ścieżki, nie pełne odświeżenie zbioru. Integracja obejmuje knowledge_save, konwersję oraz zwykłe create/edit/tree dotyczące dokumentacji. Zgoda Allow nie jest zdarzeniem zmiany; błędny lub odrzucony zapis nie aktualizuje indeksu.

Dodanie i edycja zastępują wyłącznie dotknięty dokument oraz sekcje. Rename katalogu zgłasza stary i nowy zakres. Przy niepewnym rollbacku lub wyniku committed=None planujemy uzgodnienie dotkniętego zakresu. Awaria cache nie cofa udanego zapisu pliku: ustawia zaległą aktualizację i bezpieczny błąd.

Zapis kolejnych zmian podczas przetwarzania nie może zostać skasowany przez starszy wynik. Worker ponownie sprawdza snapshot i dostęp przed commit/publikacją. Usunięcie lub niedostępność wykrytego dokumentu usuwa go z indeksu aktywnego; błąd odczytu nie jest interpretowany jako dowód usunięcia całego zbioru.

## Start, zmiany zewnętrzne i odświeżanie

Start ładuje tylko zgodny cache dostępnych, enabled zbiorów, z jawnym stanem cache_unchecked do czasu uzgodnienia plików. Zmiana roota/subpath/profile lub wersji parsera/maskowania unieważnia cache; nie może odsłonić starego wpisu w nowym zbiorze. Disabled/utrata roota natychmiast blokuje publikację i zwrot wyników oraz usuwa aktywną generację RAM. Fizyczne czyszczenie SQLite jest osobną czynnością retencji.

Przy włączonych sesjach wiedza centralna jest ładowana/odświeżana przy starcie procesu bez przypisania zadania do sesji. Zbiory projektowe są dopuszczane według folderów jawnie wybranej sesji, a ich uzgodnienie zlecają Start i potwierdzenie planu wznowienia. Szczegóły bramek, wersji per zbiór i trybu bez sesji: [sesja i odświeżanie](index-and-refresh.md). Nadal używana jest jedna baza cache; rozdzielenie lifecycle nie tworzy osobnych kopii wiedzy.

Pierwsza wersja wykrywa zewnętrzne edycje, git pull, kopiowanie i usunięcia przez ograniczoną okresową inwentaryzację w tle, także po starcie. Watcher jest późniejszą opcją, nie zależnością. Interwał, backoff i maksymalny zakres wymagają jawnej konfiguracji i diagnostyki; nie obiecujemy natychmiastowego wykrywania.

Rozmiar/mtime/tożsamość pliku są filtrem kandydatów, nie dowodem identycznej treści. Odśwież indeks wymusza sprawdzenie zawartości wszystkich dostępnych źródeł; identyczny dokument może użyć zgodnego cache bez ponownego parsowania. Przebuduj indeks wymusza parsowanie i maskowanie. Niepełna inwentaryzacja nie pozwala uznać niewidzianych plików za usunięte.

## Dostęp, rewizje i API

Cache nigdy nie nadaje dostępu. Przed zwróceniem wyników sprawdzamy aktualny zbiór, wiązanie, root, zakres i ścieżkę z ochroną symlinków/junctionów. Optymalizacja może ograniczyć powtarzane przeliczenia całego rejestru, ale nie cache'ować zgody dostępu jako bezterminowego pozwolenia. Zmiany uprawnień unieważniają generacje i trwające zadania.

Get/save nadal odczytują źródło na nowo. Rewizje API są związane z procesem; restart wymaga świeżej rewizji. Gdy search zwraca sekcję i rewizję z cache odtworzonego po restarcie, musi najpierw wykonać świeży odczyt i powiązać oba pola z tą samą wersją dokumentu. Nie wolno podpisać aktualnego pliku i przypisać tokenu do starszych sekcji. Zmieniony dokument wymaga aktualizacji trafienia lub jego pominięcia z diagnostyką.

Sygnały czasu, stale i trust są ponownie oceniane przy użyciu cache; sama poprawność YAML, restart lub commit SQLite nie nadają verified/generated. Brak auto-fetch URL i wykonywania treści.

## Diagnostyka i odbiór

Panel/API pokazują źródło cache, stan uzgodnienia, postęp, pending, pominięcia, bezpieczne przyczyny błędów i task_id. Pomiary agregowane obejmują inventory/read/parse/mask/access/publish oraz liczbę operacji, bez surowych treści, rewizji i sekretów. State ready nie oznacza nieograniczonej gwarancji świeżości; jest związany z ostatnim ukończonym uzgodnieniem.

Kryteria odbioru: edycja jednego dokumentu parsuje jeden dokument, nie cały zbiór; niezmienione źródła używają cache; restart odtwarza zgodne wpisy; usunięcia i zewnętrzne zmiany są wykrywane; niepełny skan nie kasuje niewidzianych wpisów. Testy obejmują ograniczenia kolejki, restart, anulowanie, spóźniony commit, błąd SQLite/RAM, uszkodzony cache, cofnięcie dostępu, junctiony, zmianę maskowania, rewizje sekcji i brak poufnych danych w logach.

## Etapy dostarczenia

1. Kontrakt i ADR; wybrane miejsce bazy, retencja, schemat i fingerprint.
2. Cache SQLite z walidacją, transakcjami i testami na danych sztucznych.
3. Worker partiami, integracja RAM i diagnostyka kosztów/kontroli dostępu.
4. Zdarzenia committed konkretnych ścieżek i kontrola zmian zewnętrznych.
5. Odbiór Windows, regresje bezpieczeństwa i pomiar zimnego/ciepłego indeksowania.

Ta kolejność nie jest listą ukończonych prac. Bieżące zadania pozostają w backlogu. Nie uruchamiać testów ani serwera bez wyraźnego polecenia użytkownika.
