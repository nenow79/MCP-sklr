---
type: Specification
title: "OKF: indeks w pamięci i odświeżanie"
tags: [sklr, okf, index, refresh]
---
# OKF: indeks w pamięci i odświeżanie

Kontrakt lokalny sklr dla implementacji w `e6cee29`; odbiór i ograniczenia testów: [CURRENT](../../plan/CURRENT.md).
Mapa i pozostałe kontrakty: [obsługa OKF](../okf-support.md). Standard formatu: [OKF 0.2](../OKF/SPEC.md).

## Status kontraktu

Aktualny runtime używa SQLite jako podpisanego publicznego cache i RAM jako indeksu wyszukiwania. [Trwały indeks](persistent-index.md) rozróżnia wdrożony zakres od docelowych rozszerzeń. Po starcie cache ładowany jest bez odczytu źródeł jako cache_unchecked; pełne uzgodnienie planowane jest w tle. Użytkownik może pracować bez czekania na pełny skan, z ograniczeniami świeżości wyników opisanymi poniżej.

Dokumenty przetwarza dedykowany worker z kontynuacją między partiami; TaskManager pozostaje uchwytem oczekiwania/anulowania. Inventory pełnego refresh nadal używa ograniczonego skanu 90 s. Dla backendu cache publikacja ma osobny kooperacyjny budżet 120 s od zakończenia odczytu; jego wyczerpanie odrzuca niedokończoną generację i zachowuje poprzednią RAM ze stanem error. Stara ścieżka bez backendu zachowuje 120 s łącznie. Nie jest to twardy timeout blokującego I/O. Pomiary i wyniki odbioru są w CURRENT.

## 7. Skan, cache i odświeżanie

Indeks powstaje tylko z enabled/dostępnych bundles, wyłącznie z publicznego widoku. Skany pomijają symlinki/junctions i kontrolują wszystkie przodki katalogu oraz finalne ścieżki, nie tylko wynik scandir. HMAC/private digest może służyć wyłącznie kontroli rewizji, nie rankingowi. Snapshot/parse raw nie jest trwałym cache wiedzy.

C2 uruchamia pełne odświeżanie asynchronicznie przez TaskManager, kind=knowledge_refresh, needs_approval=false: użytkownik włączył odczyt bundle lub zatwierdził root z automatycznie podpinanym docs. Zadanie nie uruchamia skryptu ani sieci. Do payload/stdout trafiają wyłącznie bezpieczne metadane stanu/liczniki. Query i znalezione treści nie są przechowywane w zadaniu. Wyniki wyszukiwania pobieramy ponownym knowledge_search po zakończeniu skanu, nie z trwałego stdout.

Reguły refresh w search:
- auto: dla empty lub skanu starszego niż refresh_after_s planuje odświeżanie (najwyżej jedno na bundle). Może zwrócić poprzedni publiczny indeks z indexed_at/refreshing. Jeśli brak indeksu, results=[], partial=true i task_id; agent czeka przez istniejące get_task/wait_task i ponawia search.
- cached: nie uruchamia skanu, jawnie pokazuje wiek/state i empty/partial. Nie omija kontroli dostępności.
- force: uruchamia lub dołącza do trwającego skanu; wynik oznacza refresh requested i nie jest deklarowany jako świeży. Bez pętli skanów dla tego samego bundle.

Po kompletnym skanie publikujemy nową generację atomowo w pamięci. Skasowane/ręcznie zmienione pliki znikają/zmieniają się w nowej generacji. Po niepełnym skanie można opublikować wyłącznie zweryfikowany podzbiór jako partial z powodem; nie mieszamy go z niezweryfikowaną resztą starej generacji jako ready. Get i save zawsze czytają plik na nowo, także jeśli search zwróciło starą rewizję.

Skan ma budżet kooperacyjny scan_budget_s i wszystkie limity rozmiaru/liczby. search ma search_budget_s. Zimny skan nie blokuje wywołania MCP do czasu zakończenia. Limity I/O nie dają twardego timeoutu dla zablokowanego filesystem; task i status pokazują ograniczenie. Duże bundles przekraczające profile MVP mają partial/error, a nie ukrytą obietnicę obsługi nieograniczonego zbioru. Brak watcherów i maksymalnego czasu wykrycia zmian bez wywołań API. Domyślne 30 s to próg zaplanowania skanu przy kolejnym auto, nie gwarancja wykrycia po 30 s.

Failed scan ma bezpieczny error i backoff 5 s przed kolejnym auto; force może jawnie ponowić. Queue full/panel down -> bezpieczna diagnostyka, żadnego synchronicznego obejścia. Disabled/utrata root -> usuwa indeks, anuluje skan jeśli możliwe; późna publikacja generacji musi ponownie sprawdzić wiązanie i enabled. W trakcie odpowiedzi ponownie sprawdzamy scope/dostępność; nie deklarujemy odporności na wszystkie zewnętrzne TOCTOU.

Po restarcie zgodne, podpisane rekordy dostępnych zbiorów są odtwarzane z knowledge_index.sqlite3 do RAM z pustą rewizją i cache_unchecked, nie z tasks.db. Search odczytuje tylko kandydatów z niezweryfikowanego cache przed zwrotem i ponownie ocenia dopasowanie, filtry i sekcję; nie przypisuje świeżej rewizji do starego excerpt. Cały wynik może być partial, bo nowe dopasowania lub ranking poza starym cache nie są jeszcze znane. Get/save nadal czytają źródło.

Po committed knowledge_save/convert/create_file/edit_file zgłaszają tylko dotknięte dokumenty: stare cele są czasowo usuwane z RAM, reszta IndexEntry pozostaje; backend.update nie wykonuje inventory, patch nie zmienia indexed_at pełnego uzgodnienia. Cele zaległych aktualizacji są scalane, starszy refresh anulowany. Przerwane pełne uzgodnienie może pozostawić reconcile_required. Niepewny rollback zachowuje ostrożną invalidację. Create_tree/rename i okresowa kontrola zmian zewnętrznych jeszcze nie są podłączone.

Zachowanie auto/cached/force opisane wyżej nadal obowiązuje: auto może planować pełny skan, a przy zaległych celach priorytetowo aktualizację przyrostową. Nie ma watchera ani gwarancji wykrycia po 30 s. Pełne odświeżenie czyta źródła nawet przy cache-hit (pomija parser) i nie purguje niewidzianych wpisów SQLite. Nie zapisujemy zapytań, treści ani klucza rewizji do logs/config; SQLite przechowuje wyłącznie publiczny payload i osobne HMAC.


Nie powstaje index.md na dysku. Reserved index.md/log.md należą do standardu, lecz nie są wymaganym produktem indeksowania sklr. Wynik partial wymaga odczytania reasons i diagnostyki; nie dowodzi pełnego przeszukania zbioru.
