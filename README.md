# MCP-my-codex-sklr

Nowy lokalny serwer MCP oparty na standardzie Agent Skills. Szczegóły: `AGENTS.md`, `docs/`.

## Uruchomienie (faza 0)
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

## Testy
```
pytest
```
