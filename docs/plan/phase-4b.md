# Faza 4b: panel webowy zatwierdzeń (plan)

Status: ZREALIZOWANA (kroki 1-6 zrobione, testy automatyczne i ręczne zaliczone, 2026-10-10). Decyzje 1-7 wg rekomendacji. Podstawa: ADR 0002.
Cel: zatwierdzanie, odrzucanie i anulowanie zadań z kolejki w panelu webowym na `127.0.0.1:8102`; panel zastępuje okno tkinter (`gui_simple`) jako domyślny interfejs zatwierdzeń.

## Technologia
Starlette (już w zależnościach FastMCP/uvicorn) + statyczny frontend: HTML + czysty JavaScript + CSS, bez frameworka, bez builda, bez CDN. Odświeżanie przez polling `fetch` co ok. 1,5 s (bez SSE, które sprawia problemy z `: ping`). Panel działa w wątku procesu sklr (drugi `uvicorn.Server`), bind wyłącznie `127.0.0.1`.

## Architektura
Pakiet `panel/`:
- `panel/auth.py`: token (`secrets.token_urlsafe`, tylko w pamięci), wymiana tokenu na ciasteczko sesji, weryfikacja ciasteczka, `Origin`, `Host` (`secrets.compare_digest`).
- `panel/app.py`: aplikacja Starlette i endpointy:
  - `POST /api/session` (token -> ciasteczko `HttpOnly`, `SameSite=Strict`)
  - `GET /api/tasks?status=&limit=`
  - `GET /api/tasks/{id}`
  - `POST /api/tasks/{id}/decision` (`allow` | `trust` | `deny`)
  - `POST /api/tasks/{id}/cancel`
  - `GET /` oraz pliki statyczne
- `panel/static/`: `index.html`, `app.js`, `style.css`.
- `panel/server.py`: start/stop wątku z uvicornem.
- `main.py`: start panelu, link `http://127.0.0.1:8102/#token=...` w konsoli, opcjonalne `webbrowser.open`, flaga `tasks.approval_ui` (`panel` domyślnie | `dialog`).

Panel korzysta z istniejących `TaskManager` i `ApprovalService.resolve`. Brak nowych narzędzi MCP (ADR 0002).

## Bezpieczeństwo (ADR 0002)
- Token tylko w pamięci, nowy przy każdym starcie; nigdy w `config.json`.
- Strona wymienia token z fragmentu URL na ciasteczko `HttpOnly` i czyści adres.
- Endpointy `/api/*` (poza wymianą tokenu) wymagają ciasteczka oraz poprawnych `Origin` i `Host` (127.0.0.1:8102 lub localhost:8102). Brak CORS.
- Operacje zmieniające stan tylko przez `POST`.
- `Content-Security-Policy` bez zewnętrznych skryptów; treść z outputu skryptów wstawiana jako tekst (`textContent`), nigdy jako HTML.

## Decyzje (zatwierdzone)
1. Starlette + vanilla JS + polling 1,5 s.
2. Panel w wątku serwera (nie osobny proces).
3. Wygaśnięcie `awaiting_approval`: konfigurowalne (`tasks.approval_expiry_min`, domyślnie 60 min) -> `rejected` ("brak decyzji").
4. "Ufaj skillowi": przycisk, gdy `offer_trust` = true; logika `offer_trust` zostaje w `policy.py`.
5. Pełny output: panel pokazuje to, co w bazie (6000 znaków na strumień).
6. Flaga `tasks.approval_ui` (`panel` domyślnie, `dialog` = `gui_simple` jako zapas do fazy 6).
7. Automatyczne `webbrowser.open` przy starcie (wyłączalne: `panel.open_browser`); powiadomienia: licznik w tytule karty, dźwięk, Notification API.

## Kolejność prac (kod + testy; `pytest` uruchamia użytkownik)
1. `panel/auth.py` + testy. ZROBIONY.
2. `panel/app.py` (API) + testy. ZROBIONY (`TestClient`: 401 bez ciasteczka, 403 dla złego `Origin`/`Host`, decyzja zmienia stan, 404 dla nieznanego zadania, 409 dla zadania nie czekającego na decyzję).
3. Wygaśnięcie zadań `awaiting_approval` (`tasks/expiry.py`, `expire_overdue` + `ExpiryWatcher`, config `tasks.approval_expiry_min`) + testy. ZROBIONY.
4. Frontend statyczny (`panel/static/`, `tests/test_panel_static.py`). ZROBIONY (testy ok); pełna weryfikacja ręczna w kroku 6.
5. `panel/server.py` + integracja w `main.py` + config (`panel`, `tasks.approval_ui`, `tasks.approval_expiry_min`). ZROBIONY (`tests/test_panel_server.py`, `tests/test_state.py`); zapas: gdy panel nie wystartuje (zajęty port), zatwierdzenia przez okno tkinter.
6. Testy ręczne: zatwierdzenie, odrzucenie, anulowanie, wygaśnięcie, zły token, zły `Origin`, powiadomienia.

## Wyniki kroku 6 (testy ręczne, 2026-10-10) - ZROBIONY
1. Zezwól: zadanie z `hello.py` pojawia się w panelu, szczegóły (podgląd skryptu, hash, powód), po zatwierdzeniu `done`. OK.
2. Odrzuć: `rejected`, `decision: deny`, skrypt się nie uruchomił. OK.
3. Anuluj trwającego `sleep.py`: `cancelled` po ok. 5 s. OK.
4. Powiadomienia: dźwięk, licznik w tytule karty, plakietka "Czeka: N". OK.
5. Wygaśnięcie (tymczasowo `approval_expiry_min` = 1): zadanie bez decyzji -> `rejected`, `decision: timeout` po 60 s; zadanie zatwierdzone tuż przed upływem minuty -> `done`. OK. Config przywrócony do wartości domyślnej (60).
6. Bezpieczeństwo: brak tokenu i zły token -> komunikat o nieważnej sesji; odświeżenie karty utrzymuje sesję (ciasteczko), token znika z paska adresu. OK.
- Nieprzetestowane ręcznie: przycisk "Ufaj skillowi" (brak skilla z `offer_trust: true`); pokryty testem jednostkowym `tests/test_panel_api.py`.

## Poza zakresem 4b
Okno konfiguracji (faza 6), narzędzia plików i `execute_command` (faza 5), pełny output poza limit 6000 znaków.
