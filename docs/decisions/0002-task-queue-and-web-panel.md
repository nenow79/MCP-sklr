# ADR 0002: Asynchroniczna kolejka zadań i panel webowy

Status: zaakceptowany (rozmowa, 2026-10-09)

## Kontekst
Pomiar limitu harnessa (`sleep.py`, skill `hello-skill`), serwer sklr podłączony bezpośrednio do harnessa:
- 5, 9, 10, 11 s: OK. 12, 15, 30 s: błąd `SyntaxError: Unexpected token ':', ": ping - 2"... is not valid JSON`.
- Przez stary MCP jako pośrednika limit był niższy (8 s OK, 9 s błąd).
- Błąd nie jest timeoutem skryptu (`timeout` ustawiony na 120 s). Wygląda na komentarz keep-alive SSE (`: ping`), którego klient harnessa nie parsuje. `sse_starlette` ma domyślny ping co 15 s; zmierzony próg to ok. 11-12 s (źródło różnicy niepotwierdzone).
- Bezpieczne maksimum czasu jednego wywołania MCP: **8 s**.
- Stary MCP obchodził problem asynchronicznością (okienka GUI w osobnym wątku, natychmiastowy zwrot z instrukcją "wywołaj ... aby potwierdzić"), bez zmian w transporcie.
- Blokujące okienko zatwierdzenia w trakcie wywołania wydłuża wywołanie i ryzykuje przekroczenie limitu.

## Decyzje
1. **Kolejka zadań w procesie serwera.** Pula wątków wykonuje zadania, rejestr stanów zapisany w SQLite (historia przeżywa restart; zadania `running` w chwili restartu oznaczane `interrupted`). Stany: `awaiting_approval`, `queued`, `running`, `done`, `failed`, `rejected`, `timed_out`, `cancelled`, `interrupted`. Korzysta z runnera, trustu i policy z fazy 3.
2. **`run_skill_script` z parametrem `wait`** (domyślnie 8 s). Szybkie zadanie zwraca wynik od razu (zgodnie z dzisiejszym zachowaniem), wolne zwraca `task_id` i status.
3. **Narzędzia MCP dla LLM:** `get_task(id)`, `list_tasks()`, `wait_task(id, seconds)` (max 8 s), `cancel_task(id)`. **Brak narzędzia zatwierdzającego/odrzucającego.**
4. **Zatwierdzanie wyłącznie przez panel webowy** na `127.0.0.1:8102` (osobny port od MCP 8101; port 8101 zostaje z szerokim CORS dla harnessa i nie ma na nim endpointów zatwierdzania). Panel pokazuje listę i szczegóły zadań (skrypt, argumenty, stdout/stderr, status) oraz pozwala zatwierdzić lub odrzucić.
5. **Panel web zastępuje tkinter** z ADR 0001 pkt 4. `ApprovalService` zostaje niezależny od UI (zgodnie z ADR 0001), więc ewentualny klient tkinter może używać tego samego API. Okno konfiguracji (faza 6) do ponownej oceny.
6. **Historia w SQLite** (standardowa biblioteka Pythona, bezpieczna przy zapisach z wielu wątków).

## Model zagrożeń panelu
- Założenie: zwykła przeglądarka użytkownika jest poza zasięgiem LLM. Narzędzie `web_browser` używa osobnej instancji Chrome (port debug 9222) z innym profilem.
- Model ma jednak powłokę (`execute_command` w fazie 5, skrypty skills), więc może wysyłać żądania HTTP do localhost. Dlatego:
  - Token zatwierdzania generowany przy starcie, tylko w pamięci serwera; nigdy w `config.json` ani w plikach workspace.
  - Launcher otwiera `http://127.0.0.1:8102/#token=...`; strona wymienia token na ciasteczko `HttpOnly` i czyści adres (fragment URL nie trafia do serwera ani logów).
  - Endpointy zatwierdzania wymagają ciasteczka oraz poprawnych nagłówków `Origin` i `Host`. Brak CORS na panelu.
- Ochrona dotyczy pomyłek modelu i prompt injection. Nie chroni przed celowo złośliwym modelem, który działa z uprawnieniami użytkownika.
- Wycofanie starego MCP (vibecoder) nie usuwa ryzyka: nowy MCP ma własne `execute_command`.

## Ograniczenia
- Serwer nie może obudzić LLM. "Powiadom mnie po zakończeniu" oznacza powiadomienie użytkownika w panelu; model dowiaduje się o wyniku przez `get_task` / `list_tasks` / `wait_task` po prośbie użytkownika albo własnym odpytywaniu (kosztuje tury narzędziowe).
- Nieznany limit tur narzędziowych harnessa w jednej odpowiedzi.

## Rozstrzygnięcia (fazy 4a i 4b)
- `json_response=True` w `http_app()`: włączone, ale niekonkluzywne. Błąd `: ping` nadal występuje przy pierwszym wywołaniu po restarcie serwera (ponowienie działa), więc to prawdopodobnie nawiązywanie sesji po stronie klienta. Kolejka z `wait` do 8 s pozostaje wymagana.
- Limity kolejki: `max_parallel` 2, `max_queued` 50, retencja 30 dni / 500 zadań, output 6000 znaków na strumień (konfiguracja `tasks`).
- Panel działa w wątku procesu serwera (drugi `uvicorn.Server`, bind `127.0.0.1`). ~~Zapasowe okno tkinter, gdy panel nie wystartuje~~ **wycofane w kroku T 5b**: zatwierdzenia wyłącznie przez panel; gdy panel nie wystartuje (np. zajęty port) lub `panel.enabled=false`, serwer wypisuje błąd i kończy działanie. `gui_simple.py` i `tasks/dialog_adapter.py` usunięte; `ApprovalService` pozostaje niezależny od UI.
- Wygaśnięcie zadań `awaiting_approval`: `tasks.approval_expiry_min` (domyślnie 60), po czym `rejected` z `decision: timeout`.

## Otwarte pytania
- Zatwierdzenie pojedynczego zadania a zaufanie do skryptu na stałe (hash, istniejący `TrustStore`): kiedy `policy.py` ma ustawiać `offer_trust` (dziś zawsze `false` dla domyślnej polityki).

## Konsekwencje
Kolejka i API panelu są fundamentem; interfejs jest wymienny. Zmiana pociąga za sobą nowe moduły (`tasks/`, panel web), migrację `run_skill_script` na model zadań i aktualizację `gui_simple.py` (tymczasowy dialog do czasu fazy 4b).
