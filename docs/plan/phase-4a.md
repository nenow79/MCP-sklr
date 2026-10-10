# Faza 4a: kolejka zadań (plan do akceptacji, bez kodu)

Status: ZREALIZOWANA (kroki 1-7 zrobione; testy ręczne A-E zaliczone, eksperyment F niekonkluzywny; decyzje 1-7 potwierdzone, w pkt 1 wybrana alternatywa). Podstawa: ADR 0002 (`docs/decisions/0002-task-queue-and-web-panel.md`).
Cel: `run_skill_script` przestaje blokować wywołanie MCP (limit harnessa ok. 11 s, bezpiecznie 8 s). Skrypt staje się zadaniem w kolejce; LLM odpytuje o status. Panel webowy to faza 4b.

## Stan wyjściowy (z kodu)
- `server/skills_tools.py::run_skill_script_impl` robi wszystko synchronicznie: walidacja -> `decide_confirmation` -> blokujący `approver` (okno tkinter, do 120 s) -> `run_command` -> wynik.
- `skills_engine/runner.py::run_command`: `Popen` + `communicate(timeout)`, po timeoucie ubija drzewo procesów. Nie ma dostępu do procesu z zewnątrz, więc nie da się anulować działającego zadania.
- `TrustStore` zmienia słownik `config` i woła `save_config` bez blokady (przy wielu wątkach potrzebny lock).
- `gui_simple.ask_skill_confirmation`: blokujący dialog z `_DIALOG_LOCK` (jedno okno naraz), zwraca `allow | trust | deny | timeout`.
- `policy.decide_confirmation`, `trust.compute_skill_hash`, `runner.resolve_script/build_command/clean_env`: bez zmian.

## Architektura
Nowy pakiet `tasks/` (bez zależności od FastMCP i od tkinter, w całości testowalny):
- `tasks/models.py`: `TaskStatus` i dataclass `Task`.
- `tasks/store.py`: `TaskStore` (SQLite, `tasks.db` obok kodu, w `.gitignore`). Jedno połączenie `check_same_thread=False` + lock, tryb WAL. Metody: `create`, `update`, `get`, `list`, `purge`, `mark_interrupted`.
- `tasks/manager.py`: `TaskManager`: `submit`, `get`, `list`, `wait`, `cancel`, `resolve_approval`. Pula wątków (`max_parallel`), kolejka FIFO, `threading.Condition` do `wait`.
- `tasks/approval.py`: `ApprovalService` (interfejs niezależny od UI): `request(task)` oraz `resolve(task_id, decision)`. Decyzje: `allow | trust | deny`. W 4a adapter wywołuje `gui_simple` w wątku tła i woła `resolve`. W 4b adapterem będzie panel web (ten sam `resolve`).
- `server/task_tools.py`: logika narzędzi MCP (`*_impl`, jak w `skills_tools.py`). `main.py` tylko rejestruje.

## Model zadania i stany
Pola: `id` (kolejna liczba całkowita, czytelna: "zadanie 7"), `created_at`, `started_at`, `finished_at`, `status`, `skill`, `source`, `script`, `args`, `timeout`, `command` (lista), `skill_hash`, `approval_reason`, `offer_trust`, `decision`, `decided_at`, `exit_code`, `stdout`, `stderr`, `stdout_truncated`, `stderr_truncated`, `duration_s`, `error`.

Stany: `awaiting_approval`, `queued`, `running`, `done`, `failed`, `rejected`, `timed_out`, `cancelled`, `interrupted`.

Przejścia:
- submit: po walidacji. Jeśli `decide_confirmation.required` -> `awaiting_approval`, w przeciwnym razie -> `queued`.
- `awaiting_approval` -> `queued` (allow/trust) | `rejected` (deny lub brak decyzji w oknie) | `cancelled`.
- `queued` -> `running` -> `done` (exit 0) | `failed` (exit != 0 lub nie wystartował) | `timed_out` | `cancelled`.
- Start serwera: wszystkie zadania nieterminalne (`awaiting_approval`, `queued`, `running`) -> `interrupted`.

## Zachowanie narzędzi MCP
- `run_skill_script(name, script, args, timeout, wait=8)`:
  - Walidacja jak dziś (timeout, args, skill, ścieżka skryptu, interpreter). Błąd walidacji = natychmiastowy `success: false`, bez zadania.
  - Tworzy zadanie; jeśli `awaiting_approval`, zwraca od razu `task_id` + status. W pozostałych przypadkach czeka maks. `wait` s (twardy limit 8 s, config `tasks.max_wait_s`) na stan terminalny.
  - Zakończone w tym czasie: wynik w kształcie jak dziś + `task_id` i `status`.
  - Niezakończone: `success: true`, `data: {task_id, status, hint}` (hint: "użyj wait_task/get_task").
- `get_task(task_id)`: pełne dane zadania (stdout/stderr, powód, decyzja).
- `list_tasks(status=None, limit=20)`: zwięźle, bez stdout/stderr, najnowsze pierwsze.
- `wait_task(task_id, seconds=8)`: czeka maks. 8 s na stan terminalny.
- `cancel_task(task_id)`: `awaiting_approval`/`queued` -> `cancelled`; `running` -> ubicie drzewa procesów -> `cancelled`.
- Brak narzędzia zatwierdzającego/odrzucającego (ADR 0002).

## Zmiany w istniejącym kodzie
1. `runner.run_command`: opcjonalny `cancel_event` (pętla `communicate(timeout=0.5)` sprawdzająca zdarzenie i upływ czasu) i zwrot informacji o anulowaniu. Zachowanie dla timeoutu bez zmian.
2. `skills_tools.run_skill_script_impl` dzieli się na `prepare_script_run` (walidacja, interpreter, hash, decyzja o potwierdzeniu -> opis zadania) i wykonanie w `TaskManager`. Powtórne sprawdzenie hasha tuż przed startem zostaje (skill mógł się zmienić w trakcie oczekiwania).
3. `TrustStore`: lock wokół modyfikacji i `save`. "Ufaj i uruchom" zapisuje zaufanie dopiero przy decyzji `trust` (jak dziś).
4. `state.py`/`config.json`: sekcja `tasks`: `max_parallel` (2), `max_queued` (50), `max_wait_s` (8), `retention_days` (30), `retention_max` (500).
5. `main.py`: tworzenie `TaskStore`/`TaskManager`/`ApprovalService`, `mark_interrupted` przy starcie, rejestracja 4 nowych narzędzi, nowy `run_skill_script`.
6. `gui_simple.py`: bez zmian (tymczasowy adapter zatwierdzeń do czasu 4b).

## Decyzje do potwierdzenia (moje rekomendacje)
1. ZATWIERDZONE (alternatywa): przy `awaiting_approval` `run_skill_script` zwraca od razu `task_id` + status, bez czekania (`wait` dotyczy tylko `queued`/`running`).
2. Brak decyzji w oknie tymczasowym po 120 s = `rejected` (przyczyna "brak odpowiedzi"). W 4b: wygaśnięcie konfigurowalne (domyślnie 60 min).
3. `max_parallel = 2`, `max_queued = 50`, retencja 30 dni / 500 zadań.
4. Id jako kolejne liczby całkowite (SQLite autoincrement), nie UUID.
5. W bazie przechowujemy output tak jak zwraca runner (6000 znaków na strumień). Pełny output dla panelu = osobna decyzja w 4b.
6. Zadania po restarcie: wszystkie nieterminalne -> `interrupted` (bez wznawiania).
7. Osobny, tani eksperyment przed lub w trakcie 4a: `json_response=True` w `http_app()` i ponowny pomiar `sleep.py` 12/15/30 s. Kolejka jest potrzebna niezależnie od wyniku.

## Kolejność prac (każdy krok: kod + testy, użytkownik uruchamia `pytest`)
1. `tasks/models.py` + `tasks/store.py` + testy store (CRUD, purge, `mark_interrupted`, wielowątkowość).
2. `runner.run_command` z `cancel_event` + testy (anulowanie, timeout nadal działa).
3. `tasks/approval.py` + `tasks/manager.py` z atrapami runnera i approvera + testy przejść stanów (zatwierdzenie, odrzucenie, anulowanie w każdym stanie, równoległość, limit kolejki, `wait`, zmiana hasha między submit a startem).
4. `TrustStore` z lockiem + test.
5. `server/task_tools.py` + testy narzędzi; migracja testów `test_skills_run_tool.py` do nowego modelu (te same przypadki: path traversal, nieznany skill, trust, hash, timeout).
6. Integracja w `main.py`, konfiguracja `tasks`, adapter tkinter -> `ApprovalService`.
7. Testy ręczne z harnessa: `hello.py` (szybkie, wynik od razu), `sleep.py` 30 s (zwraca `running`, `wait_task`, `get_task`, `cancel_task`), zadanie z potwierdzeniem (okno -> `queued` -> `done`), odrzucenie, restart serwera (`interrupted`).

## Wyniki kroku 7 (testy ręczne z harnessa, 2026-10-10) - ZROBIONY
- A. `hello.py`: zatwierdzenie w oknie -> `done`, exit 0, wynik poprawny. OK.
- B. `sleep.py` 30 s: `running`, `wait_task` wraca z podpowiedzią, `cancel_task` -> `cancelled` z częściowym outputem. OK. Uwaga: `exit_code` = 1 przy `cancelled` (kosmetyka).
- C. `awaiting_approval` -> `queued` -> `running` -> `done`. OK.
- D. Odrzucenie w oknie -> `rejected`, `decision: deny`, skrypt się nie uruchomił. OK.
- E. Restart serwera w trakcie `running` -> `interrupted`, `error: "serwer zrestartowany"`. OK.
- F (decyzja 7, `json_response=True`): niekonkluzywny. Błąd `: ping` nadal występuje przy pierwszym wywołaniu po restarcie serwera, więc to prawdopodobnie nawiązywanie sesji po stronie klienta, a nie długie wywołanie. Pomiaru 12/15/30 s nie wykonano (wywołania kolejki trwają maks. 8 s). Decyzja: `json_response=True` zostaje w `main.py`.

## Poza zakresem 4a
Panel webowy, token, ciasteczko, powiadomienia (4b). Narzędzia plików i `execute_command` (faza 5).
