---
type: Specification
title: Model i twarde reguły komend — propozycja
description: Proponowane, niewdrożone wymagania dla execute_command i command_policy.
tags: [sklr, commands, proposal]
---
# Model i twarde reguły komend — propozycja

Niewdrożone w `e6cee29`. [Status, korekty i mapa propozycji](command-execution-proposal.md) są częścią kontraktu. Wzmianki o ADR 0003 oznaczają planowany dokument. Testy opisują wymagania, nie wyniki.

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
