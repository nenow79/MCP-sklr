# ADR 0004: Wiedza centralna i projektowa w OKF

Status: zaakceptowany. Pierwotny kontrakt C0 przyjęto po zapisie #153 i odczycie. Poniższe decyzje zachowują uzasadnienia z C0; stan wdrożenia i testów opisuje [CURRENT](../plan/CURRENT.md).

Aktualizacja 2026-10-15: po odbiorze C3b użytkownik autoryzował domknięcie przepływu. Panel i minimalne P0/P1/P2 wdrożono w `e6cee29` przed zakończeniem pilota C4; pierwotna kolejność central → projekt nie jest już blokadą implementacji. Brak AGENTS/konwersji pozostaje advisory. Konwerter nie porządkuje historii, a indeks jest w pamięci. Ręczne dodanie wiedzy centralnej potwierdzono; partial/3 dokumenty projektu nie są pełnym odbiorem. Hard guard, selektor semantyczny i skill kuratora nadal są propozycjami.

Numer 0003 pozostawiamy dla decyzji przewidzianej w odłożonej fazie 5e. Ten dokument nie zastępuje tamtej decyzji.

## Kontekst w chwili decyzji C0

Sklr ma już skills, roots, kontrolowane zapisy, kolejkę zadań i panel zatwierdzeń. Brakuje trwałej, przenośnej wiedzy, którą agent może odnajdywać bez czytania wielu pełnych dokumentów.

Standard OKF 0.2 (docs/specs/OKF/SPEC.md) definiuje pliki Markdown z YAML frontmatter, koncepty identyfikowane ścieżkami, źródła, weryfikację i aktualność. Nie definiuje wyszukiwarki ani bezpiecznego runtime. Dokumentacja projektowa już zawiera wiedzę; tworzenie jej równoległych kopii w docs/knowledge/ zwiększa ryzyko rozbieżności.

## Decyzje

1. **Kolejność:** najpierw wiedza centralna (C1-C4), następnie projektowe docs i konwersja (P1-P4). Praktyczny odbiór wiedzy centralnej poprzedza etap projektowy.
2. **Lokalizacje:** ~/.agents/okf/ jest katalogiem centralnych bundles. Projektowy bundle wskazuje domyślnie docs/ w zatwierdzonym root projektu. Nie tworzymy drugiego magazynu tej samej dokumentacji. Uzupełnienie decyzji 2026-10-15: bezpośrednie <root>/docs podpinamy automatycznie, a panel służy do wyłączenia automatyki i ręcznych wyjątków. Centralne zbiory pozostają rejestrowane ręcznie. Ręczne wiązania mają pierwszeństwo; wykryte wpisy nie są utrwalane w rejestrze konfiguracji.
3. **Źródło prawdy:** oryginalne pliki są nadrzędne; indeks jest odtwarzalny. AGENTS.md pozostaje źródłem zasad, CURRENT.md stanu, ADR decyzji. Metadane OKF nie zmieniają tych ról.
4. **Architektura:** osobny okf_engine bez FastMCP oraz adaptery server/knowledge_tools.py i administracja panelu. Skill pomaga opracowywać metadane; nie zastępuje serwerowych walidacji i zgód.
5. **Dostęp:** bundle jest jawnie powiązany z zatwierdzonym root i subpath. Włączenie bundle nie nadaje nowych uprawnień plikowych. Nie dodajemy rw do całego ~/.agents/ ani wyjątku osłabiającego ochronę skills. Sąsiedni okf/ może być osobnym root, o ile przejdzie aktualne guardy.
6. **Tożsamość:** bundle_id + concept_id, bez cichego przesłaniania między centralną i projektową wiedzą. Zapis wymaga wskazania konkretnego bundle. Brak automatycznej promocji i synchronizacji kopii.
7. **Narzędzia centralne:** knowledge_bundles, knowledge_search, knowledge_get, knowledge_save. Wyszukiwarka zwraca krótkie wyniki z identyfikatorem sekcji i rewizją; get pozwala pobrać outline albo sekcję. Zimny/pełny skan działa jako zadanie; nie dodajemy osobnego MCP do zarządzania konfiguracją.
8. **Rewizje:** nie ujawniamy surowych hashy zawartości. Rewizja jest opaque tokenem powiązanym z plikiem i wiązaniem bundle, chronionym kluczem tylko w pamięci procesu. Restart wymaga ponownego odczytu. Aktualizacja sprawdza rewizję przed przygotowaniem oraz snapshot po decyzji w panelu.
9. **Zapis:** create/update wyłącznie po zgodzie, przez istniejące PreparedChanges, FileTransaction i TaskManager. Brak Ufaj dla zapisów. Pełny YAML przy jawnej edycji jest widoczny w diffie; brak zgody na ciche gubienie istniejących kluczy. Pierwszy MVP nie tworzy automatycznie katalogów.
10. **Indeks:** w pamięci, tekst i metadane po maskowaniu, bez trwałej bazy i embeddingów. SQLite FTS5 jest późniejszą optymalizacją, nie warunkiem startu.
11. **Poufność:** dotychczasowy mask_text rozpoznaje przede wszystkim pliki sekretów i nie zabezpiecza ogólnych .md. Wymagany jest dedykowany, przetestowany etap maskowania OKF przed ekstrakcją publicznych metadanych, dopasowaniem i indeksowaniem. Nie deklarujemy pełnego DLP. Narzędzia wiedzy są rejestrowane jako confidential.
12. **Zaufanie:** verified jest deklarowanym sygnałem, nie podpisem, uprawnieniem ani instrukcją. Zgoda na zapis, konwersja i poprawny YAML nie oznaczają weryfikacji wiedzy. Ostrzegamy o weryfikacji starszej od znaczącej zmiany treści.
13. **Konwersja:** in place, wyłącznie frontmatter i z zachowaniem body. Model proponuje metadane; knowledge_audit i knowledge_convert kontrolują zakres i zapis. Zwykły Markdown podczas migracji jest jawnie oznaczony. Zgodność aktywnego zakresu nie jest zgodnością całego drzewa docs.
14. **Runtime poza zakresem:** narzędzia wiedzy nie wykonują SQL, skryptów, executors ani attesters, nie pobierają URL automatycznie. Można przechowywać opis Attested Computation, ale nie ogłaszamy obsługi atestacji.
15. **Format zapisów projektu:** domyślna zgodność pozwala pracować bez konwersji starszych projektów. Poprawny OKF jest chroniony przed degradacją we wspólnych zapisach MCP. Jawny require_okf obejmuje wybrane podkatalogi i wyjątki, jest przypięty do ścieżki projektu i ustawiany przez użytkownika w panelu. Konwersja nie włącza go automatycznie. Szczegóły: [polityka zapisu](../specs/okf-integration/write-policy.md).

## Rozważone alternatywy

- **Wyłącznie skill/skrypt:** szybki prototyp, ale słabszy kontrakt rewizji, administracji i zapisów; odrzucone jako docelowy fundament.
- **Osobne docs/knowledge/:** proste wydzielenie, ale duplikacja istniejącej wiedzy; zamiast tego stopniowe wzbogacenie oryginalnego docs/.
- **Od razu baza wektorowa:** większy koszt operacyjny i poufności, bez pomiaru potrzeb; odłożone.
- **Projekt przesłania centralną wiedzę:** wygodne dla skilli, ale ukrywa sprzeczności między źródłami; odrzucone.
- **Surowy SHA-256 jako publiczna rewizja:** prosty, lecz może pozwalać na zgadywanie ukrytej treści; zamiast niego token chroniony kluczem procesu.

## Konsekwencje i ograniczenia

- Powstaje nowa funkcja serwera i panelu, ale bez drugiego systemu uprawnień czy zgód.
- Rewizje trzeba pobrać ponownie po restarcie. Indeks również jest odbudowywany; historyczne zadania nie przywracają prywatnych planów.
- Wyszukiwanie może zwrócić wyniki z wcześniejszego skanu z jawną informacją o stanie odświeżenia. Get zawsze odczytuje plik na nowo; tylko to pozwala potwierdzić bieżący fragment.
- Frontmatter nie rozwiązuje dokumentów mieszających historię i stan aktualny. Selektor bieżącej sekcji to jawne rozszerzenie projektowe, nie domyślne zgadywanie.
- Root rw nadal daje dostęp również zwykłymi narzędziami plikowymi po ich zgodach. Enabled dotyczy API OKF, nie jest uniwersalnym ACL ani sandboxem skryptów.
- Maskowanie jest heurystyką; nietypowe sekrety i poufne dane biznesowe mogą nie zostać rozpoznane. Dane zwrócone przez MCP mogą trafić do rozmowy.
- Istniejące transakcje zapewniają walidację, staging i best-effort rollback, nie pełną atomowość wielu plików przy awarii systemu ani sandbox wobec TOCTOU.
- Sama akceptacja C0 nie była potwierdzeniem kodu ani testów. Obecny zakres potwierdzeń jest w CURRENT; wpisy historyczne nie poświadczają pełnego bieżącego pytest.

## Dokumenty związane

- docs/archive/pre-okf-2026-10-15/plan/okf.md — historyczny kierunek i kolejność wdrożenia; aktualizacja kolejności na początku tego ADR.
- docs/specs/okf-support.md — lokalny kontrakt narzędzi, konfiguracji, formatu i bezpieczeństwa.
- docs/specs/OKF/SPEC.md — oryginalna specyfikacja OKF 0.2, bez zmian w C0.
- docs/decisions/0002-task-queue-and-web-panel.md — kolejka, limit wywołania i panel zgód.
