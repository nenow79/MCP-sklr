# Faza 5: narzędzia podstawowe (plan szczegółowy, wersja 2)

Status: PLAN ZAAKCEPTOWANY. Podfaza 5.0 ZREALIZOWANA (testy automatyczne i ręczne zaliczone, patrz "Wyniki 5.0" na końcu). Następna: 5.1 (`roots`), najpierw plan szczegółowy do akceptacji. Wersja 2 uwzględnia statystyki użycia starego MCP i ustalenia z rozmowy.
Podstawa: ADR 0001 (pkt 2, 3, 7), ADR 0002 (model zagrożeń panelu), `docs/plan/implementation.md`.
Cel: przenieść ze starego MCP tylko te narzędzia, które realnie się przydają, w nowej architekturze (moduły, kolejka zadań, zatwierdzanie bez limitu czasu przez panel, trwała konfiguracja). Reszta trafia do skills lub późniejszych faz.

## 0. Wnioski ze statystyk starego MCP (telemetria, 605 wywołań)

| Grupa | Wywołania | Odsetek błędów | Decyzja |
|---|---|---|---|
| Odczyt i search: `search_code_context` 59, `read_file_lines` 50, `read_file` 50, `read_files_batch` 30, `list_directory` 29, `search_code` 21 | 239 (40%) | `search_code_context` 36%, `search_code` 33%, `read_file` 2% | Faza 5a, konsolidacja do 4 narzędzi |
| Zapisy: `edit_file` 138, `create_file` 31, `manage_folder` 5, `rewrite_full_file` 4 | 178 (29%) | `edit_file` 3%, `create_file` 3% | Faza 5b |
| Kontekst: `get_system_info` 31, `workspace_overview` 28, `checkpoint_context` 19 | 78 (13%); ok. 607 KB outputu, 33% całości | `checkpoint_context` 5% | Faza 7 (zwięzły overview bez złotych zasad) |
| Workspace i referencje: `switch_workspace` 17, `set/clear/list_reference_dir` po 8, `get_current_workspace` 6, `swap...` 1 | 48 (8%) | `clear_reference_dir` 12% | Zastąpione modelem `roots` (sekcja 2.2) |
| `execute_command` | 41 (7%) | 34% (14 z 41) | Faza 5c, wariant mieszany z skills (sekcja 4) |
| Excel/CSV (10), API (`test_local_api` 8, `get/set_local_api_config` 3) | 21 (3%) | brak | Skills w fazie 8 (`excel-analysis`, `api-testing`) |
| Zewnętrzne MCP, `fetch_external_api`, `free_port`, `reset_workspace` | 0 | | Zewnętrzne MCP do fazy 6, reszta odpada |

Przyczyny błędów (z `error_summary` w logach), które zamieniamy na wymagania:
- `search_code(_context)`: nieistniejący `subpath` (ok. 11 przypadków, w tym plik zamiast katalogu), `context_lines` poza zakresem 0-30 (3), alias referencji `"false"` zamiast braku referencji, stary błąd `Path` (naprawiony).
- `execute_command`: `cd ... && komenda` (8 z 14 błędów), komenda spoza listy prefiksów, jeden zawieszony proces (czeka na klawisz; 222 s mimo timeoutu 60 s).
- `edit_file`: nie znaleziono `old_text` (2), plik nie istnieje (1).
- `create_file`: plik już istnieje (1).

Uwaga o danych: `search_code` ma średnio 30 s tylko przez 7 wywołań z jednego dnia w jednym projekcie (64-117 s każde, razem ok. 612 s z 622 s sumy). Pozostałe 14 udanych zajęło razem ok. 10 s (dwa wyjątki ok. 8-11 s). Budżet czasu w search jest zabezpieczeniem, nie głównym zadaniem. Telemetria nie zapisuje treści komend ani czasu oczekiwania na decyzję osobno.

## 1. Zasady przyjęte w rozmowie
1. **Zatwierdzanie bez limitu czasu.** Wszystkie operacje zmieniające stan (zapisy plików, komendy, zmiany roots) zwracają natychmiast `task_id` i `awaiting_approval`; użytkownik decyduje w panelu, kiedy chce (`approval_expiry_min` bez zmian: 60 min). `wait` do 8 s dotyczy tylko zadań, które już działają. Odpowiedź zawiera podpowiedź: model ma się zatrzymać i poczekać na wiadomość użytkownika, zamiast odpytywać `wait_task` w pętli (serwer nie obudzi modelu; polling zużywa tury narzędziowe).
2. **Wiele zmian w jednym zadaniu.** `edit_file` przyjmuje listę zmian (plik, `old_text`, `new_text`); jedno zadanie, jeden diff z podziałem na pliki, jedna decyzja (zamiast 138 osobnych klików). Zmiany są atomowe: wszystkie albo żadna.
3. **Wariant mieszany dla komend:** `execute_command` w kodzie (twarde reguły) + skills dla powtarzalnych operacji (git, python-env, npm, docker, proxy/env).
4. **Model `roots`** zamiast workspace + referencji (sekcja 2.2).
5. **P11:** wzorzec `cd <ścieżka> && komenda` tłumaczony na `cwd` (sekcja 4.1).
6. Pytania P2 (allowlista komend tylko-odczytu) i `switch_workspace`/`set_workspace` odpadają.

## 2. Architektura

### 2.0 Stan wyjściowy (z przeglądu kodu)
- `ApprovalService`, `TaskManager`, panel (`127.0.0.1:8102`) i `ExpiryWatcher` działają; `Task` jest "skillowy" (`skill`, `script`, `skill_dir`, `script_preview`, `skill_hash`), a `dialog_adapter.build_message` i frontend panelu zakładają zadanie-skrypt.
- `workspace.path` i `references` są w configu, ale `PROJECT_ROOT` jest stałą w `main.py`; `external_mcp` puste i nieużywane.
- `TrustStore` ma własny lock przy zapisie configu; każdy nowy kod piszący do configu musi użyć tej samej synchronizacji.
- Limit harnessa 8 s na wywołanie obowiązuje każde narzędzie.

### 2.1 Uogólnienie zadań (podfaza 5.0)
`Task` dostaje `kind`: `skill_script` (dotychczasowe) | `file_edit` | `file_write` | `folder` | `command` | `root_change`.
- Nowe kolumny `tasks.db`: `kind` (domyślnie `skill_script`), `title`, `payload` (JSON: ścieżki, diff, `argv`, `cwd`, hashe przed zapisem). Migracja `ALTER TABLE ... ADD COLUMN` z wartościami domyślnymi plus `PRAGMA user_version`; stare wiersze działają bez zmian.
- `TaskManager.submit` bez zmian w API; `kind/title/payload` przychodzą w `fields`. Executor `file_edit` zapisuje po zatwierdzeniu; `verify` porównuje hash plików z chwili generowania diffu (jak `verify` skilla): zmiana pliku w międzyczasie = `failed` z komunikatem, bez zapisu.
- Panel (`app.js`) i `dialog_adapter.build_message` renderują widok wg `kind` (diff, `argv` + `cwd`, ścieżki). Treść zawsze jako tekst (`textContent`). Opis podany przez model jest oznaczony "od modelu" i jest drugorzędny wobec tego, co faktycznie się wykona.
- `exit_code` = `None` dla `cancelled` i `timed_out` (rozstrzygnięcie 3.2).

### 2.2 Model `roots` (podfaza 5.1)
Lista folderów w `config.json`: `roots: [{alias, path, mode: "rw" | "ro", description, default}]`. Zastępuje `workspace.path`, `references` i narzędzia `switch_workspace`/`set_workspace`/`add_reference`/`swap_workspace_with_reference`.
- Każde narzędzie plikowe ma parametr `root` (alias; brak = root domyślny). Jeden root oznaczony `default`, wybierany przez użytkownika w panelu.
- Model widzi tylko `list_roots()` (alias, tryb, opis; ścieżki bez sekretów). Nie dodaje i nie zmienia roots sam: opcjonalne `request_root(path, mode, reason)` tworzy zadanie `root_change` do zatwierdzenia w panelu (bez "Ufaj").
- Zarządzanie z panelu: `GET /api/roots`, `POST /api/roots` (dodaj/zmień/usuń/ustaw domyślny), zakładka "Foldery" w `app.js`. Te endpointy podlegają tej samej ochronie co zatwierdzanie (ciasteczko sesji, `Origin`, `Host`, bez CORS).
- Walidacja przy dodawaniu: ścieżka istnieje i jest katalogiem; odrzucane: katalog główny dysku, katalog profilu użytkownika, `%APPDATA%`/`%LOCALAPPDATA%` i profile przeglądarek, katalogi systemowe, UNC bez jawnego potwierdzenia; roots nie mogą się zagnieżdżać z różnymi trybami (zagnieżdżony `ro` w `rw` jest dozwolony, odwrotnie zostaje `ro` jako bardziej restrykcyjny dla ścieżki wewnętrznej; zasady rozstrzygania do testów).
- Wprowadzenie ścieżki: pole tekstowe z walidacją po stronie serwera (przeglądarka nie zwraca pełnej ścieżki z dialogu). Dialog tkinter to dodatek na później.
- `ConfigStore` (jedyna ścieżka zapisu configu, wspólny lock; `TrustStore` przechodzi na niego) i `RootsRegistry` (czyste funkcje + stan thread-safe) zastępują `PROJECT_ROOT` i `WORKSPACE_NAME` w `main.py`. Telemetria dostaje alias domyślnego roota jako "workspace".
- Migracja configu: dotychczasowe `workspace.path` staje się rootem `rw` oznaczonym jako domyślny; `references` stają się rootami `ro`.
- Ograniczenie (zapisane w ADR 0003): tryb `ro` chroni narzędzia plikowe, ale nie `execute_command`. Komenda w rootcie `rw` może zapisać plik w `ro` przez ścieżkę absolutną. Barierą jest zatwierdzenie komendy, nie tryb. `cwd` komendy wolno ustawić tylko w rootach `rw`.

### 2.3 Moduły (jeden plik = jedna odpowiedzialność)
- `roots.py` (model i walidacja), `config_store.py` (zapis z lockiem), `server/path_guard.py`, `server/file_tools.py` (odczyt, search), `server/edit_tools.py` (zapisy i diff), `server/command_tools.py`, `server/roots_tools.py`; w `main.py` tylko rejestracja (`register_tool`) i wstrzykiwanie zależności, jak w fazach 2-4.
- `command_policy.py` w korzeniu repo (czyste funkcje bez I/O: `check_command(argv, cwd, ctx) -> Verdict`). Nazwa unika kolizji z `skills_engine/policy.py` (polityka potwierdzeń skills).
- Zewnętrzne MCP (`mcp_client/`) przechodzą do fazy 6.

### 2.4 Path guard (5a)
Wejście: ścieżka względna (względem roota) lub absolutna; wynik: `Path` rozwiązany i sprawdzony `is_relative_to` któregoś roota (tryb sprawdzany przy zapisie). Przypadki testowe (Windows):
- `..`, ścieżki absolutne poza rootami, inne dyski, UNC, prefiks `\\?\`.
- Symlinki i junctiony (junction da się testować bez Developer Mode; 2 testy symlinków są dziś pomijane).
- Nazwy 8.3 (`PROGRA~1`), wielkość liter (`normcase`), końcowe kropki i spacje, strumienie NTFS (`plik.txt:stream`), nazwy zarezerwowane (`CON`, `NUL`, `COM1`).
- TOCTOU: ponowna walidacja tuż przed zapisem (po zatwierdzeniu), nie tylko przy tworzeniu zadania.
- Ścieżki `sensitive` (nawet wewnątrz roota `rw`; konfigurowalna lista, wartości domyślne w P10): `panel/`, `command_policy.py`, `config.json`, `tasks.db*`, `.git/`, `main.py`, `state.py`. Zapis do nich = zadanie z flagą `sensitive`: wyróżnione w panelu, zawsze ręczne zatwierdzenie, bez "Ufaj". Ważne, bo repo tego serwera może być jednym z roots.

### 2.5 Narzędzia i kształty odpowiedzi
Odpowiedzi w kształcie `{"success": bool, "data"/"error"}`, walidacja parametrów jak w `task_tools` (typy, zakresy; `bool` nie jest `int`).

5a (odczyt, bez zatwierdzania, domyślnie 12000 znaków):
- `list_roots()`; `list_directory(subpath, root)`; `read_file(filepath, start_line, end_line, max_chars, root)` (zastępuje `read_file_lines`); `read_files_batch(requests)`; `search_code(query, subpath, case_sensitive, context_lines, max_matches, root)` (zastępuje `search_code_context`); `root="*"` przeszukuje wszystkie roots dostępne do odczytu, a wyniki są opisane aliasem roota.
- Tolerancja parametrów (wymagania z logów): `subpath` wskazujący plik = szukanie w tym pliku; nieistniejący `subpath` = błąd z listą dostępnych katalogów; `context_lines` obcinane do 0-30 zamiast błędu; `root` przyjmuje alias albo brak (puste, `false`, `"false"` = root domyślny); nieznany alias = błąd z listą aliasów.
- `search_code`: budżet czasu ok. 6 s z wynikiem częściowym i flagą `truncated`; pomijane katalogi (`.git`, `node_modules`, `venv`, `__pycache__`, `dist`, `build`, `coverage`), pliki binarne i duże; opcjonalnie `ripgrep`, gdy dostępny.
- **Maskowanie sekretów w odczycie (P14, zatwierdzone):** `read_file`, `read_files_batch` i `search_code` dla plików sekretów (`.env`, `.env.*` poza `.env.example`, `pip.ini`, `pip.conf`, `.npmrc`, `*.pem`, `*.key`, `*.pfx`) maskują wartości kluczy pasujących do `TOKEN|PASSWORD|SECRET|KEY|CREDENTIAL` oraz poświadczenia w adresach (`://user:pass@`). Odpowiedź zawiera flagę `masked=true` i liczbę zamaskowanych wartości. Ujawnienie pełnej wartości to osobne zadanie `secret_reveal` zatwierdzane w panelu (bez "Ufaj"); edycja tych plików to zadanie `sensitive`. Maskowanie działa też na dopasowaniach `search_code` (wyszukanie `PASSWORD` nie zwraca haseł). Zastępuje ostrzeżenie z P3. Powód: w tej sesji zwykłe `search_code` na referencji pokazało linie z `.env`.

5b (zapisy, zawsze zadanie `awaiting_approval`, natychmiastowy zwrot `task_id`):
- `edit_file(changes[], root)`: lista zmian (plik, `old_text`, `new_text`, opcjonalnie `root` per zmiana; brak = `root` z wywołania, potem root domyślny), więc jedno zadanie może obejmować kilka rootów (np. `frontend` i `backend` przy zmianie kontraktu API); w panelu diff pogrupowany wg `root: plik`, jedna decyzja, zapis atomowy wszystkich zmian; dokładne dopasowanie, błąd gdy 0 lub więcej niż 1 trafienie (z liczbą). Przy braku trafienia komunikat pokazuje najbliższy fragment pliku (różnica białych znaków lub końców linii). Zapis atomowy (plik tymczasowy + `os.replace`), zachowanie CRLF/LF i BOM.
- `create_file(filepath, content, overwrite=False, root)`: zastępuje `rewrite_full_file`; przy `overwrite=True` diff całego pliku.
- `manage_folder(action, path, new_name, root)`: `create` | `rename`. Usuwanie plików i folderów poza fazą 5 (P4).
- Diff w panelu: limit rozmiaru (ok. 200 KB), przy większym "diff skrócony"; znaki sterujące i dwukierunkowe Unicode (RLO) w ścieżkach i komendach pokazywane jawnie (escape).
- W opisach narzędzi: "przed ponowieniem po błędzie `: ping` sprawdź `list_tasks`" (pierwsze wywołanie po restarcie daje błąd, a zadanie i tak powstaje).

5c: `execute_command` (sekcja 4).

## 3. Rozstrzygnięcia otwartych tematów

### 3.1 `offer_trust` w `policy.py`
Logika `decide_confirmation` jest poprawna: `offer_trust=True` tylko dla skilla centralnego z `metadata.vibecoder-confirm: false`, który nie jest jeszcze zaufany (nowy lub zmieniony hash). Przycisk nie pojawiał się, bo żaden skill nie ma tej flagi. Decyzje:
1. Logika `policy.py` bez zmian.
2. Test ręczny na kopii `hello-skill` (np. `hello-trust`) z `vibecoder-confirm: false`: pierwsze uruchomienie oferuje "Ufaj", po zaufaniu kolejne bez pytania, zmiana pliku w `scripts/` cofa zaufanie (hash).
3. Zaufanie na stałe dotyczy wyłącznie skills. Dla `execute_command`, zapisów i zmian roots `offer_trust` jest zawsze `false`; `TaskManager.resolve_approval` już degraduje `trust` do `allow`. Zamyka to pytanie z ADR 0002 (zapisać w ADR 0003).
4. Konsekwencja wariantu mieszanego: operacje, które mają być wykonywane bez klikania (np. odczyt git), realizuje skill centralny z `vibecoder-confirm: false` i zaufanym hashem. To jedyny mechanizm "zaufanych operacji" w projekcie; katalog centralny leży poza rootami, więc model nie zmieni skryptu.

### 3.2 `exit_code` przy `cancelled`
Dziś runner zwraca kod ubitego procesu (na Windows zwykle 1), więc anulowane zadanie wygląda jak błąd. Decyzja: w `TaskManager._run` zapisujemy `exit_code = None` dla `cancelled` i `timed_out`; `FAILED` z realnym kodem bez zmian (`task_to_response` opiera się na statusie). Dostosować testy (`test_tasks_manager.py`, `test_task_tools.py`, ewentualnie `test_skills_run_tool.py`). Robimy w 5.0, bo `execute_command` dziedziczy to zachowanie.

### 3.3 Rola `gui_simple.py` (WYCOFANE w kroku T 5b)
Pierwotna decyzja (zapas w oknie tkinter) została zastąpiona: **wszystkie zatwierdzenia idą wyłącznie przez panel web, tkinter wycofany**. Szczegóły w sekcji "Krok T" w Wynikach 5b. Poniższe punkty mają wartość historyczną:
- (wycofane) `dialog_adapter` z wariantami tekstowymi i auto-odrzucaniem `sensitive`/`root_change`/za długiej treści;
- (wycofane) limit czasu okna `approval_timeout_s` sprzeczny z zasadą "decyzje bez presji czasu".

## 4. `execute_command`, `command_policy` i skills

### 4.1 Model wykonania
- Wejście: `command` (napis), `cwd` (podkatalog roota `rw`), `root`, `timeout`. Własny tokenizer (nie `shell=True`), odrzucenie metaznaków (`| & ; < > ` $( ) ^`, `%VAR%`, nowe linie). Wykonanie jako lista argumentów, interpreter wskazany wprost, `clean_env()`, `stdin=DEVNULL`, timeout 1-300 s, output 6000 znaków na strumień. Zawieszenie czekające na klawisz jest obsłużone (test regresji).
- **Środowisko komendy (`command_env_passthrough`, zatwierdzone):** `clean_env()` z runnera wycina dziś zmienne proxy i certyfikatów, więc `pip`, `npm`, `git` w sieci firmowej nie miałyby proxy. Konfigurowalna lista (edycja w panelu, w `config.json`), domyślnie: `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `PIP_CONFIG_FILE`, `NPM_CONFIG_USERCONFIG`, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`, `DOCKER_HOST`, `DOCKER_CONFIG`. Przekazywane tylko te, które istnieją w środowisku serwera (bez zgadywania wartości); nazwy pasujące do `TOKEN|SECRET|PASSWORD|KEY` nie mogą trafić na listę. Lista nie zawiera zmiennych tokenu panelu i MCP (test T1). Panel pokazuje, które zmienne przekazano do danego zadania (nazwy, nie wartości).
- Zablokowane uruchamianie plików `.bat`, `.cmd`, `.ps1` (cmd.exe i PowerShell są powłoką; argumenty z metaznakami wstrzykują polecenia w obsłudze `.bat`). Zamiast `dev.bat` model wywołuje właściwą komendę (np. `docker compose -f ... up`) przez skill.
- Zawsze zadanie `kind=command` z natychmiastowym `task_id` (zatwierdzenie bez limitu czasu); po decyzji `wait_task`/`get_task`/`cancel_task` jak dla skryptów skills.
- **P11 (zatwierdzone):** dokładny wzorzec `cd [/d] <ścieżka> && <komenda>` jest tłumaczony na `cwd` po sprawdzeniu path guardem (tylko roots `rw`) i tokenizacji reszty bez metaznaków. Powłoka nie jest używana; w panelu widać faktyczne `cwd` i `argv`. Wszystko inne z `&&`, `;`, `|` jest odrzucane komunikatem z gotowym poprawnym wywołaniem (`użyj cwd=... i command=...`). Odpowiedź przy tłumaczeniu informuje modela, jakiej formy użyć następnym razem.

### 4.2 Twarde reguły w kodzie (`command_policy`)
Zablokowane z komunikatem "co zrobić zamiast":
- `pytest` (także `python -m pytest`): zasada 7, komendę uruchamia użytkownik. Wyjątek konfigurowalny dla skilla w przyszłości, nie w fazie 5.
- `uvicorn`, `flask run`, `fastapi dev/run`, serwery deweloperskie (`npm run dev`, `npm start`, `vite`): zasada 8 i brak mechanizmu procesów w tle (P6 = poza fazą 5). Odpowiedź zwraca gotową komendę dla użytkownika.
- `pip install`, `python -m venv`, `conda create/install`: tylko zwracają gotową komendę dla użytkownika (zasady 4 i 10); ścieżki do interpretera z istniejącego venv (`venv\Scripts\python.exe`) są dozwolone.
- Komendy bez powłoki: `call`, `activate`, `set`, `echo %X%`, `deactivate` nie mają sensu (są wbudowane w cmd i działają tylko w jednej sesji powłoki). Python z venv wywołujemy wprost (`venv\Scripts\python.exe`), bez aktywacji; komunikat to wyjaśnia.
- Zmiany konfiguracji trwałej i sieci: `setx`, `reg add/delete`, `netsh`, `git config --global/--system`, `npm config set`, `pip config set`, `New-NetFirewallRule`: odrzucane (patrz 4.4).
- `docker` i `docker compose` (także `docker-compose`): zablokowane w `execute_command` z komunikatem "użyj skilla docker" (sekcja 4.5a). Blokada działa po nazwie pliku wykonywalnego, z `.exe` i wielkością liter.
- Porty: argument portu równy portowi panelu (8102) lub MCP (8101/8100) = blokada dla narzędzi sieciowych i serwerów (sekcja 4.3).
- `python -c` / `node -e` (inline kod niewidoczny dla guardu): blokada jak w starym MCP (P8).
- Opcjonalnie wymuszenie formatu Conventional Commits dla `git commit` w kodzie (P5); domyślnie robi to skill `git`.

### 4.3 Ryzyko żądań HTTP do localhosta (blokada portu panelu 8102)
Założenie z ADR 0002: model ma powłokę, więc może wysyłać żądania HTTP do localhosta; ochrona dotyczy pomyłek i prompt injection, nie celowo złośliwego modelu.

| # | Wektor | Środek | Typ ochrony |
|---|---|---|---|
| T1 | Bezpośrednie żądania do `http://127.0.0.1:8102/api/...` (`curl`, `Invoke-WebRequest`, `python`, skrypt zapisany `create_file`) | Panel wymaga ciasteczka sesji, `Origin` i `Host`; model nie zna tokenu (brak w env, argv, configu, bazie, plikach, odpowiedziach). Test: token nie występuje w `clean_env()`, `config.json`, `tasks.db`, odpowiedziach narzędzi | prawdziwa (kryptograficzna) |
| T2 | Wyciek tokenu z historii przeglądarki (`#token=...` trafia do profilu, link w konsoli) | Token jednorazowy (P1); profile przeglądarek poza dozwolonymi rootami; policy blokuje argumenty wskazujące te ścieżki | prawdziwa (jednorazowość), heurystyka (ścieżki) |
| T3 | Zajęcie portu 8102 przez inny proces (podszycie pod panel) | Link z tokenem tylko po udanym `PanelServer.start()` (jest); przy zajętym porcie zapas to okno bez linku; policy odrzuca port 8102 jako argument serwerów i narzędzi sieciowych; głośna diagnostyka po porażce bind | częściowo prawdziwa + heurystyka |
| T4 | Zbudowanie adresu lub portu w kodzie (`81*100+2`, base64, osobny skrypt) | Nie do zablokowania ogólnie. Środek: użytkownik widzi pełną komendę, a dla `python skrypt.py` w panelu podgląd treści skryptu z roota; guard nie jest granicą bezpieczeństwa (ADR 0003) | rezydualne, jawnie zaakceptowane |
| T5 | DNS rebinding i inna nazwa hosta | `Host` i `Origin` tylko `127.0.0.1:8102`/`localhost:8102` (jest) | prawdziwa |
| T6 | Żądania do portu MCP 8101 (szeroki CORS, bind `0.0.0.0`) z pominięciem harnessa | Na 8101 brak endpointów zatwierdzania; wszystkie narzędzia zmieniające stan tworzą zadanie `awaiting_approval`. Test regresyjny: żadne narzędzie nie ma parametru typu "approve/auto" | prawdziwa (architektura) |
| T7 | Zapis modyfikujący sam serwer (root = to repo): `panel/`, `command_policy.py`, `config.json`, `main.py` | Zadania `sensitive` (2.4): ręczne zatwierdzenie, bez "Ufaj" | prawdziwa |
| T8 | Podszycie opisu zadania ("bezpieczne, kliknij zezwól") | Panel pokazuje treść wykonywaną (argv, diff) jako główną, opis modelu jako drugorzędny z etykietą; brak HTML | prawdziwa |
| T9 | Zmiana roots przez model (nadanie sobie dostępu do dowolnego folderu) | Brak narzędzia modyfikującego roots; `request_root` tworzy zadanie do zatwierdzenia; endpointy `/api/roots` tylko z sesją panelu | prawdziwa |
| T10 | Subprocess dziedziczy sekrety z serwera | `clean_env()` (jest), `close_fds` (domyślnie) | prawdziwa |
| T11 | Zapis do roota `ro` przez `execute_command` (ścieżka absolutna) | Tylko zatwierdzenie komendy; `cwd` ograniczony do roots `rw`; opisane w ADR 0003 | rezydualne |

Heurystyczna detekcja `localhost`, `127.0.0.1`, `::1`, `0.0.0.0` w komendach nie blokuje (np. testy API na porcie 8000), tylko blokuje port panelu/MCP i dodaje plakietkę "łączy się z localhostem" w panelu. Wykrywane narzędzia: `curl`, `wget`, `iwr`, `irm`, `Invoke-WebRequest`, `Invoke-RestMethod`, `nc`, `ncat`, `telnet`, `ssh -L`.

### 4.4 Komendy firmowe: proxy i zmienne środowiskowe
Ustalone: LLM bywa proszony o komendy cmd dotyczące firmowego proxy i zmiennych środowiskowych. To zmiany o wysokiej wadze: `setx`, `npm config set proxy`, `git config --global http.proxy`, `pip config`, `netsh winhttp` zmieniają zachowanie sieci dla całego użytkownika, a podmiana proxy pozwala przechwytywać ruch (w tym tokeny).
- W `execute_command` są zablokowane (4.2). Model zwraca użytkownikowi gotowe komendy do własnego terminala (zasada jak dla venv).
- Odczyt: skill `env-info` (Python), wzorowany na projekcie referencyjnym CSM: zmienne `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, prefiks `DEV_*`, `PIP_CONFIG_FILE`, `NPM_CONFIG_FILE`, `DOCKER_REGISTRY`; `npm config get registry|proxy|userconfig`, `git config --get http.proxy`, `pip config list` (z maskowaniem `index-url` z tokenem). Zawartość `pip.ini` i `.npmrc` nie jest czytana: tylko ścieżka, istnienie pliku i nazwy kluczy. Nazwy pasujące do `TOKEN|SECRET|PASSWORD|KEY|PROXY_PASS` i adresy z poświadczeniami są maskowane.
- Zmiany proxy i zmiennych środowiskowych nie wchodzą w zakres skilla: model zwraca użytkownikowi komendy do własnego terminala. Wrócimy do tematu w fazie 8, jeśli pojawi się realna potrzeba (wtedy: jawna lista dozwolonych kluczy, osobne zadanie z ostrzeżeniem "zmiana trwała, dotyczy całego użytkownika", zawsze ręczne zatwierdzenie).
- Wzorzec z CSM: proxy żyje w zmiennych `DEV_*` w `.env` (bez systemowego `HTTP_PROXY`), a compose mapuje je na build args. Skill `env-info` rozpoznaje oba warianty.

### 4.5 Podział `execute_command` i skills
| Obszar | Gdzie | Zatwierdzanie |
|---|---|---|
| Ogólne `execute_command` (rzadkie, 7%) | kod, faza 5c | zawsze w panelu |
| git odczyt (`status`, `diff`, `log`) | skill, faza 8 (można wcześniej) | bez pytania (`vibecoder-confirm: false`, zaufany hash) |
| git zapis: `init` (nowe repo), `add`, `commit` (Conventional Commits), `push` | skill | `init`/`add` w panelu; `commit` i `push` zawsze w panelu |
| python: uruchamianie skryptów z venv, venv, `pip install -r`, upgrade pip | skill: uruchamianie przez zadanie z `venv\Scripts\python.exe`; tworzenie venv i instalacje zwracają gotową komendę dla użytkownika | uruchamianie w panelu; reszta tylko informacja |
| pytest | użytkownik w swoim terminalu (zasada 7); skill podaje komendę | brak |
| npm: nowy projekt, instalacja, `run build` | skill (instalacje jako zadania z dłuższym timeoutem, zasada 3) | w panelu |
| npm: `run dev` | użytkownik w terminalu (zasada 8 i brak procesów w tle); skill zwraca komendę | brak |
| docker | wyłącznie skill z listą dozwolonych podkomend (sekcja 4.5a) | w panelu; `config`, `ps`, `logs --tail` bez pytania |
| proxy i zmienne środowiskowe | skill `env-info` (odczyt z maskowaniem) | bez pytania dla odczytu |

Wniosek: `python`, `venv`, `activate`, `call`, `set` nie wymagają obsługi powłoki; zamiast aktywacji wywołujemy interpreter z venv. Procesy działające długo (`npm run dev`, serwery) zostają po stronie użytkownika; mechanizm zadań w tle (P6) wraca do rozważenia po fazie 8, jeśli okaże się potrzebny.

### 4.5a Docker: skill, nie `execute_command`
Podstawa: projekt referencyjny CSM (`docker-compose.yml`, `docker-compose.dev.yml`, `.env.example`, `start-dev.bat`, `start-prod.sh`). Docker daje de facto dostęp administratora do hosta, więc `docker` nie jest dozwolony w `execute_command` (blokada w `command_policy`); obsługuje go wyłącznie skill.

Ryzyka:
| # | Ryzyko | Środek |
|---|---|---|
| D1 | Montowanie (`-v C:\:/host`, bind mount w pliku compose) omija path guard i tryb `ro` | brak `docker run`, `-v`, `--mount` w poleceniu; `compose config` przed `up`/`build` wykrywa mounty poza rootami i pokazuje je w panelu |
| D2 | `privileged: true`, `security_opt: seccomp:unconfined`, `network_mode: host` (jest w dev CSM: mongo-express i frontend) | panel wyróżnia te ustawienia przy `up`/`build`; edycja plików compose to zadanie `sensitive` |
| D3 | `down -v` kasuje wolumeny (u CSM `mongo_data`, czyli dane bazy) | osobne ostrzeżenie w panelu, zawsze ręczne zatwierdzenie |
| D4 | `compose config` wstawia wartości z `.env`, więc ujawnia hasła | wynik maskowany (jak w odczycie plików); pełne wartości tylko przez `secret_reveal` |
| D5 | Porty 8100-8102 (MCP, panel) wystawione przez kontener lub przekierowane | `config` sprawdzany pod kątem tych portów (T3) |

Lista podkomend skilla `docker` (wstępna, do potwierdzenia przy fazie 8; plik compose wskazywany jawnie jak w Twoich komendach `-f docker-compose.dev.yml`):
- Bez pytania (odczyt): `compose config` (maskowany), `compose ps`, `compose logs --tail N` (bez `-f`).
- W panelu: `compose build`, `compose up -d`, `compose restart`, `compose cp` (tylko ścieżki wewnątrz rootów `rw`), `compose exec <usługa> <komenda>` z listy (`python -m unittest ...`, `npm install`, `npm run build`).
- Ostrzeżenie dodatkowe: `compose down` (z `-v` osobno, patrz D3), `compose up --build`.
- Zablokowane: `docker run`, `--privileged`, `-v`, `--mount`, `exec -it` (brak TTY), `compose exec` z dowolnym napisem poza listą.
- Komendy bez końca (`up` bez `-d`, `logs -f`): skill zwraca gotową komendę dla Twojego terminala (P6).
- Drugi projekt referencyjny (FastAPI + PostgreSQL) przyda się dopiero w fazie 8 do doprecyzowania podkomend bazy (`alembic`, `pg_dump`, `psql`); nie blokuje fazy 5.

### 4.6 Testy automatyczne (`pytest` uruchamia użytkownik)
- `command_policy`: tabela przypadków (zablokowane, dozwolone, ostrzeżenie), w tym obejścia składniowe: wielkość liter, `.exe`, cudzysłowy, `--port=8102`, `127.0.0.1:8102/api`, `[::1]:8102`, `localhost.`, zapis dziesiętny/hex IP, `cd` z `/d`, ścieżki ze spacjami.
- Tłumaczenie `cd`: poprawny wzorzec, `cd` poza rootem, `cd` do roota `ro`, `cd a && b && c`, `cd a; b`.
- Tokenizer: metaznaki, cudzysłowy, nowe linie, Unicode (RLO), bardzo długie wejścia.
- Brak tokenu panelu w `clean_env()`, configu, `tasks.db`, odpowiedziach narzędzi.
- Maskowanie sekretów: `.env` z `*_PASSWORD`, `*_TOKEN`, `://user:pass@host`; `search_code` po `PASSWORD` nie zwraca wartości; `.env.example` nie jest maskowany; `masked=true` i licznik; `secret_reveal` wymaga zatwierdzenia.
- `command_env_passthrough`: przekazywane tylko istniejące zmienne z listy; nazwa pasująca do `TOKEN|SECRET|PASSWORD|KEY` odrzucona przy zapisie listy; zmienne tokenu panelu/MCP nigdy nie trafiają do procesu potomnego.
- Blokada `.bat`/`.cmd`/`.ps1` i `docker` w `execute_command` (z komunikatem "użyj skilla").
- Path guard (lista z 2.4), TOCTOU zapisu, atomowość, rollback `edit_file` przy błędzie w jednej z wielu zmian.
- Roots: walidacja (profil użytkownika, katalog główny dysku, zagnieżdżenia), tryb `ro` blokuje zapis, migracja ze starego configu, `request_root` bez efektu przed zatwierdzeniem.
- Integracja: `edit_file` -> `awaiting_approval` -> `allow` -> zapis; `deny` -> plik nietknięty; `cancel` w oczekiwaniu; zmiana pliku po wygenerowaniu diffu -> `failed`.
- Panel: render każdego `kind`, brak "Ufaj" dla `command`/`sensitive`/`root_change`, `/api/roots` wymaga sesji i `Origin`/`Host`.
- Migracja `tasks.db`: stara baza otwiera się, stare zadania czytelne. Regresja `exit_code=None`. Komenda czekająca na wejście kończy się timeoutem, nie zawieszeniem.

### 4.7 Testy ręczne (z harnessa)
1. Dodanie roota `rw` i `ro` w panelu; model widzi je w `list_roots`; zapis do `ro` odrzucony.
2. `edit_file` z wieloma plikami: jeden diff, decyzja po dłuższej przerwie (> 8 s), zapis lub odrzucenie.
3. `execute_command` `git status` w panelu; `cd x && git status` przetłumaczone na `cwd`.
4. `curl http://127.0.0.1:8102/api/tasks`: odrzucone przez policy; po tymczasowym wyłączeniu guardu 401 (potwierdzenie T1).
5. `python -m http.server 8102` przy działającym panelu: zablokowane.
6. Start sklr przy zajętym 8102: brak linku z tokenem, fallback do okna.
7. Token jednorazowy: ponowne użycie linku nie działa; historia przeglądarki nie daje dostępu.
8. Edycja `panel/app.py` jako `sensitive`: plakietka, brak "Ufaj".

## 5. Kolejność prac (każda podfaza = osobna akceptacja)
1. **5.0** Porządki: `exit_code=None`, `kind/title/payload` + migracja `tasks.db`, uogólnienie panelu i `dialog_adapter`; testy regresji.
2. **5.1** `roots`: `ConfigStore` (lock wspólny z `TrustStore`), `RootsRegistry`, migracja configu, `/api/roots` i zakładka w panelu, `list_roots`, `request_root`; testy.
3. **5a** Path guard + `list_directory`, `read_file`, `read_files_batch`, `search_code`; test ręczny.
4. **5b** `edit_file` (lista zmian, diff), `create_file`, `manage_folder`; test ręczny zatwierdzania.
5. **5c** Tokenizer + `command_policy` + `execute_command` (+ token jednorazowy P1, jeśli zaakceptowany); testy 4.6, test ręczny 4.7.
6. **5e** Domknięcie: CURRENT.md, ADR 0003 (decyzje, ryzyka rezydualne T4/T11, rola skills), README, backlog, checkpoint.
Po każdej podfazie aktualizacja statusu tego pliku.

Poza zakresem 5: zewnętrzne MCP (faza 6, razem z oknem konfiguracji), `workspace_overview` i `save_progress` (faza 7), skills startowe `git`, `python-env`, `npm`, `docker`, `env-info`, `excel-analysis`, `api-testing` (faza 8), usuwanie plików (P4), procesy w tle (P6).

## 6. Decyzje

Zatwierdzone w rozmowie: zatwierdzanie bez limitu czasu (dla zapisów zawsze `task_id`), lista zmian w jednym zadaniu, P11 (tłumaczenie `cd` -> `cwd`), model `roots` z `request_root`, wariant mieszany (`execute_command` + skills), odpadnięcie P2 i `switch_workspace`.

Do akceptacji (zalecenia w nawiasach):
- P1. Token panelu jednorazowy (tak). Skutek: ponowne użycie linku nie działa; nowa karta wymaga ponownego wygenerowania linku (komenda w konsoli lub restart).
- P3. Zastąpione przez P14.
- P14 (ZATWIERDZONE). Maskowanie sekretów w odczycie plików, ujawnienie pełnej wartości tylko przez zadanie `secret_reveal` w panelu.
- P15 (ZATWIERDZONE). `command_env_passthrough` domyślnie z zmiennymi proxy i certyfikatów, edytowalne w panelu; przekazywane tylko istniejące zmienne.
- P4. Usuwanie plików w fazie 5 (poza zakresem).
- P5. Wymuszanie Conventional Commits w kodzie `command_policy` (nie, robi to skill `git`).
- P6. Procesy w tle (poza fazą 5; `npm run dev` i serwery uruchamia użytkownik).
- P8. Blokada `python -c` / `node -e` (tak).
- P10. Domyślna lista `sensitive`: `panel/`, `command_policy.py`, `config.json`, `tasks.db*`, `.git/`, `main.py`, `state.py` (potwierdzić lub zmienić).
- P12. Czy `request_root` w ogóle wprowadzać w 5.1, czy dodawanie roots wyłącznie z panelu (zalecane: wprowadzić, ale jako zadanie do zatwierdzenia).
- P13. Maksymalny timeout komend: zostaje 300 s; dla instalacji npm skill może mieć własny, dłuższy limit (np. 900 s) (do ustalenia przy fazie 8).
- Rozstrzygnięcia 3.1-3.3 (`offer_trust`, `exit_code`, `gui_simple`): potwierdzić.

## Plan podfazy 5.1: `roots` (ZAAKCEPTOWANY)

Przypadek użycia: starsze projekty z osobnymi folderami frontendu i backendu, bez wspólnego rodzica. Każdy folder to osobny root (np. `frontend`, `backend`, oba `rw`); narzędzia przyjmują `root`, domyślny jest tylko wartością pomijaną. Przełączanie niepotrzebne.

Schemat: `config.json` dostaje `"roots": null` (null = jeszcze niezmigrowane, `[]` = zmigrowane i puste). Wpis: `{alias, path, mode: "rw"|"ro", description, default}`. Alias: `[a-z0-9][a-z0-9_-]{0,31}`; limit 20 roots; ścieżka zapisywana po `resolve()`.

Migracja jednorazowa przy starcie: `workspace.path` -> root `rw` domyślny; każda pozycja `references` -> root `ro`; aliasy czyszczone i dedupowane; wpisy nieprawidłowe pomijane (liczba w konsoli). Stare klucze `workspace` i `references` zostają nietknięte do fazy 6 (wycofanie, istniejące testy bez zmian).

Walidacja (przy zgłoszeniu i ponownie tuż przed wykonaniem po zatwierdzeniu):
- odrzucane zawsze: nieistniejąca ścieżka lub plik, UNC i prefiks `\\?\`, root będący strefą zakazaną, zawierający ją lub leżący w niej (katalog główny dysku, `C:\Users`, katalog domowy, `%APPDATA%`, `%LOCALAPPDATA%` z profilami przeglądarek, `%PROGRAMDATA%`, `%SystemRoot%`, `Program Files*`, `~/.ssh`, `~/.aws`, `~/.azure`, `~/.kube`, `~/.gnupg`);
- odrzucane tylko dla `rw`: katalog centralny skills oraz jego rodzice i dzieci (dla `ro` dozwolony);
- zagnieżdżanie: `ro` w `rw` dozwolone (chroni podfolder), `rw` w `ro` błąd, duplikat ścieżki (bez wielkości liter) lub aliasu błąd.

Decyzje: skills projektowe tylko z domyślnego roota (flaga `skills` per root w fazie 6); domyślny root może być `ro` (zapisy zwrócą błąd "folder tylko do odczytu"); `request_root` może prosić o `rw` (tryb pokazany wyraźnie w panelu); UNC odrzucane w całości.

Pliki. Nowe: `config_store.py` (dane, wspólny `RLock`, `save()` atomowy, `update(mutator)` z wycofaniem przy błędzie), `roots.py` (model, alias, strefy zakazane, `validate_root`, `migrate_legacy`), `roots_registry.py` (stan thread-safe, callback po zmianie domyślnego roota wołany poza lockiem), `server/roots_tools.py` (`list_roots`, `request_root`). Zmieniane: `state.py` (`"roots": None`), `skills_engine/trust.py` (opcjonalny `lock`, API wstecznie zgodne), `panel/app.py` (endpointy; bez parametru `roots` zwracają 404), `panel/static/{index.html,app.js,style.css}` (zakładka "Foldery"), `main.py` (wiązanie, getter telemetrii z aliasem domyślnego roota, usunięcie `PROJECT_ROOT`/`WORKSPACE_NAME`, 2 nowe narzędzia).

Endpointy panelu (zmieniające stan tylko `POST`, ta sama ochrona sesji, `Origin`, `Host`): `GET /api/roots`, `POST /api/roots`, `POST /api/roots/{alias}/update`, `.../delete`, `.../default`. Kody: 400 (walidacja), 404, 409 (duplikat); `GET` oznacza wpis `valid=false` z powodem, gdy katalog zniknął.

Narzędzia MCP: `list_roots()` (alias, tryb, opis, ścieżka, domyślny, dostępny); `request_root(path, mode, alias, description)` waliduje od razu (błąd bez zadania), potem zadanie `root_change` z `sensitive=true` (bez "Ufaj"), root dodawany dopiero po zatwierdzeniu w panelu.

Testy: `test_config_store.py` (atomowość, wspólny lock, test obciążeniowy `trust` + `update`), `test_roots.py` (tabela walidacji, junction, aliasy, zagnieżdżanie, domyślny root), `test_roots_migration.py`, `test_roots_tools.py`, `test_panel_roots.py`, rozszerzenie `test_panel_static.py`. Testy ręczne: dodanie roota `rw` i `ro` w panelu; odrzucenie `C:\`, katalogu domowego, `%APPDATA%`; `list_roots` z harnessa; `request_root` (plakietka "wrażliwe", odrzucenie nic nie dodaje, zatwierdzenie dodaje); zmiana domyślnego roota odświeża skills projektowe. Przed pierwszym startem kopia `config.json`.

Kolejność: 1) `ConfigStore` + lock w `TrustStore`; 2) `roots.py`; 3) rejestr i migracja; 4) API i zakładka; 5) narzędzia MCP i `main.py`; 6) testy ręczne.

Status kroków: 1) ZROBIONY (`config_store.py`, parametr `lock` w `TrustStore`, `tests/test_config_store.py`; czeka na uruchomienie testów); 2) ZROBIONY (`roots.py`: alias, model `Root`, strefy zakazane, `validate_root`, `check_new_root`, `find_root_for`, `migrate_legacy`; `tests/test_roots.py`, `tests/test_roots_migration.py`; czeka na uruchomienie testów); 3) ZROBIONY (`roots_registry.py`: lista, `resolve`, `add`/`update`/`remove`/`set_default`, `migrate`, callback zmiany domyślnego roota; `state.py` dostał `"roots": None`; `tests/test_roots_registry.py`; czeka na uruchomienie testów); 4) ZROBIONY (`panel/app.py`: parametr `roots`, `/api/roots*`; wyjątki `RootNotFound` i `RootConflict` w `roots.py`; zakładka "Foldery" w `panel/static`; `tests/test_panel_roots.py`, rozszerzony `test_panel_static.py`; testy przechodzą; odświeżanie zakładki przy wejściu i po akcji, bez pollingu); 5) ZROBIONY (`server/roots_tools.py`: `list_roots`, `request_root` -> zadanie `root_change` sensitive; `RootsRegistry.check_add`; `main.py`: `ConfigStore`, `RootsRegistry`, migracja z kopią `config.json.bak`, skills projektowe z domyślnego roota, telemetria z aliasem, panel z `roots`; `tests/test_roots_tools.py`; testy przechodzą, zakładka "Foldery" widoczna w panelu); 6) ZROBIONY (testy ręczne, patrz "Wyniki 5.1").
Testy kroków 1-3 przechodzą (potwierdzone przez użytkownika): `test_config_store.py`, `test_state.py`, `test_roots.py`, `test_roots_migration.py`, `test_roots_registry.py`. Kroki 1-3 nie są jeszcze podłączone w `main.py` (to krok 5), więc działający sklr jest bez zmian poza ponawianiem zapisu configu w `state.save_config`.
Dodatkowo w kroku 1: `state.save_config` ponawia `os.replace` przy `PermissionError` (Windows, chwilowa blokada pliku) i sprząta plik `.tmp` (wykryte testem współbieżnym; testy w `tests/test_state.py`).

## Plan podfazy 5a: path guard, odczyt i search (ZAAKCEPTOWANY)

Pliki. Nowe: `server/path_guard.py` (`resolve_path(registry, path, root, write, allow_empty)` -> `ResolvedPath(root, owner, path, relative)`; błąd `PathError`), `server/secrets_mask.py` (P14), `server/file_tools.py` (`list_directory`, `read_file`, `read_files_batch`, `search_code`; ewentualnie `server/search_tools.py`). Zmieniany: `main.py` (rejestracja 4 narzędzi).

Path guard: ścieżka względna liczona od roota, bezwzględna tylko wewnątrz któregoś roota (bez parametru `root` root wybierany po ścieżce). Odrzucane przed `resolve()`: znaki sterujące, UNC i `\\?\`, strumienie NTFS, nazwy zarezerwowane (`CON`, `NUL`, `COM1-9`, `LPT1-9`, także z rozszerzeniem), końcowe kropki i spacje, znaki `<>"|?*`, ścieżka zaczynająca się od ukośnika bez dysku, ścieżka z dyskiem bez ukośnika. Po `resolve()` (rozwija 8.3, symlinki, junctiony) sprawdzenie `is_relative_to` roota; tryb sprawdzany dla najgłębszego roota (`ro` w `rw` blokuje zapis).

Narzędzia (parametr `root`: brak, pusty napis i `false` = domyślny; liczby przycinane do zakresu, nie błąd):
- `list_directory(subpath, root)`: jeden poziom, katalogi z `/`, limit 500 pozycji.
- `read_file(filepath, start_line, end_line, max_chars=12000, root, line_numbers=false)`: zastępuje `read_file` i `read_file_lines`; zwraca `eol`, `total_lines`, `truncated`; `max_chars` 1000-80000; UTF-8 z BOM, potem cp1250; binarne odrzucane; limit 5 MB.
- `read_files_batch(requests, max_total_chars=24000)`: do 10 plików, wspólny budżet, błąd jednego nie psuje reszty.
- `search_code(query, subpath, case_sensitive, context_lines=0, max_matches=30, max_total_chars=24000, root)`: zastępuje `search_code` i `search_code_context`; dosłowne dopasowanie; `context_lines` 0-30; `subpath` wskazujący plik = szukanie w pliku; `root="*"` = wszystkie roots; budżet czasu 6 s z `truncated`; pomijane `.git`, `node_modules`, `venv`, `.venv`, `__pycache__`, `dist`, `build`, `coverage`, `.pytest_cache`, pliki binarne i większe niż 1 MB; symlinki i junctiony nie są przeszukiwane; wyszukiwanie działa na tekście po zamaskowaniu (brak wyroczni wartości sekretu). Bez ripgrepa w 5a.

Maskowanie (P14): pliki `.env`, `.env.*` (poza `.env.example`, `.env.sample`, `.env.template`), `pip.ini`, `pip.conf`, `.npmrc`, `*.pem`, `*.key`; `*.pfx` i `*.p12` odczyt zablokowany. Wartości kluczy pasujących do `TOKEN|PASSWORD|PASSWD|SECRET|KEY|CREDENTIAL|AUTH` zamieniane na `***`, hasło w `://user:pass@` na `***`, bloki `PRIVATE KEY` zastąpione znacznikiem; odpowiedź ma `masked` i `masked_count`. To heurystyka, nie granica bezpieczeństwa. `secret_reveal` odłożone do 5b; lista `sensitive` (P10) potwierdzana w 5b.

Kolejność: A) `path_guard` + `secrets_mask` z testami; B) `list_directory`, `read_file`, `read_files_batch`; C) `search_code`, rejestracja w `main.py`, testy ręczne.

Status 5a: **ZAMKNIĘTA; paczki A/B/C zrealizowane, testy automatyczne i wykonane testy ręczne zaliczone.** Użytkownik potwierdził wykonanie commitu 5a. Błąd powiadomień panelu w tle naprawiony i zweryfikowany przed 5b; commit poprawki panelu niepotwierdzony. Plan 5b poniżej zaakceptowany (w tym secret_reveal i P10); implementacja nie rozpoczęta. Następny krok: osobny plan paczki A do akceptacji.

Uwagi path guarda rozwiązane po osobnej akceptacji: absolutna ścieżka bez jawnego aliasu wybiera root po położeniu bez wymagania roota domyślnego; usunięto `path.strip()`, aby końcowe spacje i skrajne znaki sterujące podlegały walidacji. Dodano testy regresji, zachowano ochronę najgłębszego roota `ro`.

## Plan podfazy 5b: zapisy i ujawnianie sekretów (ZAAKCEPTOWANY)

Status: użytkownik zaakceptował podział A-D, rozszerzenie P10 oraz wynik secret_reveal wyłącznie w pamięci. **A (A1/A2), B1 i B2 zrealizowane i zweryfikowane testami przez użytkownika. D1 (rejestracja 3 zapisów) zaimplementowana przed C, oczekuje na testy i restart; C i reszta D pozostają otwarte.** Następny krok: weryfikacja D1 oraz testy ręczne przed nowym chatem z bezpośrednim sklr. Ogólna akceptacja 5b nie znosi zasady planu i akceptacji przed każdą zmianą. Szczegóły: Wyniki B1/B2 oraz D1 poniżej.

### Decyzje zatwierdzone
- Operacje zmieniające stan zawsze przez kolejkę i panel, natychmiastowy task_id/awaiting_approval, bez oczekiwania na decyzję, bez Ufaj. Model czeka na wiadomość użytkownika; po błędzie :ping przed ponowieniem operacji tworzącej zadanie sprawdza list_tasks.
- P10 sensitive: `panel/`, `command_policy.py`, `config.json`, `tasks.db*`, `.git/`, `main.py`, `state.py` oraz pliki sekretów rozpoznawane w 5a. Dodatkowo w repo serwera chronione: `server/`, `tasks/`, `roots.py`, `roots_registry.py`, `config_store.py`. Wzorce konfigurowalne; doprecyzować rozpoznawanie repo serwera i zakotwiczenie wzorców w planie paczki A, nie rozszerzać po cichu na wszystkie katalogi o tej samej nazwie.
- Narzędzie `secret_reveal`: wynik tylko w pamięci procesu, dostępny po zatwierdzeniu przez get_task, niedostępny po restarcie. Surowy sekret nie trafia do SQLite (stdout/payload/diff/error), telemetrii ani logów. Przed zatwierdzeniem żadnego surowego sekretu w danych zadania. Potrzebne niewielkie rozszerzenie obsługi wyników zadań; limity i sprzątanie pamięci doprecyzować w paczce C. Zakresy/limity jak odczyt tekstowy; binarne .pfx/.p12 pozostają zablokowane.
- Wiele plików: staging i os.replace per plik, wspólna blokada zapisów, rollback przy obsługiwanym błędzie. **Nie obiecujemy pełnej atomowości przy awarii procesu/systemu ani wobec zewnętrznych zapisów**; system plików nie ma jednej atomowej operacji na wielu plikach. Ograniczenie zaakceptowane, opisać także przy domknięciu/ADR.

### Paczka A: przygotowanie zmian i zapis
Nowe: `server/file_changes.py`, `server/file_transaction.py` i ich testy (np. `tests/test_file_changes.py`, `tests/test_file_transaction.py`).
- Dokładne old_text, błąd przy 0 lub więcej niż 1 dopasowaniu z liczbą; przy 0 najbliższy fragment (zamaskowany, bez wycieku). Kilka zmian w jednym pliku wykonywanych kolejno.
- Zachowanie kodowania UTF-8/BOM/cp1250 i końców linii, diff pogrupowany root/plik, hashe stanu początkowego. Szczegóły normalizacji wstawianej treści i limitów do przedstawienia przed implementacją.
- Ponowny path guard/uprawnienia/hashes po zatwierdzeniu i tuż przed zapisem, brak nadpisania zmiany zewnętrznej. Walidacja wszystkich zmian przed publikacją, pliki tymczasowe, os.replace, wspólna blokada i rollback/cleanup.
- Testy: dokładność i kolejność zmian, BOM/EOL/kodowania, nested ro, sensitive, zmiana pliku/roota między planem a wykonaniem, rollback przy błędzie częściowym i sprzątanie plików tymczasowych.

### Wyniki paczki A 5b (ZREALIZOWANA)
- A1: nowe `server/file_changes.py` i `tests/test_file_changes.py`. Dokładne old_text bez normalizacji, liczone również nakładające się wystąpienia; 0/>1 to błąd, zmiany tego samego rozwiązanego celu wykonywane kolejno. Przypięte ścieżki wejściowe, alias/root/owner i bajty początkowe/wynikowe pozostają w prywatnym planie; wyłącznie preview() jest publicznym podglądem. Nie serializować planu przez asdict() do SQLite/logów/telemetrii.
- UTF-8/BOM/cp1250 zachowane, kodowanie ścisłe bez automatycznej konwersji. old_text dosłowny; new_text dostosowuje EOL do jednolitego LF/CRLF/CR pliku, przy mixed/none pozostaje dosłowny. Nowy plik UTF-8 bez BOM; overwrite zachowuje format istniejącego. Brak niejawnego tworzenia rodziców; binarne/UTF-16/.pfx/.p12 odrzucane.
- Limity: 50 wpisów, 10 różnych rozwiązywanych celów, 5 MiB na plik przed/po, 20 MiB sumy przed i osobno po oraz treści żądania. Maskowany diff root/plik do 200 KiB UTF-8, kosztowne generowanie oznaczone jako skrócone. Maskowanie pełnych tekstów przed diffem/oknem/skróceniem, komunikat o ukrytej zmianie nawet przy identycznych maskach; brak publicznych hashy plików sekretów. Maskowanie nadal heurystyką 5a.
- P10: konfigurowalny obiekt SensitivePolicy (podłączenie do konfiguracji startowej później). Wzorce ogólne zakotwiczone względem roota, katalog i potomkowie; dodatkowe server/tasks/roots/config_store tylko względem rzeczywistego repo działającego serwera ustalonego z położenia modułu. Ochrona repo serwera działa także z rootem rodzica/podkatalogu, nie rozszerza się po nazwie na inne repo.
- Diagnostyka 0 trafień: ograniczony ranking zamaskowanych fragmentów zwykłych plików; pliki sekretów pokazują początek pełnego zamaskowanego tekstu bez rankingu wartości sekretu, do 20 linii/2000 znaków, jawne CR/LF/TAB/control/bidi. Poprawiono podgląd PEM: jednoliniowe old_text nie ogranicza już okna do samego nagłówka; dodano regresje limitów.
- A2: nowe `server/file_transaction.py` i `tests/test_file_transaction.py`. Wspólny procesowy WRITE_LOCK, następnie wstrzyknięty reentrantny ConfigStore.lock; callback verify i ponowne guard/root/owner/rw/hash/tożsamość/tryb pliku przy wykonaniu, po stagingu i przed każdym os.replace. Staging replacement/backup w katalogu celu: mkstemp, flush/fsync, zamknięte uchwyty, kontrola tożsamości rodzica/temp i hashy. Kolejka/panel nie były zmieniane; moduł wykonywać wyłącznie po decyzji.
- Rollback odwrotny przy obsługiwanym błędzie/anulowaniu, dla create usuwa wyłącznie własny opublikowany cel. Nie nadpisuje wykrytej zewnętrznej zmiany; niepełny rollback i cleanup raportowane jawnie. Wynik bez surowych treści/wyjątków/hashy wskazuje committed/cancelled/rollback_complete/cleanup_complete/published_count; committed może być true przy błędzie cleanup, nie ponawiać w ciemno. Cleanup dotyczy własnych rozpoznanych temp, bez kasowania cudzych lub pozostałości po restarcie na podstawie nazwy.
- Ograniczenia: os.replace atomowy per plik, NIE pełna atomowość wieloplikowa przy awarii procesu/systemu lub zewnętrznych zapisach; TOCTOU pozostaje możliwe. Nie gwarantujemy zachowania wszystkich metadanych NTFS/ACL. Temp/backup zawiera rzeczywiste bajty (także sekretów) podczas wykonania; brak gwarancji sprzątania po awarii procesu/systemu, wykryty błąd sprzątania jest jawny.
- Użytkownik potwierdził sukces: `pytest tests/test_file_transaction.py tests/test_file_changes.py tests/test_path_guard.py tests/test_secrets_mask.py tests/test_file_tools.py` (ostatnia poprzedzająca sukces próba: 223 collected, 218 passed/4 skipped/1 failed w przygotowaniu testu repoint). Poprawka wyłącznie testu: usunięcie istniejącego aliasu other przed użyciem tego katalogu jako app; użytkownik po powtórzeniu potwierdził sukces, bez pełnego logu końcowego. Testy obejmują konflikty, nested ro/rw, multi-root, formaty, anulowanie, wspólną blokadę, błędy staging/replace/rollback/cleanup, sekrety i junction/symlink.
- Agent nie uruchamiał pytest/serwera/venv/pip. Po każdym zapisie wykonano wymagane search_code backslash+cudzysłów w katalogu, bez trafień w nowych plikach. main.py/panel/tasks/config bez zmian, brak rejestracji zapisów i testów ręcznych 5b. Commit paczki A oraz wcześniejszej poprawki panelu niepotwierdzone.

### Krok T 5b: wygaszenie tkintera (ZREALIZOWANY)
- Decyzja użytkownika: w sklr zatwierdzenia wyłącznie przez panel web; tkinter (używany przed panelem) wygaszony. Bez panelu sklr nie działa (wariant A).
- Zmiany: `main.py` (usunięte `enable_dialog_approvals`, `APPROVAL_UI`, import dialogu; brak panelu lub `panel.enabled=false` -> `PanelRequiredError`, komunikat `BŁĄD:`, `TASK_MANAGER.shutdown()`, `exit 1`), `state.py` (usunięte `approval_ui` i `approval_timeout_s` z wartości domyślnych; stare klucze w lokalnym `config.json` ignorowane, config nietknięty), `README.md`, testy: `test_state.py`, `test_roots_tools.py` (test przemianowany na natychmiastowe odrzucenie jako bezpiecznik), `test_phase5_tasks.py` (bez testów okna).
- Do usunięcia przez użytkownika (git rm): `gui_simple.py`, `tasks/dialog_adapter.py`, `tests/test_dialog_adapter.py`. Po usunięciu zniknął mechanizm auto-odrzucania i limit 10000 znaków okna; panel nie używał `dialog_adapter`.
- Wynik: `pytest` w całości 724 passed, 7 skipped (potwierdził użytkownik). Docs: sekcja 3.3 oznaczona jako wycofana, CURRENT, ADR 0002. `request_root_impl` zostawia gałąź "panel niedostępny" jako bezpiecznik (przy wariancie A praktycznie martwa). Zapis `file_edit`/`file_write`/`folder` nie wymaga już zmiany `AUTO_DENY_KINDS` (pytanie 1 z planu B zamknięte).
- Zasada kontynuacji: nie przywracać okna zatwierdzeń; błędny start panelu = błąd startu serwera.

### Paczka B: narzędzia i kolejka (B1/B2 ZREALIZOWANE, testy potwierdzone)
B1 i B2 ukończone; poniżej zachowany uzgodniony plan. Rejestracja przeniesiona do D1 przed paczką C, aby umożliwić bezpośrednią pracę nad sklr przez sklr.
Uzgodnione w rozmowie, przed implementacją wymaga potwierdzenia jako B1 i B2 osobno:
- Kolejność: B1 (`edit_file`, `create_file`) -> B2 (`manage_folder`), każdy krok z osobnym planem plików/testów do akceptacji.
- Przepływ: walidacja i przygotowanie planu (`prepare_edit`/`prepare_write`) przed utworzeniem zadania; błąd = brak zadania. `manager.submit(..., needs_approval=True)`, `offer_trust=False`, natychmiastowy `task_id`/`awaiting_approval` bez `wait`, podpowiedź "poczekaj na wiadomość użytkownika, po `: ping` sprawdź list_tasks". `verify` = `FileTransaction.verify`, `executor` = `FileTransaction.execute(cancel_event)`. Jeden współdzielony `FileTransaction(registry, ConfigStore.lock)`.
- Rodzaje zadań: `file_edit`, `file_write`, `folder`; `sensitive` z planu (P10/sekrety/repo serwera). `payload`: root/ścieżki, maskowany `diff` z `preview()`, flagi, bez surowych bajtów i hashy sekretów. Tytuł krótki.
- **`description`** (decyzja użytkownika: TAK): opcjonalny parametr w `edit_file`, `create_file`, `manage_folder`; tekst do ok. 500 znaków, odrzucane znaki sterujące i bidi; trafia do `payload.description`, panel pokazuje jako "opis od modelu (niezweryfikowany)". Jawne escapowanie znaków w panelu w D.
- Adaptacja wyniku transakcji do menedżera (executor nigdy nie rzuca; stałe bezpieczne komunikaty): sukces -> `exit_code=0` i krótkie podsumowanie w `stdout` bez treści; anulowanie -> `cancelled=True`; konflikt/błąd -> `started=False` + komunikat; niepełny rollback/cleanup -> `failed` z jawnym komunikatem i informacją `committed` w `stdout`. Uwaga: `_finish` zapisuje `error` do SQLite, a `TaskManager` loguje wyjątki, więc nic wrażliwego w wyjątkach.
- **`manage_folder`** (decyzja użytkownika: TAK): `create` (nowy katalog, rodzic musi istnieć, `os.mkdir`, istniejący cel = błąd) i `rename` (`new_name` to sama nazwa bez separatorów/`.`/`..`/składni Windows, ten sam rodzic, cel nie istnieje, źródło zwykły katalog nie link/junction). Zakaz create/rename dla rootów i ich przodków (obejście roots), brak przenoszenia i usuwania. Ponowny guard `write=True` przy wykonaniu. Sensitive wg P10 dla źródła i celu oraz gdy źródło leży pod/nad chronionym wzorcem (w tym katalogi repo serwera).
- Testy B: błędne argumenty bez zadania; poprawne zadanie (`kind`, `sensitive`, `payload`, `title`, `offer_trust=False`, natychmiastowy `task_id`); brak surowych treści/hashy sekretów w zadaniu i SQLite; wykonanie po `allow` (wiele rootów); `deny`/`cancel`; konflikt po decyzji -> `failed`; anulowanie w toku; pełna kolejka; adaptacja niepełnego rollbacku; `description` (limit, znaki sterujące); `manage_folder` (create/rename, istniejący cel, root/przodek roota, nazwa ze ścieżką, junction, `ro`, sensitive).

### Wyniki B1 (ZREALIZOWANE)
- Nowe: `server/edit_tools.py` (`edit_file_impl`, `create_file_impl`, `check_description`) i `tests/test_edit_tools.py`. Użytkownik potwierdził zielone testy, restart sklr i commity A/T/poprawki panelu. Narzędzia jeszcze niezarejestrowane w `main.py` (D).
- Przepływ jak `request_root`: `prepare_edit`/`prepare_write` przed zgłoszeniem (błąd lub brak zmian = brak zadania), `submit(needs_approval=True, verify=transaction.verify)`, natychmiastowy `task_id`, `offer_trust=False`, `timeout=10`, `sensitive` z planu. Jeden współdzielony `FileTransaction` wstrzykiwany z zewnątrz.
- Payload: `root` (None przy wielu rootach), `paths` (`alias:ścieżka`), `diff` (jeden tekst, nagłówki `=== alias: plik [flagi] ===`), `files` (metadane bez diffu), `truncated`, `description`. Bez treści żądania, bajtów i hashy (także dla zwykłych plików). `approval_reason` zawiera powody sensitive z P10.
- `description`: tekst do 500 znaków po `strip()`; błąd (bez zadania) dla typu innego niż napis oraz znaków Cc, Cf, Zl, Zp, Cs.
- Adapter wyniku: sukces `exit_code=0` i `stdout` bez treści; `cancelled`; konflikt `started=False`; niepełny rollback/cleanup `failed` z `committed=...` w `stdout` (przy zapisanych zmianach komunikat zakazuje ponawiania); wyjątek daje stały komunikat bez `str(exc)`.
- Testy: argumenty i `description`, zadanie bez zapisu przed decyzją, allow/trust/deny/cancel, wiele rootów w jednym zadaniu, konflikty po decyzji, anulowanie w toku, atrapy adaptera, brak sekretów i hashy w zadaniu i SQLite, P10, kolejka, panel niedostępny, skrócony diff.

### Wyniki B2 (ZREALIZOWANE)
- Nowe `server/folder_tools.py` i `tests/test_folder_tools.py`: `manage_folder_impl`, create (`os.mkdir`) i rename (`os.rename`, ten sam rodzic), bez usuwania i przenoszenia. Użytkownik potwierdził zielone testy B2 oraz zmienionego testu skróconego diffu B1.
- Przygotowanie przed submit, natychmiastowy task_id i decyzja w panelu. Payload root/action/path/new_name/target/paths/diff/description; sensitive źródła i celu wg SensitivePolicy oraz przodka repo serwera. Zakaz operacji na rootach i ich przodkach, istniejącego celu, rename linków/junctionów i ścieżek z linkiem pośrednim. Case-only rename na Windows odrzucany.
- Ponowne przygotowanie i porównanie rootów, trybów, ścieżek i tożsamości źródła po decyzji oraz w executorze pod wspólnym WRITE_LOCK i ConfigStore.lock. Cancel przed operacją; po skutecznym mkdir/rename sukces. Bez wielooperacyjnej transakcji/rollbacku; pozostaje okno TOCTOU wobec zmian zewnętrznych.
- Testy obejmują nazwy Windows, roots/ro, linki, decyzje, konflikty, P10, błędy bez wycieku wyjątków do SQLite, wspólną blokadę zapisów i configu.

### D1: wcześniejsza rejestracja zapisów (testy/restart i podstawowe próby ręczne POTWIERDZONE)
- Świadoma zmiana kolejności zatwierdzona przez użytkownika: D1 przed C, następnie testy ręczne i nowy chat z bezpośrednim sklr. Stary vibecoder dostępny awaryjnie; roots projektu użytkownik już przygotował.
- `main.py`: import edit_tools/folder_tools, jeden FILE_TRANSACTION = FileTransaction(ROOTS, CONFIG_STORE.lock), wrappery edit_file/create_file/manage_folder. Bez wait, opisy panelu i reguła list_tasks po błędzie :ping. Po restarcie oczekiwane 18 narzędzi.
- Nowy `tests/test_edit_tools_registration.py`: AST bez importu main.py, dekoratory/opisy, argumenty domyślne i jawne, pojedyncza współdzielona transakcja oraz lock folderów.
- Bez zmian panelu/configu i bez secret_reveal. C oraz reszta D (control/bidi, widok diffów, secret_reveal, domknięcie testów ręcznych) pozostają otwarte. Nie uznawać D1 za pełne domknięcie zabezpieczeń panelu.
- Weryfikacja: użytkownik potwierdził testy D1 i restart; list_external_mcp_tools potwierdził 18 narzędzi. Pierwszy odczyt listy dał :ping, ponowienie odczytu poprawne. Ręczne: #19 create, #20 edit, #21 overwrite, #22 folder create i #23 rename -> done/exit_code=0, stan dysku potwierdzony; #24 -> rejected/deny bez zmiany; #25 -> cancelled, plik nie powstał; #26 -> failed z konfliktem po ręcznej zmianie, zachowano zmianę zewnętrzną. #24-26 zgłoszono jako trzy osobne zadania bez czekania na decyzje (nie test wieloplikowego edit_file).
- Użytkownik podejrzewał sukces #26: API pokazało decision=allow, ale failed i konflikt; diff pozostaje propozycją, nie dowodem zapisu. Brak potwierdzonego błędu panelu. Kontrola escape przez search całego roota była częściowa (time_budget), odczyty pliku potwierdziły treść.
- Zatwierdzona przeprowadzka do nowego chatu ze sklr bezpośrednio http://localhost:8101/mcp; root sklr rw domyślny wskazuje repo, vibecoder ro referencję. Stary MCP odłączony od chatu, lecz dostępny awaryjnie przy uszkodzeniu startu/edycji sklr.
- Następne: odczyt kontekstu w nowym chacie, osobny plan pozostałych prób ro/sekrety/sensitive i multi-root (jeśli roots przygotowane), następnie szczegółowy plan C. Reszta D nadal otwarta. Użytkownik usuwa ręcznie d1_smoke_test.txt oraz d1_smoke_folder_renamed/, bez dodawania do commitu; d1_cancel_test.txt nie powstał. Commity B1/B2/D1 wymagają potwierdzenia.

Oryginalny zapis paczki B (przed uzgodnieniami):
Nowy `server/edit_tools.py` i `tests/test_edit_tools.py`.
- `edit_file(changes, root)`: root per wpis, brak dziedziczy root wywołania, potem domyślny; jedno zadanie i jedna decyzja dla wielu plików/rootów.
- `create_file(filepath, content, overwrite=False, root)`: istniejący plik odrzucany bez overwrite; overwrite pokazuje pełny diff, zastępuje rewrite_full_file.
- `manage_folder(action, path, new_name, root)`: create/rename; brak narzędzi usuwania w fazie 5. Ponowna walidacja źródła/celu po zatwierdzeniu, bez niejawnego nadpisywania.
- Zawsze needs_approval, offer_trust=false; odmowa/anulowanie przed wykonaniem nic nie zmienia. Testy kolejki, sensitive i konfliktów oraz błędów pojedynczych operacji.

### Paczka C: secret_reveal
Nowy `server/secret_tools.py`, `tests/test_secret_tools.py`; kontrolowane rozszerzenie tasks (dokładne pliki po przeglądzie).
- Zawsze sensitive i panel; hash/walidacja po zatwierdzeniu. Wynik tekstowy/zakres tylko w pamięci, get_task po decyzji; brak trwałego sekretu oraz brak ujawnienia przy odmowie/anulowaniu/konflikcie.
- Testy: SQLite i trwałe dane bez sekretu, pamięć/retencja/restart, limity, odrzucenie i anulowanie, zmiana pliku lub dostępu, brak Ufaj i blokada binarnych.

### Aktualizacja C po sesji 2026-10-12: kontrakt i C1/C2
Ta sekcja jest nadrzędna wobec starszych statusów mówiących, że C nie rozpoczęto lub D1 czeka na testy. Commity B1/B2/D1 potwierdzone przez użytkownika; commity obecnego C1/docs niepotwierdzone.

#### Wyniki ręczne przed C
- #27-31 fixture w izolowanych rootach a/b: done/exit0, dysk sprawdzony. #29 diff maskuje atrapy token/hasło (panel potwierdzony); #31 sensitive P10 panel/probe.txt, etykieta wrażliwe i brak Ufaj potwierdzone.
- Negatywne ro edit/create/folder create/rename: błąd bez zadania, dysk nietknięty. Rename tylko guard nieistniejącego źródła, nie pełny test istniejącego katalogu ro.
- #32 jeden edit_file a+ b: done/exit0, oba diffy/rooty w panelu i oba pliki wersja=2. rw+ro: całe przygotowanie odrzucone bez zadania/częściowego zapisu.
- #33 a/.env token dummy-before -> dummy-after, masked/hidden_change/sensitive, done/exit0; user lokalnie potwierdził token zmieniony, hasło dummy-password i PUBLIC_LABEL=smoke bez zmian. read/batch/search maskują wartości, search wartości atrapy daje0. Brak rzeczywistych sekretów. Nie zastępuje audytu DB/WAL/logów ani testów secret_reveal.

#### Kontrakt C (ZAAKCEPTOWANY)
- secret_reveal(filepath, key=None, start_line=None, end_line=None, max_chars=12000, root=None, description=''). Rozłączne selektory: konkretny key ALBO oba jawne końce zakresu fizycznych linii. Brak selektora nie oznacza całego pliku; błąd bez zadania. Pojedyncza wartość albo jawny zakres, bez automatycznego ujawniania sąsiadów.
- Zawsze needs_approval=True, sensitive=True, offer_trust=False, panel i natychmiastowy task_id. Payload tylko ścieżka/root/selektor/limit/opis, bez wartości/diffu/hashy. Panel musi komunikować, że wynik trafi do modelu/klienta MCP; jego retencja jest poza kontrolą sklr.
- Wynik tylko w pamięci, osobno od Task, wyłącznie MCP get_task dołączy secret_result w C3. Panel/list/wait/cancel pozostają bez wartości. Po restarcie trwałe done nie oznacza dostępnego wyniku; nowe ujawnienie wymaga nowej zgody.
- TTL5min od publikacji, maks50 wyników/16MiB, do80000znaków i320000bajtów UTF8 na wynik; bez obcinania sekretu. Wielokrotne pobranie do TTL, bez konsumowania i przedłużania (odporność na utratę odpowiedzi :ping). Pełny magazyn nie usuwa niewygasłych wyników. Okresowe sprzątanie oraz sprawdzenie terminu przy get; shutdown/restart usuwa wyniki. Usuwanie referencji w Pythonie nie gwarantuje zeroizacji RAM/swap/crashdump.
- Kolejność paczek: C1 magazyn/cykl życia -> C2 narzędzie/ekstrakcja -> C3 MCP/panel/telemetria. Przed każdym zapisem plan i akceptacja, ogólna zgoda C nie zwalnia.

#### C1 (ZREALIZOWANE, testy/restart potwierdzone)
- #34 tasks/volatile_results.py i #36 tests/test_volatile_results.py: VolatileResult repr=False, oddzielny magazyn RLock; reserve ukryte/activate TTL, limity treści/metadanych/łącznego kosztu, kopie danych, wątek sprzątający startuje przy aktywacji, callback poza lockiem, sprawdzenie tokena/TTL po callbacku, deny/exception unieważniają. close czyści i join poza lockiem.
- #37 tasks/manager.py: opcjonalnie wstrzyknięty magazyn; typ executora dict|VolatileResult. secret_reveal przy submit wymaga zgody/sensitive/braku trust. Osobne get_volatile_result (get/list/wait/cancel bez sekretów). Finalizacja _cond -> magazyn -> SQLite: reserve, trwałe bezpieczne done, activate pod lockami; błędy usuwają rezerwację i próbują stałego failed, bez tracebacków poufnej ścieżki. Cancel przed publikacją wygrywa; po done cancel no-op. Shutdown _stopped pod _cond, close/join poza _cond, późny wynik odrzucony. Ścieżka secret_reveal nie utrwala surowych dict stdout/stderr/error ani tekstów verify/wyjątków. Zwykłe executory zachowują dotychczasową obsługę.
- #39 tests/test_tasks_manager.py rozszerzone: sukces osobnym kanałem, flagi/zgoda, wrong kind, deny/cancel/timeout, verify/executor exceptions, surowy dict odrzucony, TTL/restart, callback poza _cond/dostęp, full/SQLite done/activate failure, shutdown/późny wynik. Brak wartości w modelach, logicznych danych SQLite i caplog. Pełny audyt trwałych DB/WAL/panelu/telemetrii i dalsze wyścigi pozostają do C3/domknięcia; nie deklarować pełnego audytu na podstawie tych testów.
- User potwierdził zielone pytest tests/test_volatile_results.py oraz później pytest tests/test_volatile_results.py tests/test_tasks_manager.py tests/test_tasks_store.py tests/test_task_tools.py tests/test_phase5_tasks.py tests/test_edit_tools.py tests/test_folder_tools.py; brak pełnego logu/liczb. Restart user wykonany; Session not found na ping/roots ustąpiło po odświeżeniu połączenia, potem ping8101 i5 roots OK. main.py/panel/telemetry/config/Task/schema bez zmian. Zapisy po restarcie jeszcze niesprawdzone przed zadaniem przekazania docs.
- Próba pierwszego edit manager nie utworzyła zadania (old_text LF vs CRLF); po akceptacji powtórzono dopasowania CRLF jako #37. Search tasks/ pełny bez escape; tests/ time_budget po18-19pl., zmienione testy sprawdzono osobno bez trafień. Nie uruchamiano pytest/serwera/git przez agenta.

#### C2 (ZREALIZOWANE, wskazana paczka testów potwierdzona)
- Nowe server/secret_tools.py (#41/#42) i tests/test_secret_tools.py (#43/#44). Każde zadanie done/exit0/1 z 1, pełne odczyty i kontrole po decyzji. Bez main/panel/telemetry/manager/config i bez rejestracji; restart C2 nie był potrzebny.
- Realizacja kontraktu poniżej: prywatny plan repr=False bez snapshot/raw/text/wartości w oczekiwaniu; ekstrakcja key literalna lub zakres fizycznych CRLF/LF/CR bez clamp/truncate. Jawne odrzucenie BOM UTF16/32. Binding root/owner/path/modes i hash/identity/mode; READ ro dozwolone, WRITE_LOCK -> config_lock, wynik VolatileResult z tego samego zweryfikowanego snapshotu. Callback zachowuje tylko binding, sprawdza dostęp bez ponownego odczytu treści.
- Przegląd #41 ujawnił kontynuacje INI z '=' i możliwość zwrotu wyniku przy błędzie release; #42 po osobnej akceptacji odrzuca wcięte przypisania INI przy key oraz wynik przy błędzie zwolnienia locków. Testy regresyjne w #43. #44 poprawia test owner (nested root) osobno od aliasu, niezależny limit metadanych, nazwę testu snapshotu i martwy kod/import.
- Użytkownik potwierdził zielone pytest tests/test_secret_tools.py tests/test_volatile_results.py tests/test_tasks_manager.py tests/test_file_changes.py tests/test_file_transaction.py tests/test_path_guard.py tests/test_secrets_mask.py tests/test_edit_tools.py; bez pełnego logu/liczb. Testy obejmują podstawowe selektory/ekstrakcję/format/limity, plan/payload, decyzje/konflikty/ro, callback, kolejność locków i release failure. Nie oznaczają pełnego audytu trwałych DB/WAL/logów/telemetrii ani wszystkich wyścigów; dalsze pokrycie/audyt pozostają do C3/D.
- Kontrola server/ pełna, tylko 3 wcześniejsze escape; nowy moduł bez trafień. tests/ time_budget (ostatnio25pl), zmieniony plik osobno bez trafień. Agent nie uruchamiał pytest/serwera/git. Commity C1/docs poprzedniej sesji user potwierdził; C2 i bieżące docs niepotwierdzone. #40 docs po restarcie done/exit0/3 z 3 i odczyt potwierdzony; zapisy działają także #41-44.
- NEXT: odczyt integracji C3 i szczegółowy plan do osobnej akceptacji. secret_reveal NIEZAREJESTROWANE, dotychczas18tools. C3 i reszta D otwarte, bez napraw scroll/mode przy okazji.

#### Szczegółowy kontrakt C2 (zaakceptowany plan zachowany po implementacji)
- Nowe tylko server/secret_tools.py i tests/test_secret_tools.py. secret_reveal_impl(registry, manager, config_lock, filepath, key=None, start_line=None, end_line=None, max_chars=12000, root=None, description=''). Bez rejestracji/main/panel/telemetry/manager w C2.
- Pierwsza wersja tylko pliki sekretów rozpoznawane w5a; .env.example/sample/template, zwykłe pliki, pfx/p12/binarne/UTF16 odrzucone. Tekst UTF8/BOM/cp1250 do5MiB. max_chars ścisły int1-80000 (niebool), wynik także <=320000bajtów UTF8, błąd bez obcinania.
- key dla .env/.env.*, npmrc i pip.ini/conf: dokładne case-sensitive przypisanie rozdzielone pierwszym '=', opcjonalne export tylko env, pomijanie pustych/komentarzy/nagłówków INI; trim zewnętrznych spacji i jednej pary pasujących cudzysłowów. Brak interpolacji/dekodowania escape/env lookup/kodu. Pusta wartość dozwolona. Brak klucza, duplikaty (także różne sekcje INI), niedomknięte cudzysłowy, multiline lub niejednoznaczny inline komentarz -> bezpieczny błąd bez zadania, użyć jawnego zakresu; nie zgadywać składni.
- Zakres: oba końce wymagane, dodatnie int, start<=end, fizyczne linie i oryginalny EOL; poza plikiem błąd bez przycinania. PEM i raw key tylko zakres. Selektory mieszane lub brak -> błąd bez zadania. description jak B1: do500 bez control/bidi.
- Przygotowanie przed submit: read_snapshot chwilowo sprawdza ekstrakcję/limit; prywatny plan repr=False zawiera wejściową ścieżkę/binding root i owner/rozwiązany cel/hash/identity/mode/selektor/limit. NIE przechowuje raw/text/snapshot/sekretu podczas oczekiwania. Domknięcia verify/executor mają tylko plan i zależności. Publiczny payload bez hashy/wartości/diffu, tylko selektor/root/path/limit/opis; stałe ostrzeżenia. Brak hintów/fragmentów pliku w błędach.
- verify i executor ponownie guard READ (ro dozwolone), przypięte root/owner/path/tryby oraz hash/identity/mode. Zmiana pliku/dostępu -> failed bez wyniku. Executor bierze WRITE_LOCK potem config_lock, cancel-aware; świeży snapshot zweryfikowany i ekstrakcja z TEGO SAMEGO snapshotu; ponowny path/access/cancel przed zwrotem VolatileResult(content+bezpieczne metadane). Zero zapisów/temp. TOCTOU rezydualne.
- access_check wyniku zachowuje wyłącznie binding, nie snapshot/sekret. Sprawdza root/owner/path i bieżącą dostępność zwykłego pliku do odczytu. Nie porównuje treści przy każdym pobraniu: wynik jest snapshotem z wykonania. Utrata dostępu -> unieważnienie w magazynie C1. Callback bez surowych wyjątków.
- Wszystkie komunikaty stałe, bez str(exc), treści i hashy. Executor/callback nie propagują surowych wyjątków. C1 zapisuje ogólny błąd ujawnienia, nie dokładną diagnostykę dict executora.
- Testy: konkretna wartość bez sąsiadów; quotes/export/puste/ '=' w wartości; brak/duplikaty/niejednoznaczność/multiline; zakres/EOL/BOM/cp1250/PEM/key; limity/binary/pfx/p12; brak task przy błędzie; plan/payload bez zachowanych bajtów/tekstu; allow/deny/cancel/conflict/root changes/read ro; wartości i hashe poza Task/SQLite/logami; callback tylko binding i unieważnienie dostępu. Moduł -> kontrola -> testy -> kontrola -> komenda pytest userowi. W nowym chacie najpierw odczyt i krótki plan do osobnej akceptacji przed kodem.

#### C3 (ZREALIZOWANE, testy i próby na atrapach potwierdzone)
- Zakres wykonany jako C3a-C3d: #48/#49 get_task z osobnym secret_result i testy; #50/#51 confidential w telemetrii i testy; #52/#53 widok panelu secret_reveal; #54/#55 testy API panelu oraz runtime JS; #56/#57 rejestracja poufna i testy AST; #58 poprawka atrap kolejki. Wszystkie done/exit0, odczyty po decyzji, kontrole escape bez nowych trafień w zmienionych plikach (tests/ nadal częściowe przez time_budget).
- Decyzje: poufność telemetrii wybiera rejestrujący narzędzie (register_confidential_tool tylko get_task i secret_reveal), nigdy analiza treści odpowiedzi; puste rozmiary CSV oznaczają niemierzone. Panel czyta Task przez manager.get/list/cancel i nie ma dostępu do kanału RAM. Widok secret_reveal pokazuje tylko metadane i escapuje Cc/Cf/Zl/Zp/Cs; wzmocnienie C2 odrzucające takie znaki w filepath przy przygotowaniu zostało zaproponowane i nie zostało wprowadzone na życzenie użytkownika.
- Weryfikacja: zielone paczki testów po kolejnych zapisach według użytkownika, w tym rejestracja, telemetria, task_tools, panel API/static, node --test dla panelu i szersza regresja przed restartem; brak pełnego logu i liczb. Ostrzeżenie o martwym wątku telemetrii było wadą testowej atrapy kolejki (#58). Po restarcie sklr (Session not found do odświeżenia połączenia) próby na a/.env: brak selektora i błędy walidacji bez zadań; #59 klucz DEMO_TOKEN z wielokrotnym get_task i bez konsumowania; list/wait/cancel bez wyniku; #60 odmowa; #61 zakres 3-3 dokładnie jedna linia z LF; po ponad 5 minutach #59/#61 unavailable. Widok panelu przed zgodą potwierdził użytkownik.
- Próbna kontrola logs/ nie wykryła atrap w 6 plikach CSV, a wpisy get_task/secret_reveal po restarcie nie mają rozmiarów. Nie jest to audyt DB/WAL/logów/telemetrii ani wszystkich śladów. Brak gwarancji zeroizacji RAM/swap/crashdump i retencji po stronie klienta/LLM. Pełny pytest po C1-C3 oraz commit C3 niepotwierdzone w chwili zapisu.

#### Plan C3 i pozostała D (C3 zrealizowane, D OTWARTE)
- C3: server/task_tools.py/main.py/telemetry.py, minimalny widok panel/static/app.js i testy API/static/AST. Tylko get_task dołącza wynik; telemetry nie przetwarza surowego wyniku, brak sekretów w panelu/SQLite/logach. 19. narzędzie secret_reveal dopiero po testach/rejestracji/restart usera. Escapowanie control/bidi nowego widoku przed rejestracją.
- D: pełna kontrola control/bidi/diff i integracji, audyt trwałych danych, dalsze testy. Zgłoszony scrollY długiego diffu wraca na górę: hipoteza re-render przy pollingu, naprawa tylko po planie/akceptacji. Tryb folderu '-' bo payload.mode brak, także single root; do osobnego planu. UX opcjonalne czekanie w całym budżecie8s albo grupowanie zmian: tylko notatka, natychmiast task_id i STOP nadal obowiązują. Bez tkintera, bez execute_command.

### Paczka D: integracja i ręczne testy
`main.py`: rejestracja edit_file/create_file/manage_folder/secret_reveal; panel i testy według potrzeb po przeglądzie.
- Diff według root/plik, około 200 KB podglądu z jawnym oznaczeniem skrócenia. Nie ujawnia sekretów; szczegóły maskowania diffów sekretów ustalić przed kodem.
- Znaki sterujące i kierunkowe Unicode w ścieżkach/komendach pokazywane jawnie, treść przez textContent, bez regresji CSP.
- Testy wrapperów, panelu i bezpieczeństwa, potem komendy użytkownikowi; restart sklr wykonuje użytkownik. Ręcznie create/edit/multi-root/overwrite/folder/deny/cancel/conflict/ro/sensitive/secret_reveal, po :ping list_tasks przed ponowieniem.

## Wyniki 5a (ZREALIZOWANA)
- A: `server/path_guard.py`, `server/secrets_mask.py`, `tests/test_path_guard.py`, `tests/test_secrets_mask.py`; poprawki guarda opisane powyżej.
- B: `server/file_tools.py`, `tests/test_file_tools.py`: listowanie jednego poziomu (500), odczyt UTF-8/BOM i cp1250 do 5 MB, zakres/numeracja/EOL, maskowanie całego tekstu przed zakresem i skróceniem, batch do 10 wpisów ze wspólnym budżetem (także pozostałość poniżej 1000 znaków) i niezależnymi błędami. Numery fizycznych linii zachowane przy skracaniu bloków klucza prywatnego.
- C: `server/search_tools.py`, `tests/test_search_tools.py`, `tests/test_file_tools_registration.py`, rejestracja czterech narzędzi w `main.py`. Search dosłowny, po maskowaniu, plik jako subpath, wszystkie roots, scalanie okien kontekstu, alias/zakres/match_lines w wyniku. `count` oznacza liczbę dopasowanych linii w zwróconych fragmentach; zakres opisuje pełne okno również przy skróconym content. `max_matches` przycinane do 1-500, `max_total_chars` do 1000-80000. Brak arbitralnej blokady tekstowych `.lock`, `.svg`, `.map`. Stary MCP porównany z referencji `vibecoder`, tylko odczyt.
- Budżet search 6 s jest kooperacyjny (nie przerywa blokującej operacji systemu plików); `truncated_reason`: `time_budget`, `max_matches`, `max_total_chars`. Test czasu używa sztucznego zegara bez oczekiwania. Binarne, duże pliki, symlinki/junctiony i katalogi śmieciowe pomijane.
- Użytkownik potwierdził przejście: `pytest tests/test_path_guard.py tests/test_secrets_mask.py tests/test_file_tools.py tests/test_search_tools.py tests/test_file_tools_registration.py`. Agent nie uruchamiał testów ani serwera. Użytkownik zrestartował sklr; alias `sklr` wskazuje `http://localhost:8101/mcp`, lista zawiera 15 narzędzi. Test rejestracji wyodrębnia wrappery przez AST bez importu produkcyjnego `main.py` i jego skutków ubocznych.
- Testy ręczne przez sklr zaliczone: listowanie domyślnego/jawnego roota; zakres, numeracja, CRLF i root=False; batch między roots z błędem jednego wpisu; search w pliku, scalanie kontekstu, root='*' z ostrzeżeniem brakującego subpath jednego roota; brak dopasowań; limit dopasowań i przycięcie context_lines; odrzucenie wyjścia poza root i nieznanego aliasu z listą aliasów.
- Maskowanie ręczne na sztucznym `todo/test-5a/.env`: odczyt/batch i search zwracają `masked=true`, `masked_count=2`; wyszukanie sztucznej wartości tokena daje 0 dopasowań, kontekst pokazuje tylko maski. Istniejący `todo/test-5a/dummy.p12` potwierdzony listowaniem: odczyt zablokowany, search pomija (`skipped_files=1`). Pliki testowe utworzył użytkownik, nie zawierają rzeczywistych sekretów.
- Po restarcie pierwszy odczyt listy narzędzi dał znany błąd `: ping`; ponowienie odczytu zadziałało. Nie tworzono zadań w testach odczytu.
- Doprecyzowanie zasad pracy przez użytkownika: docelowe zapisy/komendy sklr będą zawsze przez panel z natychmiastowym task_id; do czasu implementacji zapisów rozwijamy repo starym MCP z akceptacją planu i GUI. Po każdej edycji wykonano wymagane wyszukiwanie backslash+cudzysłów w katalogu; kod/config/panel poza zakresem nie były zmieniane.

## Naprawa powiadomień panelu przed 5b (ZREALIZOWANA)
- Przyczyna: tick pomijał pobieranie zadań przy document.hidden. Zmiany tylko w `panel/static/app.js`: polling 5 s w tle / 1,5 s na widocznej karcie, wspólne żądanie refresh i jedna pętla timerów, powiadomienia/licznik przed pominięciem renderowania list i szczegółów w tle, odświeżenie po zmianie widoczności.
- Powiadomienia opisują rodzaj/tytuł zadania (nie tylko skill/script), mają stabilny tag; komunikat przycisku rozróżnia zgodę, odmowę i brak API. AudioContext wznawiany przy zawieszeniu; dźwięk wymaga kliknięcia użytkownika.
- Testy: uzupełniony `tests/test_panel_static.py`, nowy `tests/test_panel_notifications.cjs` na node:test/vm z atrapami DOM/API/timerów (Node 18+, bez nowych bibliotek). Pokrycie: ukryta karta, brak duplikatów, scalanie żądań/timerów, powrót na kartę, odmowa uprawnień, opis innych rodzajów zadań, audio, błąd sieci i 401. Użytkownik potwierdził przejście obu komend.
- Ręcznie: Ctrl+F5 panelu, włączenie powiadomień, inna aktywna karta; użytkownik potwierdził dźwięk/powiadomienie/licznik bez aktywowania panelu. Zadanie #18 hello-skill zatwierdzone w panelu, get_task: done, exit_code=0. Backend/config bez zmian, restart sklr niepotrzebny.
- Chrome może opóźniać timery lub uśpić kartę; nie gwarantujemy 5 s przy długim uśpieniu. Osobnego kilkuminutowego testu nie wykonano. SSE nie wdrażano.
- Edycja testu mimo błędu :ping została zapisana (potwierdzono odczytem, bez ponawiania). Po każdej edycji wymagane wyszukiwanie escape. Commit poprawki panelu niepotwierdzony.

## Wyniki 5.1 (ZREALIZOWANA)
Testy ręczne (sklr na porcie 8101, panel zatwierdzeń, harness przez alias `sklr`):
- Walidacja w panelu: odrzucone `C:\`, `C:\Users\rap1tw` i `%APPDATA%`; odrzucony także katalog centralny skills jako `rw` (zamierzone: skills centralne ładują się zawsze, niezależnie od roots).
- Dodanie folderów `rw` (`ToDo`, `GoTest`) i `ro` (`Kalifornia`, alias zmieniony na `test`); pierwszy dodany został domyślnym.
- `list_roots` z harnessa: alias, tryb, opis, ścieżka, `default`, `available`.
- `request_root`: zadanie `root_change` z plakietką "wrażliwe", bez "Ufaj"; odrzucenie (#16) nic nie dodało; zatwierdzenie (#17) dodało `gotest`. Pomyłkowe zatwierdzenie (#15) cofnięte przyciskiem "Usuń" w panelu, ponowny wniosek zadziałał.
- Skills projektowe: `ToDo\.agents\skills\proj-hello` widoczny jako `project`; zmiana domyślnego na `gotest` usuwa go z `list_skills`, powrót na `todo` przywraca (callback `on_default_change` -> `set_project_root` + `rescan` działa w obie strony).
- `config.json` ma wpis `roots`, `config.json.bak` istnieje (kopia przed migracją).

Uwagi z testów:
- `request_root` po błędzie `: ping` utworzył zadanie mimo błędu; sprawdzenie `list_tasks` przed ponowieniem zadziałało (bez duplikatu).
- Zgłoszony błąd panelu (powiadomienia, dźwięk i licznik w tytule nie działają w karcie w tle): notatka w `CURRENT.md`, sekcja "Do rozważenia".
- Odstępstwo od planu: zakładka "Foldery" odświeża się przy wejściu i po akcji, bez pollingu (żeby nie nadpisywać edytowanych pól).
- `config.json` (śledzony w repo) zawiera teraz lokalne foldery testowe; patrz decyzja w `CURRENT.md`.

## Wyniki 5.0 (ZREALIZOWANA)
Zmiany: `tasks/models.py` (`kind`, `title`, `payload`, `sensitive`; `payload` poza `to_summary`), `tasks/store.py` (nowe kolumny, obsługa słowników JSON, migracja addytywna `ALTER TABLE` + `PRAGMA user_version = 1`), `tasks/manager.py` (`exit_code = None` dla `cancelled` i `timed_out`; `trust` degradowane do `allow` dla `sensitive`), `tasks/dialog_adapter.py` (wiadomości wg `kind`, ucieczka znaków sterujących i dwukierunkowych, auto-odrzucanie `sensitive`/`root_change`/`secret_reveal` i treści powyżej 10000 znaków), `panel/static/app.js` i `style.css` (plakietki rodzaju i "wrażliwe", widok wg `kind`, diff, etykieta "opis od modelu", brak "Ufaj" dla `sensitive`). Testy: nowy `tests/test_phase5_tasks.py`; `pytest` przechodzi.

Testy ręczne (sklr na porcie 8101, panel zatwierdzeń):
- `hello.py` (#12): zatwierdzenie, `done`, `exit_code` 0, nowe pola w odpowiedzi.
- `sleep.py` 60 s z anulowaniem (#13): `cancelled`, `exit_code` null, częściowy `stderr`.
- `sleep.py` 30 s z `timeout=3` (#14): `timed_out`, `exit_code` null, `duration_s` 3,46.
- Migracja: stare zadania (#7-#11) czytelne, `kind` = `skill_script`. Odrzucenie (`rejected`) nie było powtarzane (kod bez zmian, zaliczone w 4b, zadanie #10).

Uwagi z testów:
- Alias zewnętrznego MCP wskazywał początkowo na port 8100 (stary MCP, 36 narzędzi); poprawny adres sklr to `http://localhost:8101/mcp` (9 narzędzi).
- Pierwsze wywołanie po podłączeniu zwraca błąd `: ping`; ponowienie działa (znane).
- Jeden pozorny błąd decyzji w teście #13 wynikał z kolejności zdarzeń (sprawdzenie przed kliknięciem), nie z błędu serwera.
- Decyzje zrealizowane: limit okna zapasowego 10000 znaków; kolumna `sensitive` dodana do modelu (nie było jej w pierwotnym planie).

## 7. Ryzyka
- Faza jest największa (stary `main.py` 1769 linii): podział na podfazy i osobne akceptacje ogranicza rozrost.
- Migracja `tasks.db` i uogólnienie panelu dotykają działającej fazy 4b: testy regresji przed zmianami funkcjonalnymi.
- Heurystyki guardów dają złudne poczucie bezpieczeństwa; panel i ADR 0003 mówią wprost, że granicą jest zatwierdzenie użytkownika i tajny token, a nie lista zabronionych słów.
- Zatwierdzanie bez limitu czasu zwiększa liczbę zadań czekających (limit `max_queued` 50, wygaśnięcie 60 min); trzeba zweryfikować, że wiele równoległych `awaiting_approval` jest czytelnych w panelu.
- Model może zdublować zadanie po błędzie `: ping`; opisy narzędzi nakazują sprawdzić `list_tasks`, a rozważamy opcjonalny `idempotency_key`.
- Wariant mieszany przesuwa część wartości (zatwierdzanie odczytu git bez klikania) do fazy 8; do tego czasu `git status` będzie wymagał zatwierdzenia. Jeśli to uciążliwe, przesuwamy skill `git` (odczyt) wcześniej.
