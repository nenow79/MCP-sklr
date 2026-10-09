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
- Sekcja `tasks` w `config.json`: `max_parallel`, `max_queued`, `max_wait_s` (limit 8), `retention_days`, `retention_max`, `approval_timeout_s`.

## Testy
```
pytest
```
