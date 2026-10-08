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

## Testy
```
pytest
```
