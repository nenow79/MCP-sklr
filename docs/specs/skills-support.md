# Obsługa Agent Skills

Kontrakt lokalnej implementacji sklr; źródło standardu wskazane w poprzedniej wersji: https://agentskills.io. Starsze, zastąpione założenia (enum/deduplikacja activate_skill, brak skanowania dev, okno zaufania) zachowano w [archiwum](../archive/pre-okf-2026-10-15/specs/skills-support.md), nie są aktualnym kontraktem.

## Standard
- Skill = folder z `SKILL.md` (frontmatter YAML: `name`, `description` wymagane; opcjonalnie `license`, `compatibility`, `metadata`, `allowed-tools`) + opcjonalne `scripts/`, `references/`, `assets/`.
- `name`: do 64 znaków, a-z0-9 i myślniki, bez myślnika na początku/końcu i bez `--`, zgodne z nazwą folderu.
- `description`: do 1024 znaków, co robi i kiedy użyć.
- Progressive disclosure: katalog (name+description) -> pełny SKILL.md (<5000 tokenów, <500 linii) -> zasoby na żądanie.
- Lokalna walidacja lenientna: ostrzegaj, ładuj; pomiń skill tylko przy braku description lub nieparsowalnym YAML. Fallback dla niepoprawnego YAML (np. dwukropek w wartości).
- Referencje w skillu: ścieżki względne od katalogu skilla, maks. 1 poziom zagłębienia.

## Aktualny kontrakt po P1-P3/T1-T2 (2026-10-13)
- Źródła: project > central > dev. Project pochodzi tylko z domyślnego roota; dev z skills.dev_dirs (domyślnie []). Kolizje rozstrzygane przed filtrem dostępności; wyłączenie zwycięzcy nie odsłania kopii.
- Dostępność zapisana w config.json: central/project przez skills.disabled (domyślnie włączone), dev przez skills.dev_enabled (domyślnie wyłączone). Flagi po nazwie; dostępność i trust są niezależne. Blokada w rejestrze dotyczy list_skills, activate_skill, read_skill_resource i run_skill_script. Nie cofa już przygotowanych zadań.
- Panel Skille: dostępność, źródło, ostrzeżenia, kopie przesłonięte, katalogi dev tylko do odczytu. Filtry Central/Dev/Project i licznik są tylko preferencją widoku w localStorage, nie konfiguracją dostępu.
- Panel nadaje/cofa zaufanie tylko zwycięskim central, również wyłączonym. Nadanie wymaga potwierdzenia ścieżki i bieżącego hasha; backend sprawdza wersję ponownie, konflikt 409. Zapis przez ConfigStore.update z rollbackiem. Endpointy panelu chronione sesją, Host i Origin; brak narzędzia MCP do zmiany dostępności/zaufania.
- Central pomija zgodę tylko przy vibecoder-confirm=false i zgodnym zaufanym hashu; dev/project zawsze pytają. Hash SKILL.md + scripts/ nie obejmuje references/, assets/, środowiska ani danych wejściowych. To nie sandbox; TOCTOU i uprawnienia użytkownika pozostają ograniczeniem.
- list_skills: aktywne nazwy/opisy/źródła/ostrzeżenia, disabled_count, shadowed_count, diagnostyka skanu (może wspominać kopie). Status trustu w panelu, nie katalogu MCP. activate_skill przyjmuje str, bez enum i deduplikacji w sesji. Skan przy każdym list_skills; aktywacja przy braku rekordu robi rescan.
- Nowe skille powstają w dev-skills przez create_file/manage_folder z zatwierdzeniem. Po walidacji i testach użytkownik promuje folder ręcznie do ~/.agents/skills. Docelowo testy logiki skilla przy skillu, oddzielnie od testów silnika MCP; obecne testy zewnętrznych skilli w tests/ pozostają długiem do migracji.

## Rozszerzenia (klucze `metadata`)
- `vibecoder-confirm: "false"` – deklaracja braku potwierdzenia dla skryptów. Działa WYŁĄCZNIE dla skills z katalogu centralnego i tylko przy zgodnym hashu. Domyślnie potwierdzenie wymagane. Opcjonalnie per skrypt.
- `conda-env: "<nazwa>"` – środowisko Miniforge dla skryptów (możliwe współdzielenie między skills).

## Uruchamianie skryptów
- Interpreter wprost: `<miniforge>\envs\<env>\python.exe scripts\x.py` (bez `conda run`, bez shella).
- cwd = katalog skilla; ścieżka ograniczona do `scripts/` tego skilla.
- Brak interaktywności, timeout, limit outputu (zgodnie z obecnym 6000 znaków/strumień), strukturalny output.
- Brak środowiska: błąd z gotową komendą `conda create`/`conda install` (lub `assets/environment.yml`) dla użytkownika; MCP niczego nie instaluje.
- Inne języki wg rozszerzenia skryptu: `.js` (`node`), `.sh` (`bash`), `.ps1` (`pwsh`, a gdy brak: `powershell`).
- PowerShell: `<exe> -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <skrypt> <args>`, bez shella. Runner dekoduje output jako UTF-8; Windows PowerShell 5.1 domyślnie pisze w kodowaniu konsoli, więc skrypt `.ps1` powinien zaczynać się od `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()`. Kod wyjścia ustawiaj przez `exit N`. Na Windows preferuj `.ps1` dla prostych skryptów systemowych, `.py` gdy potrzebna jest logika.


## Narzędzia i tworzenie skilli

list_skills pokazuje katalog; activate_skill zwraca SKILL.md i listę zasobów bez ich automatycznego czytania; read_skill_resource czyta tekst z katalogu skilla; run_skill_script zleca skrypt kolejce. Stan zadania sprawdzamy narzędziami task zgodnie z [AGENTS](../../AGENTS.md).

Nowy skill powstaje w dev-skills z pomocą skill-writer, przechodzi walidację i własne testy; użytkownik promuje go ręcznie do ~/.agents/skills. Dev jest już skanowane zgodnie z skills.dev_dirs, ale domyślnie wyłączone dla LLM. skill-writer jest zewnętrznym skillem, nie częścią tej specyfikacji ani gwarantowaną instalacją. Testy logiki skilli docelowo mieszkają przy skillach; migracja obecnych zewnętrznych testów pozostaje w [backlogu](../plan/backlog.md).
