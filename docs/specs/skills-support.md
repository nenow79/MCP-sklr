# Obsługa Agent Skills

Źródło: https://agentskills.io (specification, using-scripts, adding-skills-support).

## Standard
- Skill = folder z `SKILL.md` (frontmatter YAML: `name`, `description` wymagane; opcjonalnie `license`, `compatibility`, `metadata`, `allowed-tools`) + opcjonalne `scripts/`, `references/`, `assets/`.
- `name`: do 64 znaków, a-z0-9 i myślniki, bez myślnika na początku/końcu i bez `--`, zgodne z nazwą folderu.
- `description`: do 1024 znaków, co robi i kiedy użyć.
- Progressive disclosure: katalog (name+description) -> pełny SKILL.md (<5000 tokenów, <500 linii) -> zasoby na żądanie.
- Walidacja lenientna: ostrzegaj, ładuj; pomiń skill tylko przy braku description lub nieparsowalnym YAML. Fallback dla niepoprawnego YAML (np. dwukropek w wartości).
- Referencje w skillu: ścieżki względne od katalogu skilla, maks. 1 poziom zagłębienia.

## Aktualny kontrakt po P1-P3/T1-T2 (2026-10-13)
- Źródła: project > central > dev. Project pochodzi tylko z domyślnego roota; dev z skills.dev_dirs (domyślnie []). Kolizje rozstrzygane przed filtrem dostępności; wyłączenie zwycięzcy nie odsłania kopii.
- Dostępność zapisana w config.json: central/project przez skills.disabled (domyślnie włączone), dev przez skills.dev_enabled (domyślnie wyłączone). Flagi po nazwie; dostępność i trust są niezależne. Blokada w rejestrze dotyczy list_skills, activate_skill, read_skill_resource i run_skill_script. Nie cofa już przygotowanych zadań.
- Panel Skille: dostępność, źródło, ostrzeżenia, kopie przesłonięte, katalogi dev tylko do odczytu. Filtry Central/Dev/Project i licznik są tylko preferencją widoku w localStorage, nie konfiguracją dostępu.
- Panel nadaje/cofa zaufanie tylko zwycięskim central, również wyłączonym. Nadanie wymaga potwierdzenia ścieżki i bieżącego hasha; backend sprawdza wersję ponownie, konflikt 409. Zapis przez ConfigStore.update z rollbackiem. Endpointy panelu chronione sesją, Host i Origin; brak narzędzia MCP do zmiany dostępności/zaufania.
- Central pomija zgodę tylko przy vibecoder-confirm=false i zgodnym zaufanym hashu; dev/project zawsze pytają. Hash SKILL.md + scripts/ nie obejmuje references/, assets/, środowiska ani danych wejściowych. To nie sandbox; TOCTOU i uprawnienia użytkownika pozostają ograniczeniem.
- list_skills: aktywne nazwy/opisy/źródła/ostrzeżenia, disabled_count, shadowed_count, diagnostyka skanu (może wspominać kopie). Status trustu w panelu, nie katalogu MCP. activate_skill przyjmuje str, bez enum i deduplikacji w sesji. Skan przy każdym list_skills; aktywacja przy braku rekordu robi rescan.
- Nowe skille powstają w dev-skills przez create_file/manage_folder z zatwierdzeniem. Po walidacji i testach użytkownik promuje folder ręcznie do ~/.agents/skills. Docelowo testy logiki skilla przy skillu, oddzielnie od testów silnika MCP; obecne testy zewnętrznych skilli w tests/ pozostają długiem do migracji.

Poniższe starsze założenia obowiązują tylko tam, gdzie nie są zastąpione kontraktem powyżej.

## Katalogi skills
- Centralny (zaufany): domyślnie `~/.agents/skills/`, ścieżka nadpisywalna w `config.json`.
- Projektowy (opcjonalny): `<PROJECT_ROOT>/.agents/skills/`; przy kolizji nazw wygrywa projektowy; zawsze wymaga potwierdzenia.
- Skan: przy starcie i po zmianie workspace; pomijaj `.git`, `node_modules`; limity głębokości i liczby katalogów.

## Narzędzia MCP
- `list_skills` – katalog (name, description, źródło, status zaufania).
- `activate_skill(name)` – treść w `<skill_content name="...">`, katalog skilla, lista zasobów (bez ich czytania). `name` jako enum. Deduplikacja aktywacji w sesji.
- `read_skill_resource(name, path)` – plik tekstowy z katalogu skilla.
- `run_skill_script(name, script, args, timeout)` – uruchomienie skryptu z `scripts/` danego skilla. Parametr `name` oznacza nazwę skilla we wszystkich narzędziach (zgodnie z polem `name` we frontmatterze).
- Pliki `references/` czytane istniejącym `read_file`; katalogi skills dozwolone tylko do odczytu w path guard.

## Rozszerzenia (klucze `metadata`)
- `vibecoder-confirm: "false"` – deklaracja braku potwierdzenia dla skryptów. Działa WYŁĄCZNIE dla skills z katalogu centralnego i tylko przy zgodnym hashu. Domyślnie potwierdzenie wymagane. Opcjonalnie per skrypt.
- `conda-env: "<nazwa>"` – środowisko Miniforge dla skryptów (możliwe współdzielenie między skills).

## Zaufanie
- Pierwsze uruchomienie: okno "Ufaj temu skillowi?"; zapis hasha `SKILL.md` + `scripts/` w `config.json`. Zmiana hasha = ponowne pytanie.
- Skills projektowe: flaga ignorowana, zawsze potwierdzenie.
- Konfiguracja potwierdzeń per skill/skrypt oraz włączanie/wyłączanie skills w oknie konfiguracji.

## Uruchamianie skryptów
- Interpreter wprost: `<miniforge>\envs\<env>\python.exe scripts\x.py` (bez `conda run`, bez shella).
- cwd = katalog skilla; ścieżka ograniczona do `scripts/` tego skilla.
- Brak interaktywności, timeout, limit outputu (zgodnie z obecnym 6000 znaków/strumień), strukturalny output.
- Brak środowiska: błąd z gotową komendą `conda create`/`conda install` (lub `assets/environment.yml`) dla użytkownika; MCP niczego nie instaluje.
- Inne języki: `bash`, `node` wg rozszerzenia skryptu.

## Tworzenie skilli (skill-writer)
- Skill `skill-writer` (katalog centralny) prowadzi przez tworzenie skilli: szablon SKILL.md, reguły zgodne z `skills_engine`, lista kontrolna, walidator `scripts/validate_skill.py` (korzysta z parsera `skills_engine` z repo sklr).
- Nowe skille powstają w folderze deweloperskim (root `dev-skills`, zapis przez create_file z zatwierdzeniem w panelu). Sklr go jeszcze nie skanuje; wdrożenie do `~/.agents/skills` wykonuje użytkownik ręcznie.
- Plan (krok 3): trzy poziomy zaufania: central (dopracowane, trust hash, vibecoder-confirm), dev (`skills.dev_dirs`, jak projektowe: potwierdzenie zawsze, central wygrywa przy kolizji, domyślnie wyłączone dla LLM), project. Panel pozwala włączać i wyłączać skille (`skills.disabled`).
