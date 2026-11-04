# MCP-my-codex-sklr

Nowy lokalny serwer MCP oparty na standardzie Agent Skills. Zasady: [AGENTS.md](AGENTS.md). Dokumentacja: [krótka mapa](docs/README.md), [aktualny stan](docs/plan/CURRENT.md), [backlog](docs/plan/backlog.md).

Wersja OKF: panel **Wiedza**, centralne i projektowe bundles, search/get/save,
`prepare_project`, audyt i opcjonalna konwersja `docs/` oraz plików `.vibecoder_*`.
Zwykły Markdown projektu można wyszukiwać i czytać przed konwersją. Brak AGENTS
lub konwersji nie blokuje narzędzi w domyślnym trybie zgodności. Panel pozwala jawnie
wymagać OKF w wybranym zakresie; poprawne dokumenty OKF są chronione przed utratą formatu.
Szczegóły: [polityka zapisu](docs/specs/okf-integration/write-policy.md).
Aktualizacja i końcowe testy na Windows: [docs/windows-okf.md](docs/windows-okf.md).

## Instalacja na Windows

Wymagania: zainstalowana conda z Pythonem i dostęp do pakietów pip. Pobierz cały
projekt do zapisywalnego folderu użytkownika; nie kopiuj cudzego venv, configu,
baz, kluczy ani trust. Przed instalacją zatrzymaj działający sklr.

Uruchom `install.bat` (domyślnie środowisko conda `root`). Jeśli conda nie jest
dostępna z dwukliku, otwórz Anaconda/Miniforge Prompt, przejdź do katalogu projektu
i wykonaj:

```bat
install.bat root
```

Dla środowiska o nazwie `base` użyj `install.bat base`. Instalator aktywuje conda
tylko do utworzenia lokalnego `venv`, następnie ją dezaktywuje. Zależności instaluje
przez `venv\Scripts\python.exe -m pip`, bez potrzeby aktywowania venv.
Istniejący venv jest używany ponownie; niekompletny nie jest kasowany ani nadpisywany.
Ponowne uruchomienie może zmienić zależności zgodnie z `requirements.txt`.
Instalator nie instaluje conda/skilli, nie uruchamia serwera ani testów. Błąd kończy
instalację kodem 1; częściowa instalacja zależności/venv nie jest automatycznie cofana.

Gdy brak `config.json`, pomocnik `scripts/init_install_config.py` tworzy go z
`config.example.json` w UTF-8 bez BOM: **sesje włączone**, autopilot wyłączony,
panel włączony, MCP na `127.0.0.1`, lista folderów pusta. Istniejący config pozostaje
bez zmian; wyłączone sesje są sygnalizowane. Niepoprawny JSON/BOM lub niepoprawna
sekcja sesji kończą ten krok błędem, bez nadpisywania. To nie pełna walidacja configu
ani potwierdzenie uruchomienia aplikacji.

Po instalacji uruchom `sklr.bat`. Otwórz aktualny link panelu z konsoli, w zakładce
„Projekty i foldery” dodaj własny istniejący folder projektu, odśwież stan i naciśnij
**Start**. Brak folderów oznacza nieaktywny Start. Jeśli zachowany config ma
`work_sessions.enabled=false`, zmień tę flagę na `true` i zrestartuj sklr.

## Uruchomienie ręczne

W firmowym środowisku Windows najpierw aktywuj conda do utworzenia venv:

```bat
call activate root
python -m venv venv
call conda deactivate
call venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python scripts\init_install_config.py
python main.py
```

W starszej conda zamiast `call conda deactivate` użyj `call deactivate`.
Serwer: `http://127.0.0.1:8101/mcp`, panel: `http://127.0.0.1:8102`
(host, port i CORS w `config.json`).

`config.json` pozostaje lokalnie obok `main.py` i jest ignorowany przez Git.
Istniejącą konfigurację zachowaj; nie zastępuj jej przykładem. Przy nowej instalacji
możesz skopiować `config.example.json` do `config.json` i ustawić własne roots,
profile oraz środowiska. Przykład ma pustą listę roots/trust/profili i wyłączone
sesje/autopilota. Brakujące klucze istniejącego configu uzupełniają wartości domyślne
z `state.py`; aplikacja nie odczytuje automatycznie pliku przykładowego.

Przy pierwszej aktualizacji wersji ze śledzonym configiem wykonaj
[migrację z kopią poza checkoutem](docs/windows-okf.md#aktualizacja-na-windows).
Samo dodanie do `.gitignore` nie chroni pliku, który starszy commit nadal śledzi.

## Narzędzia MCP
- `ping`, `list_skills`, `activate_skill`, `read_skill_resource`.
- `run_skill_script(name, script, args, timeout, wait)` - skrypt jako zadanie w kolejce (SQLite `tasks.db`).
- `get_task`, `list_tasks`, `wait_task`, `cancel_task` - status i sterowanie zadaniami.
- Sekcja `tasks` w `config.json`: `max_parallel`, `max_queued`, `max_wait_s` (limit 8), `retention_days`, `retention_max`, `approval_expiry_min` (wygaśnięcie oczekującego zadania, domyślnie 60). Stare klucze `approval_ui` i `approval_timeout_s` (okno tkinter wycofane) są ignorowane.

## Panel zatwierdzeń

Sesje pracy opt-in obsługują Start, pauzę i przygotowanie wznowienia. Po pauzie
model kończy odpowiedź bez zapisu checkpointu. Wznowienie wymaga decyzji panelu
i potwierdzenia aktualnego planu przez model; szczegóły w [kontrakcie sesji](docs/specs/work-session-pause.md).
Po aktualizacji S6 odśwież panel i katalog narzędzi klienta MCP.

- Zadania wymagające zatwierdzenia obsługuje panel webowy na `http://127.0.0.1:8102` (tylko localhost).
- Przy starcie serwera w konsoli pojawia się link z jednorazowym tokenem sesji (`http://127.0.0.1:8102/#token=...`), a przeglądarka otwiera się automatycznie. Token jest generowany przy każdym starcie i nie jest nigdzie zapisywany; po restarcie serwera użyj nowego linku.
- Panel: lista zadań, szczegóły (podgląd skryptu, stdout/stderr), zatwierdzanie, odrzucanie, anulowanie, powiadomienia (dźwięk, licznik w tytule karty).
- Sekcja `panel` w `config.json`: `enabled`, `port` (8102), `open_browser`. Panel jest jedynym interfejsem zatwierdzeń: gdy nie wystartuje (np. zajęty port) lub `panel.enabled` = false, serwer wypisuje błąd i kończy działanie (kod 1).

## Tworzenie całej struktury jedną zgodą

`create_tree(directories, files, root, description)` przygotowuje jedno zadanie
z listą katalogów i maskowanymi diffami wszystkich plików. Wpis `files` ma
postać `{"filepath": "nowy-skill/scripts/run.py", "content": "..."}`.
`directories` to lista ścieżek, np. `["nowy-skill/assets"]`; wspólny `root`
wybiera folder bazowy. Można podać same katalogi albo same pliki.

Brakujące katalogi nadrzędne są tworzone automatycznie. Istniejące katalogi
pozostają bez zmian; istniejący plik odrzuca cały plan, bez nadpisywania.
Limity: 200 brakujących katalogów, 100 plików, 5 MiB na plik i 20 MiB łącznie.
Linki/junctiony są niedozwolone. Całość wymaga jednej decyzji w panelu;
po niej sprawdź `get_task`. Przy błędzie wykonywany jest rollback własnych
zmian, nie pełna atomowość wobec awarii procesu/systemu. Niepełny rollback
wymaga ręcznej kontroli. Dotychczasowe narzędzia pozostają bez zmian.

## Testy

Uruchamia użytkownik po świadomym wyborze zakresu; komendy odbioru i ograniczenia wyników są w [instrukcji Windows](docs/windows-okf.md).

```
pytest
```
