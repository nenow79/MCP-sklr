---
type: Specification
title: "OKF: parser, zaufanie i poufność"
tags: [sklr, okf, parser, security]
---
# OKF: parser, zaufanie i poufność

Kontrakt lokalny sklr dla implementacji w `e6cee29`; odbiór i ograniczenia testów: [CURRENT](../../plan/CURRENT.md).
Mapa i pozostałe kontrakty: [obsługa OKF](../okf-support.md). Standard formatu: [OKF 0.2](../OKF/SPEC.md).

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

### 4.3 Maskowanie OKF

Istniejący server/secrets_mask.py:mask_text działa dla nazw rozpoznanych jako pliki sekretów. Dla ogólnego .md zwykle zwraca tekst bez maskowania. Samo wywołanie tego helpera NIE spełnia kontraktu wiedzy.

Dedykowany okf_engine/public_view.py realizuje wymagania:
1. Prywatny, ograniczony parser/snapshot waliduje format; nie jest przekazywany do telemetrii, SQLite ani MCP.
2. Dedykowana transformacja maskuje tekst niezależnie od rozszerzenia: jawne przypisania sekretów w YAML/JSON/env (password, passwd, token, secret, credential, api_key/private_key i ich jednoznaczne warianty), URL z hasłem oraz bloki kluczy prywatnych. Zakres wzorców i false positives musi być opisany testami. Przykłady kodu też podlegają maskowaniu.
3. Publiczne metadane pochodzą z maskowanego widoku, nie z surowego parsera. Zagnieżdżone pola w rozszerzeniach i sources są również objęte transformacją. Placeholder ma postać cytowanego tekstu w YAML, aby nie psuć struktury. Jeśli nie można bezpiecznie utrzymać struktury, pole jest ukryte z warning.
4. Indeks, ranking, fragmenty, outline i diff używają wyłącznie widoku publicznego. Nie przechowujemy surowych wartości w lookupach ani używanych przez ranking sygnałach. Zamaskowane wartości nie są słowami wyszukiwania; zapytanie o ukrytą wartość nie może trafić po jej surowej treści.
5. Mapa linii maskowania pozwala powiązać fragment z fizycznymi liniami pliku. Zastąpienie multiline sekretu nie może przesunąć sekcji i podać błędnego zakresu. Sekcje wyznaczamy z maskowanego, zachowującego linie body, nie z frontmattera.

Heurystyka nie wykrywa wszystkich sekretów ani danych poufnych. Title/description/body mogą zawierać wrażliwą prozę nierozpoznawalną wzorcem. Zakaz utrwalania sekretów jest również zasadą kuracji; nie deklarujemy pełnego DLP. Ścieżki/ids powinny być niewrażliwe; wykryty jednoznaczny sekret w identyfikatorze blokuje zwrot wpisu z ogólną diagnostyką, zamiast emisji zniekształconego id.


Narzędzia wiedzy są confidential; to ogranicza telemetrię, nie ujawnienie przez MCP/czat. Nie wykonują SQL, skryptów, executors ani attesters i nie pobierają URL. Indeks nie zawiera prywatnych bajtów. Szczegóły zapisów i kolejki: [zapis](write.md). Zwykły projektowy Markdown bez frontmattera ma odrębny tryb migracyjny opisany w [kontrakcie projektu](project-and-conversion.md).
