# AGENTS.md

## Projekt
Nowy lokalny serwer MCP ("vibecoder") oparty na standardzie Agent Skills (agentskills.io). Udostępnia ubogiemu harnessowi pliki/roots, kolejkę i panel zgód, skills oraz wiedzę OKF. Ogólne wykonywanie komend pozostaje propozycją w backlogu. Integracja zewnętrznych MCP jest wdrożona: trwałe profile w panelu, trzy narzędzia modelu, zaufanie i poufne zadania. Kontrakt v1: `docs/specs/external-mcp-support.md`; zakres odbioru i ograniczenia: CURRENT, pozostałe prace: backlog. Nie oznacza to pełnej ochrony SSRF ani gotowości produkcyjnej.

## Stack
Python, FastMCP + Uvicorn (MCP obecnie 8101; historyczny cel 8100), panel webowy (127.0.0.1:8102: zatwierdzenia i konfiguracja folderów), Miniforge/conda (środowiska skryptów skills).

## Platformy
Development odbywa się na Linuksie; docelowy system MCP to Windows. Zachowujemy reguły ścieżek Windows i testy junctionów. Kod wewnętrzny podaje ścieżki względem przypiętego roota, aby działał także podczas developmentu POSIX. Venv, lokalne config.json, bazy zadań i dane wiedzy nie są przenoszone pomiędzy systemami przez Git.

## Status
Implementacja i odbiór testowy. Aktualny stan: `docs/plan/CURRENT.md`. Jedyny aktualny backlog: `docs/plan/backlog.md`. Przeniesienie wersji OKF i scenariusze Windows: `docs/windows-okf.md`.

## Dokumentacja
- `docs/specs/skills-support.md` – obsługa skills (spec + nasze rozszerzenia)
- `docs/decisions/` – ADR (decyzje architektoniczne)
- [README](README.md) – opis projektu, instalacja, uruchomienie i podstawowe użycie
- [Indeks docs](docs/index.md) – mapa dokumentacji; wybierz temat bez ładowania całości
- README i indeks są punktami wejścia; nie zastępują kontraktów, bieżącego stanu ani zasad z tego pliku
- [Kontrakty OKF](docs/specs/okf-support.md) i [standard OKF 0.2](docs/specs/OKF/SPEC.md) – krótkie mapy dokumentów
- `docs/plan/` – tylko bieżący stan (`CURRENT.md`) i jeden backlog
- [Archiwum](docs/archive/README.md) – zakończone/dawne plany i wyniki sesji; nie jest listą prac

## Oszczędny odczyt dokumentacji OKF
- Zacznij od `prepare_project(root="sklr", subpath=...)` dla katalogu pracy. Obowiązujące `AGENTS.md`, także zagnieżdżone, czytaj w całości; doczytaj skrócone instrukcje.
- Nie wczytuj hurtowo dokumentacji ani pełnych plików na zapas. Powyższa mapa wskazuje tematy, nie listę dokumentów do odczytania w całości.
- Wybierz właściwy bundle przez `knowledge_bundles`, znajdź temat przez `knowledge_search`, następnie przejrzyj nagłówki przez `knowledge_get(view="outline")`. Dla znanego dokumentu możesz od razu pobrać outline.
- Odczytuj tylko potrzebne sekcje przez `knowledge_get(view="section")`, z `section_id` i aktualnym `expected_revision`. Używaj `section_mode="direct"` dla samej sekcji; `"subtree"` tylko, gdy potrzebujesz podsekcji. Po konflikcie rewizji pobierz nowy outline.
- Rozszerzaj odczyt stopniowo, uwzględniając istotne odwołania. `view="document"` stosuj tylko, gdy potrzebny jest cały dokument. Gdy narzędzia OKF są niedostępne, czytaj niezbędne zakresy Markdown przez `read_file`, bez hurtowego ładowania dokumentacji.

## Spec-driven development
- Przed zmianą zachowania wskaż właściwy kontrakt w `docs/specs/` i kryteria akceptacji. Gdy wymagań brakuje lub je zmieniasz, najpierw uzgodnij cel, zakres, oczekiwane zachowanie i plan, dopiero potem implementuj.
- Nowe i aktualizowane specyfikacje utrzymuj w OKF (frontmatter YAML + Markdown), najlepiej przez `knowledge_save`; aktualizacja wymaga świeżej rewizji. Nie degraduj poprawnego OKF. Istniejący Markdown pozostaje czytelny, a konwersja wymaga osobnej zgody. `AGENTS.md` nie wymaga OKF.
- Draft, propozycja i historyczny checkpoint nie oznaczają obowiązującej decyzji. Przy sprzeczności zapytaj użytkownika. Brak OKF nie blokuje rozpoznania zadania i nie uprawnia do zmiany polityki zapisu.
- Po implementacji uzgodnij kontrakt z faktycznym zachowaniem; bieżący stan i wyniki zapisuj w CURRENT, a otwarte zadania w jednym backlogu. Nie nadawaj `verified` bez weryfikacji.

## Test-driven development
- Dla zmian zachowania pracuj: kryterium → test RED → minimalna implementacja → test GREEN → refaktoryzacja i ponowne testy. Dla błędu najpierw dodaj test regresji; uwzględniaj bezpieczeństwo, zgody i zgodność Windows/POSIX.
- Dobierz dostępny skill przez `list_skills` i `activate_skill`; do lokalnych testów pytest używaj `test-runner` zgodnie z jego instrukcjami. Interpreter, runner i zakres ustal z aktualnej konfiguracji i dokumentacji, nie zgaduj.
- Zachowaj poniższy wymóg wyraźnego polecenia użytkownika do uruchomienia pytest. Zgoda na edycję lub commit/push nie jest poleceniem uruchomienia testów. Jeśli RED/GREEN nie wykonano, zgłoś blokadę zamiast deklarować TDD jako potwierdzone.
- Brak infrastruktury lub właściwego skilla zgłoś jako blokadę weryfikacji i uzgodnij sposób sprawdzenia. Nie instaluj zależności ani nie obchodź ograniczeń narzędzi automatycznie.
- Testy wykonują kod i mogą korzystać z sieci lub zmieniać dane; przed integracyjnymi potwierdź system docelowy i zakres skutków. Raportuj zakres, wynik, liczbę testów i pominięcia. Zero testów, brak wykonania lub historyczny wynik nie oznaczają sukcesu; lint/build nie zastępują testów zachowania.

## Konwencje
- Jeden plik = jedna odpowiedzialność.
- Conventional Commits, wiadomości ekstremalnie zwięzłe.
- Plan i akceptacja użytkownika przed każdą zmianą kodu. Zachowuj cudze zmiany; roots nie muszą należeć do jednego repo Git. Ten plik nie nadaje dodatkowych uprawnień.
- Nie zapisuj sekretów w dokumentacji, argumentach ani historii zadań; korzystaj wyłącznie z zatwierdzonych roots i mechanizmu zgód sklr.
- Ręczne awaiting_approval: na każdy osobny task_id wolno wykonać JEDNO wait_task(seconds=6). Kolejne zadanie w tej samej sekwencji ma własne oczekiwanie do 6 s; zadanie obejmujące wiele plików ma jedno oczekiwanie, nie osobne dla każdego pliku. Nie ponawiać oczekiwania na tę samą zgodę ani nie tworzyć zadania w celu resetu limitu. Jeśli nadal brak decyzji, podsumować i STOP, czekać na wiadomość użytkownika bez kolejnych wait, anulowania lub resubmit. To polityka modelu, nie serwerowy licznik oczekiwań. queued/running po decyzji oznacza oczekiwanie na wykonanie, nie na człowieka: śledzić ten sam task_id, następny zależny krok dopiero po potwierdzonym sukcesie i w aktualnym zakresie zgody. Limit 8 s wait_task pozostaje limitem jednego wywołania, nie życia zadania; budżet autopilota osobno do uzgodnienia. Pauza/utrata zgody blokuje nowe kroki. Po :ping najpierw list_tasks, nie ponawiać w ciemno. Decyzja zawsze w panelu; Allow nie dowodzi sukcesu. Sekrety/poufne wyniki pobierać wyłącznie przez get_task, bez przedłużania TTL.
- Stary MCP (`MCP-my-codex`) jest referencją tylko do odczytu.
- Nie uruchamiaj pytest ani serwera API/uvicorn bez wyraźnego polecenia; podawaj komendę użytkownikowi.
- Python: venv/conda; instalacji i tworzenia środowisk nie wykonuj automatycznie.

## Źródła kontekstu i archiwum
Pracujemy bezpośrednio przez sklr. Zasady: `AGENTS.md`; aktualny stan i następny krok: `docs/plan/CURRENT.md`; kontrakty: właściwy temat w `docs/specs/`; wyniki historyczne: `docs/archive/`; decyzje: `docs/decisions/`. Bieżące sekcje stanu są nadrzędne wobec historii.
Nie aktualizujemy `.vibecoder_context.md` ani `.vibecoder_checkpoints.md` i nie wymagamy ich odczytu podczas pracy przez sklr. Oba pliki użytkownik przeniósł, bez zmiany nazw, do `docs/archive/vibecoder/`; obecność w archiwum i brak w korzeniu repo potwierdzono listowaniem. To wyłącznie historia, nie aktywny fallback.
Bieżący stan i zakres potwierdzonych testów zapisujemy zwięźle w CURRENT.md, zadania wyłącznie w backlog.md, historię w docs/archive/. Nie dopisujemy kolejnych wyników sesji do aktywnych specyfikacji ani nie tworzymy kopii wiedzy w docs/knowledge/. Przed awaryjnym podłączeniem starego vibecodera sprawdzamy jego wymagania kontekstu; nie przywracamy automatycznie nieaktualnego streszczenia.
