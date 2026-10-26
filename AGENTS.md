# AGENTS.md

## Projekt
Nowy lokalny serwer MCP ("vibecoder") oparty na standardzie Agent Skills (agentskills.io). Udostępnia ubogiemu harnessowi pliki/roots, kolejkę i panel zgód, skills oraz wiedzę OKF. Ogólne wykonywanie komend pozostaje propozycją w backlogu. Integracja zewnętrznych MCP jest wdrożona: trwałe profile w panelu, trzy narzędzia modelu, zaufanie i poufne zadania. Kontrakt v1: `docs/specs/external-mcp-support.md`; zakres odbioru i ograniczenia: CURRENT, pozostałe prace: backlog. Nie oznacza to pełnej ochrony SSRF ani gotowości produkcyjnej.

## Stack
Python, FastMCP + Uvicorn (MCP obecnie 8101; historyczny cel 8100), panel webowy (127.0.0.1:8102: zatwierdzenia i konfiguracja folderów), Miniforge/conda (środowiska skryptów skills).

## Platformy
Development odbywa się na Linuksie; docelowy system MCP to Windows. Zachowujemy reguły ścieżek Windows i testy junctionów. Kod wewnętrzny podaje ścieżki względem przypiętego roota, aby działał także podczas developmentu POSIX. Venv, lokalne config.json, bazy zadań i dane wiedzy nie są przenoszone pomiędzy systemami przez Git.

## Status
Implementacja i odbiór testowy. Aktualny stan: `docs/plan/CURRENT.md`. Jedyny aktualny backlog: `docs/plan/backlog.md`. Przeniesienie wersji OKF i scenariusze Windows: `docs/windows-okf.md`.

## Dokumentacja
- `docs/specs/skills-support.md` – obsługa skills (spec + nasze rozszerzenia)
- `docs/decisions/` – ADR (decyzje architektoniczne)
- [Mapa docs](docs/README.md) – wybierz temat bez ładowania całości
- [Kontrakty OKF](docs/specs/okf-support.md) i [standard OKF 0.2](docs/specs/OKF/SPEC.md) – krótkie mapy dokumentów
- `docs/plan/` – tylko bieżący stan (`CURRENT.md`) i jeden backlog
- [Archiwum](docs/archive/README.md) – zakończone/dawne plany i wyniki sesji; nie jest listą prac

## Konwencje
- Jeden plik = jedna odpowiedzialność.
- Conventional Commits, wiadomości ekstremalnie zwięzłe.
- Plan i akceptacja użytkownika przed każdą zmianą kodu.
- Ręczne awaiting_approval: na każdy osobny task_id wolno wykonać JEDNO wait_task(seconds=6). Kolejne zadanie w tej samej sekwencji ma własne oczekiwanie do 6 s; zadanie obejmujące wiele plików ma jedno oczekiwanie, nie osobne dla każdego pliku. Nie ponawiać oczekiwania na tę samą zgodę ani nie tworzyć zadania w celu resetu limitu. Jeśli nadal brak decyzji, podsumować i STOP, czekać na wiadomość użytkownika bez kolejnych wait, anulowania lub resubmit. To polityka modelu, nie serwerowy licznik oczekiwań. queued/running po decyzji oznacza oczekiwanie na wykonanie, nie na człowieka: śledzić ten sam task_id, następny zależny krok dopiero po potwierdzonym sukcesie i w aktualnym zakresie zgody. Limit 8 s wait_task pozostaje limitem jednego wywołania, nie życia zadania; budżet autopilota osobno do uzgodnienia. Pauza/utrata zgody blokuje nowe kroki. Po :ping najpierw list_tasks, nie ponawiać w ciemno. Decyzja zawsze w panelu; Allow nie dowodzi sukcesu. Sekrety/poufne wyniki pobierać wyłącznie przez get_task, bez przedłużania TTL.
- Stary MCP (`MCP-my-codex`) jest referencją tylko do odczytu.
- Nie uruchamiaj pytest ani serwera API/uvicorn bez wyraźnego polecenia; podawaj komendę użytkownikowi.
- Python: venv/conda; instalacji i tworzenia środowisk nie wykonuj automatycznie.

## Źródła kontekstu i archiwum
Pracujemy bezpośrednio przez sklr. Zasady: `AGENTS.md`; aktualny stan i następny krok: `docs/plan/CURRENT.md`; kontrakty: właściwy temat w `docs/specs/`; wyniki historyczne: `docs/archive/`; decyzje: `docs/decisions/`. Bieżące sekcje stanu są nadrzędne wobec historii.
Nie aktualizujemy `.vibecoder_context.md` ani `.vibecoder_checkpoints.md` i nie wymagamy ich odczytu podczas pracy przez sklr. Oba pliki użytkownik przeniósł, bez zmiany nazw, do `docs/archive/vibecoder/`; obecność w archiwum i brak w korzeniu repo potwierdzono listowaniem. To wyłącznie historia, nie aktywny fallback.
Bieżący stan i zakres potwierdzonych testów zapisujemy zwięźle w CURRENT.md, zadania wyłącznie w backlog.md, historię w docs/archive/. Nie dopisujemy kolejnych wyników sesji do aktywnych specyfikacji ani nie tworzymy kopii wiedzy w docs/knowledge/. Przed awaryjnym podłączeniem starego vibecodera sprawdzamy jego wymagania kontekstu; nie przywracamy automatycznie nieaktualnego streszczenia.
