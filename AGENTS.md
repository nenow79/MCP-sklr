# AGENTS.md

## Projekt
Nowy lokalny serwer MCP ("vibecoder") oparty na standardzie Agent Skills (agentskills.io). Zamienia ubogi harness przeglądarkowy w agenta: pliki/workspace, komendy z guardami, referencje, zewnętrzne MCP oraz skills (katalog, aktywacja, uruchamianie skryptów).

## Stack
Python, FastMCP + Uvicorn (port 8100), panel webowy (127.0.0.1:8102: zatwierdzenia i konfiguracja folderów), Miniforge/conda (środowiska skryptów skills).

## Platformy
Development odbywa się na Linuksie; docelowy system MCP to Windows. Zachowujemy reguły ścieżek Windows i testy junctionów. Kod wewnętrzny podaje ścieżki względem przypiętego roota, aby działał także podczas developmentu POSIX. Venv, lokalne config.json, bazy zadań i dane wiedzy nie są przenoszone pomiędzy systemami przez Git.

## Status
Implementacja i odbiór testowy. Aktualny stan: `docs/plan/CURRENT.md`. Zadania: `docs/plan/backlog.md`. Przeniesienie wersji OKF i scenariusze Windows: `docs/windows-okf.md`.

## Dokumentacja
- `docs/specs/skills-support.md` – obsługa skills (spec + nasze rozszerzenia)
- `docs/decisions/` – ADR (decyzje architektoniczne)
- `docs/plan/` – bieżący stan (`CURRENT.md`), backlog, plan implementacji (`implementation.md`)

## Konwencje
- Jeden plik = jedna odpowiedzialność.
- Conventional Commits, wiadomości ekstremalnie zwięzłe.
- Plan i akceptacja użytkownika przed każdą zmianą kodu.
- Zadania do zatwierdzenia w panelu (edit_file, create_file, manage_folder, secret_reveal, request_root): po task_id wolno wykonać JEDNO wait_task (seconds=8); gdy status nadal awaiting_approval, napisać użytkownikowi i STOP (bez pętli i kolejnego wait_task). Po :ping najpierw list_tasks, nie ponawiać w ciemno. Decyzja zawsze po stronie użytkownika w panelu; sprawdzać rzeczywisty status i zawartość pliku.
- Stary MCP (`MCP-my-codex`) jest referencją tylko do odczytu.
- Nie uruchamiaj pytest ani serwera API/uvicorn bez wyraźnego polecenia; podawaj komendę użytkownikowi.
- Python: venv/conda; instalacji i tworzenia środowisk nie wykonuj automatycznie.

## Źródła kontekstu i archiwum
Pracujemy bezpośrednio przez sklr. Zasady: `AGENTS.md`; aktualny stan i następny krok: `docs/plan/CURRENT.md`; kontrakty i wyniki: plan bieżącej fazy; decyzje: `docs/decisions/`. Bieżące sekcje stanu są nadrzędne wobec historii.
Nie aktualizujemy `.vibecoder_context.md` ani `.vibecoder_checkpoints.md` i nie wymagamy ich odczytu podczas pracy przez sklr. Oba pliki użytkownik przeniósł, bez zmiany nazw, do `docs/archive/vibecoder/`; obecność w archiwum i brak w korzeniu repo potwierdzono listowaniem. To wyłącznie historia, nie aktywny fallback.
Stan zapisujemy w CURRENT.md i wynikach planu fazy, bez dodatkowego pliku checkpointów. Przed awaryjnym podłączeniem starego vibecodera sprawdzamy jego wymagania kontekstu; nie przywracamy automatycznie nieaktualnego streszczenia.
