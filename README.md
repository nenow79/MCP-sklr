# MCP-my-codex-sklr

Nowy lokalny serwer MCP oparty na standardzie Agent Skills. Zasady: [AGENTS.md](AGENTS.md). Dokumentacja: [krótka mapa](docs/README.md), [aktualny stan](docs/plan/CURRENT.md), [backlog](docs/plan/backlog.md).

Wersja OKF: panel **Wiedza**, centralne i projektowe bundles, search/get/save,
`prepare_project`, audyt i opcjonalna konwersja `docs/` oraz plików `.vibecoder_*`.
Zwykły Markdown projektu można wyszukiwać i czytać przed konwersją. Brak AGENTS
lub konwersji nie blokuje narzędzi w domyślnym trybie zgodności. Panel pozwala jawnie
wymagać OKF w wybranym zakresie; poprawne dokumenty OKF są chronione przed utratą formatu.
Szczegóły: [polityka zapisu](docs/specs/okf-integration/write-policy.md).
Aktualizacja i końcowe testy na Windows: [docs/windows-okf.md](docs/windows-okf.md).

## Uruchomienie
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```
Serwer: `http://127.0.0.1:8101/mcp` (host, port i CORS w `config.json`).

## Narzędzia MCP
- `ping`, `list_skills`, `activate_skill`, `read_skill_resource`.
- `run_skill_script(name, script, args, timeout, wait)` - skrypt jako zadanie w kolejce (SQLite `tasks.db`).
- `get_task`, `list_tasks`, `wait_task`, `cancel_task` - status i sterowanie zadaniami.
- Sekcja `tasks` w `config.json`: `max_parallel`, `max_queued`, `max_wait_s` (limit 8), `retention_days`, `retention_max`, `approval_expiry_min` (wygaśnięcie oczekującego zadania, domyślnie 60). Stare klucze `approval_ui` i `approval_timeout_s` (okno tkinter wycofane) są ignorowane.

## Panel zatwierdzeń
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
