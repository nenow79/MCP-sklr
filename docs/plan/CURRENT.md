---
type: Project State
title: Aktualny stan projektu sklr
description: Zakres implementacji, potwierdzeń i ograniczeń przed dalszą pracą nad autonomią i odbiorem Windows.
tags: [sklr, project-state, sessions, autonomy, windows, testing]
---
# Aktualny stan — 2026-10-21

Baza tej paczki: `9176967` (`feat: enable work-session pause and resume`). Development: Linux, runtime docelowy: Windows. Przed commitem przekazania odczyt SSH zdalnego main potwierdził tę samą bazę (`91769674b4ade191b8423fceb37171a7f4774e5a`), bez rozbieżności. Dokładny commit przekazania to commit zawierający tę aktualizację i odłączony pakiet autopilot/; sprawdź git log -1 --oneline po aktualizacji checkoutu. Nie jest to jeszcze beta z aktywnym autopilotem.

Priorytet użytkownika: dokończenie autonomicznej pracy według kontraktu, przygotowanie wersji do publikacji i dalszy odbiór na Windows z działającym MCP/panelem. Lista prac i warunki przekazania są wyłącznie w [backlogu](backlog.md). Szczegółowe wyniki wcześniejszych paczek zachowano w [archiwum przeglądu](../archive/state-review-2026-10-21/CURRENT.md); nie są wynikiem testów obecnego HEAD.

## Punkt kontynuacji na innym komputerze

S1, A2b1 i A2b2 mają potwierdzone przez użytkownika paczki: odpowiednio 80 passed in 1.60s, 149 passed in 0.55s i 298 passed in 0.73s; zakresy poniżej. Kontrakt A2 oraz format reguł A2b2 zaakceptowano. Najbliższy krok: przygotować konkretny plan A2b3 (dziennik/retencja, generacje zasobów, koordynacja z sesją i kontrola przed skutkiem), uzyskać akceptację przed zmianą kodu. Plan A2b3 nie został jeszcze zaakceptowany. Panel/aktywacja A2b4 i odbiór A2b5 są dalszymi etapami; A3/A3b i A4 nie mają jeszcze zaakceptowanych kontraktów.

[Prompt startowy](../continue-work.md) prowadzi do aktywnych źródeł, bez odtwarzania dawnych plików kontekstu. Przy zmianie komputera zachować jego lokalne ustawienia i środowisko. config.json nadal jest śledzony; ta paczka nie zmienia jego zawartości ani zasad śledzenia. W1/W2 nadal otwarte. Publikacja tego stanu prac nie zamyka W4 dla przyszłej bety.

## Sesje, pauza, checkpoint i wznowienie — zaimplementowane opt-in

Kontrakt: [sesje pracy](../specs/work-session-pause.md). main.py podłącza wspólny kontroler do kolejki, panelu i wrapperów narzędzi. work_sessions.enabled jest domyślnie false; zmiana wymaga restartu. Przy włączeniu brak wyboru sesji blokuje pracę do jawnej decyzji w panelu. Błędna konfiguracja nie daje fallbacku do pracy bez bramki.

- Jedna wybrana sesja na całą instancję, obejmująca wszystkie rozmowy; bez przełączania przypiętej sesji i bez modelowego session_id. Roots opisują projekt, nie nadają uprawnień.
- Pauza blokuje nowe działania, pozwala dokończyć rozpoczęte w dotychczasowych limitach, pokazuje ich liczbę; nie anuluje ich automatycznie. Zadania oczekujące nie zajmują workerów w oczekiwaniu na wznowienie.
- Cztery narzędzia sterujące: get_session_state, save_session_checkpoint, get_resume_context, confirm_resume_plan. Wrapper dodaje oddzielne session_control; treści checkpointu/wznowienia mają poufną telemetrię.
- Checkpoint do 6000 znaków, uzupełnienie do 3000 i plan do 6000 pozostają w RAM; SQLite work_sessions.db zawiera metadane. Wykryty sekret odrzuca całą treść, ale heurystyka nie daje gwarancji wykrycia każdego sekretu.
- Panel przygotowuje wznowienie przez TaskManager po ponownej kontroli rewizji i liczby anulowań; stare awaiting_approval/queued są anulowane. Brak checkpointu wymaga osobnego potwierdzenia. Dopiero potwierdzenie aktualnego planu/tokenu przez model otwiera bramkę. Plan w panelu bez tokenu; ręczne odświeżanie.
- Restart nie wybiera/wznawia sesji ani nie odtwarza RAM. Jawne odzyskanie zapisanej sesji pozostawia bramkę zamkniętą. Nowy checkpoint unieważnia handshake; niepewny commit wymaga jawnego odzyskania, bez retry zdalnych skutków.

**Potwierdzony smoke 2026-10-21:** użytkownik włączył flagę i uruchamiał serwer. Start → odczyt AGENTS → pauza → checkpoint RAM → przygotowanie → potwierdzenie planu → odczyt działały przez panel i harness. Po restarcie brak automatycznego wyboru/wznowienia; odzyskanie i wznowienie bez utraconego checkpointu po osobnym potwierdzeniu również działały. Użytkownik potwierdził podgląd checkpointu, planu oraz decyzję z liczbą anulowań. To zapis wcześniejszego odbioru, nie aktualny odczyt działającej instancji.

Nie potwierdzono rzeczywistej odmowy wywołania roboczego przed Startem/podczas pauzy, anulowania faktycznie oczekujących zadań ani pauzy przy rozpoczętym działaniu.

**Lokalna poprawka S1 po akceptacji planu:** session_not_selected kieruje do Startu/jawnego odzyskania w panelu i zabrania zapisu checkpointu bez wybranej sesji. Nie twierdzi już, że narzędzie checkpointu nie istnieje. Bramka, kody odpowiedzi i uprawnienia bez zmian. Istniejące testy rozszerzono o komunikat z/bez dostępnej funkcji checkpointu oraz odmowę jego zapisu przed wyborem sesji. Kontrola AST poprawna. Użytkownik potwierdził paczkę test_session_tools/test_session_activation/test_session_checkpoint_tools: **80 passed in 1.60s**. To odbiór wskazanej paczki, nie pełna regresja ani ręczny odbiór poprawki przez MCP/panel na Windows; platformy tej paczki nie podano. Agent nie uruchamiał pytest. Zmiana nie jest jeszcze w commicie bazowym wskazanym wyżej.

## Autonomia — zakres jeszcze niewdrożony

Pauza i wznowienie realizują fundament A1/A1b; nie są autopilotem. Użytkownik zaakceptował [kontrakt A2 v1](../specs/autopilot-contract.md) po korekcie timeoutów. Autopilot A2, ograniczony wykonawca testów A3b i automatyczne poprawki A4 nie są wdrożone. Akceptacja kontraktu nie jest włączeniem delegacji ani akceptacją kolejnych paczek kodu. Ogólne execute_command/command_policy pozostaje [propozycją](../specs/command-execution-proposal.md).

Obecne zgody, trust, poufność i ograniczenia testów nadal obowiązują. sklr kontroluje własny dispatch, nie budzi modelu i nie steruje jego rozumowaniem ani narzędziami poza sklr. Wewnętrzna pętla obcego MCP/skilla nie dostaje automatycznie punktu pauzy.

Kontrakt A2 po uwadze użytkownika rozdziela oczekiwanie narzędzia (wait_task maks. 8 s), timeout zadania i ważność delegacji. Usunięto proponowany wspólny pułap zadania 120 s; timeout ma być jawny per wykonawca/profil, a długie testy objęte przyszłym A3b. Obecne skills/MCP nadal mają domyślne 60 s i maksimum 300 s; to odczyt istniejącego kodu, bez zmiany limitów runtime.

**A2b1 — odłączony fundament zapisany po akceptacji planu:** autopilot/models.py zawiera niemutowalne ContractBinding/BudgetLimits i zamkniętą walidację; autopilot/budget.py zawiera licznik RAM z nieprzezroczystymi tokenami, rezerwacją/start/finish/release, pauzą/cofnięciem/wygaśnięciem i synchronizacją RLock. Budżet liczony jednokrotnie, running <= 1; busy zachowuje rezerwację, pauza nie resetuje czasu/zużycia, rozpoczęte działanie można domknąć po cofnięciu/wygaśnięciu. Niespójność/zły zegar zamyka nowe przyjęcia. Zapisano testy modeli, cyklu i wyścigów w dwóch plikach test_autopilot_*. Kontrola AST pięciu plików i whitespace poprawna. Użytkownik potwierdził paczkę test_autopilot_models/test_autopilot_budget/test_session_controller: **149 passed in 0.55s**; platformy nie podano. Agent nie uruchamiał pytest. [Zakres i odbiór paczki](../archive/state-review-2026-10-21/A2b1.md). Pakiet nie jest podłączony do main/kolejki/panelu, nie dopasowuje pełnych reguł ani nie prowadzi trwałego dziennika, nie ma nadzoru wygaśnięcia bez wywołań. Nie stanowi działającej delegacji ani zmiany obecnych uprawnień.

**A2b2 — odłączony moduł zapisany i odebrany paczką testów:** [format reguł i ocena operacji](../specs/autopilot-rules.md) zaakceptowany. Zapisano autopilot/paths.py (leksykalne zakresy), arguments.py (ograniczony JSON i selektory), rules.py (zamknięty parser i HMAC), operations.py (niemutowalne serwerowe deskryptory/snapshoty) oraz matcher.py (pełne dopasowanie i bezpieczna decyzja). Cztery nowe pliki testów obejmują typy/pułapy, ścieżki/URL, HMAC, źródła/generacje/trust, owner ro, overwrite, rodziców create_tree i wszystkie wiązania batch. Kontrola AST/whitespace dziewięciu nowych plików i statyczne sprawdzenie nazw lokalnych importów poprawne. Użytkownik potwierdził paczkę sześciu plików test_autopilot_*: **298 passed in 0.73s**; platformy nie podano. Agent nie uruchamiał pytest ani importów runtime. [Zakres i odbiór paczki](../archive/state-review-2026-10-21/A2b2.md). Brak generacji zasobów w obecnym RootsRegistry nadal wymaga integracji A2b3. Walidator/matcher pozostaje odłączony; pozytywna ocena bez aktualnego guarda, trust, dziennika i synchronizacji nie uprawnia do wykonania. Bez zmian main/panelu/kolejki, konfiguracji, zależności i timeoutów.

## Zewnętrzne MCP — wdrożone, odbiór częściowy

Kontrakt v1: [zewnętrzne MCP](../specs/external-mcp-support.md). Trwałe profile/rewizje, panel administracji, katalog/test połączenia/trust i list_external_mcp/list_external_mcp_tools/call_external_mcp_tool są podłączone. Zaufanie jest sprawdzane przy dispatch, dotyczy zapisanych nazw i schematów; limiter 30/min, bez retry tools/call. Poufne argumenty/wyniki external_result są w RAM/TTL, nie SQLite ani telemetrii.

HTTP/SSE/stdio mają limity, TLS i kontrolę adresów; sprawdzone IP są przypinane do TCP z zachowaniem Host/SNI. Kolejne IP tylko po błędzie zestawienia TCP. Stdio używa ograniczonego środowiska i kontroli potomków (Windows Job Object/POSIX grupa procesów). To nie sandbox ani pełna ochrona SSRF.

Smoke 2026-10-19: po restarcie zachowane enabled/trust profilu web_mcp, katalog 9 narzędzi i list_open_tabs bez osobnej zgody, wynik external_result. Nie odbierano pełnego zapisanego zbioru trust ani nawigacji. Pozostałe ograniczenia: TLS/CA/Bearer i długa sesja SSE na rzeczywistym serwerze, autorytatywny DNS/NAT64/6to4/VPN, POSIX/zagnieżdżone joby i pełny przepływ M6.

## Pliki, skills i wiedza OKF — dostępne

Roots, kontrolowane zapisy plików/drzew/katalogów, kolejka i panel zgód oraz skills są wdrożone. Trust i dostępność skilli są niezależne. Skills mają jawny opt-in network-env=proxy-ca; nie oznacza on dziedziczenia całego środowiska ani zmiany transportu zewnętrznych MCP. Kontrakty: [pliki i zgody](../specs/files-and-approvals.md), [skills](../specs/skills-support.md).

Wiedza: panel, centralne i projektowe bundles, automatyczne bezpośrednie docs, profile docs/legacy/mixed, search/get/save, prepare_project, audyt i konwersja. Zwykły Markdown jest czytelny przed konwersją. Compatible domyślne; require_okf jawne i zakresowe, a poprawny OKF chroniony przed degradacją. Archiwum jest pomijane w indeksie projektu; standard OKF i przykłady nie są objęte konwersją lokalnych dokumentów. Kontrakty: [mapa OKF](../specs/okf-support.md).

Podpisany cache SQLite v2 → RAM, osobny klucz (DPAPI CurrentUser na Windows/0600 POSIX), worker, start cache_unchecked i uzgodnienie w tle są wdrożone. Pliki pozostają źródłem prawdy, baza nie jest szyfrowana. Knowledge_save/convert i zwykłe create/edit zgłaszają committed konkretne dokumenty; reszta RAM pozostaje dostępna.

Create_tree/rename nie zgłaszają przyrostowych zmian indeksu. Brak okresowej lekkiej inventory dla IDE/git pull i podłączenia purge po usunięciu zbioru. Pełne odświeżenie czyta źródła i nie usuwa niewidzianych wpisów SQLite. Wcześniejszy smoke wykazał ready/35→36 i aktualizację jednego dokumentu; bieżącej liczby wpisów nie sprawdzano przez działający MCP. W checkout brak docs/incremental-smoke-test.md.

## Zakres potwierdzonych testów i przekazanie

Wyniki zgłoszone wcześniej przez użytkownika; nie uruchamiano ich ponownie w tym przeglądzie:

| Zakres | Wynik i granica potwierdzenia |
|---|---|
| Sesje: aktywacja/bootstrap/wrapper/checkpoint/resume/panel/kolejka/recovery | 238 passed in 638.14s; atrapy/TestClient/DOM, nie pełny HEAD |
| Sesje: recovery; regresja backendu | 21 passed in 1.52s; 202 passed in 10.75s |
| Zewnętrzne MCP: adapter/wyniki/klient/trust/kolejka/sekrety | 293 passed in 21.60s; nie pełny M6 |
| Windows SDK stdio i cleanup z wnukiem | 3 passed in 22.45s; cleanup --runxfail 3 passed in 34.24s |
| Windows SDK HTTP/SSE po przypinaniu IP | 6 passed in 16.60s; lokalny serwer, nie TLS/CA |
| Rzeczywisty backend TCP z kontrolowanym resolverem | 3 passed in 1.86s; nie autorytatywny DNS |
| Cache: szybki start; przyrostowe save/convert; zwykłe create/edit | 182 passed/1 skipped; 172 passed/2 skipped; 234 passed/3 skipped |

Brak potwierdzonej pełnej regresji bieżącego HEAD; skip nie oznacza zaliczonego scenariusza Windows. Zmienność czasu paczek jest niewyjaśniona. Drobne poprawki oczekiwań testów i zakresy ponowień zachowano w archiwum. Ręczny odbiór panelu/MCP, junctionów i współbieżności pozostaje częściowy.

**Przegląd 2026-10-21:** odczyt kodu potwierdził podłączenie sesji i brak implementacji autopilota. Wykryto śledzony config.json (wbrew zasadzie lokalnej konfiguracji) oraz przypadkowy śledzony plik w korzeniu o nazwie zaczynającej się od `kills)`. To kwestie przygotowania publikacji; nie zmieniono konfiguracji ani indeksu Git. Instrukcja przekazania: [Windows](../windows-okf.md). W tym przeglądzie nie uruchamiano pytest, testów Node ani serwerów.
