---
type: Specification
title: Podział komend i skills, Docker — propozycja
description: Niewdrożona propozycja podziału odpowiedzialności komend, skills i izolacji Docker.
tags: [sklr, commands, skills, proposal]
---
# Podział komend i skills, Docker — propozycja

Niewdrożone w `e6cee29`. [Status, korekty i mapa propozycji](command-execution-proposal.md) są częścią kontraktu. Wzmianki o ADR 0003 oznaczają planowany dokument. Testy opisują wymagania, nie wyniki.

### 4.5 Podział `execute_command` i skills
| Obszar | Gdzie | Zatwierdzanie |
|---|---|---|
| Ogólne `execute_command` (rzadkie, 7%) | kod, faza 5c | zawsze w panelu |
| git odczyt (`status`, `diff`, `log`) | skill, faza 8 (można wcześniej) | bez pytania (`vibecoder-confirm: false`, zaufany hash) |
| git zapis: `init` (nowe repo), `add`, `commit` (Conventional Commits), `push` | skill | `init`/`add` w panelu; `commit` i `push` zawsze w panelu |
| python: uruchamianie skryptów z venv, venv, `pip install -r`, upgrade pip | skill: uruchamianie przez zadanie z `venv\Scripts\python.exe`; tworzenie venv i instalacje zwracają gotową komendę dla użytkownika | uruchamianie w panelu; reszta tylko informacja |
| pytest | użytkownik w swoim terminalu (zasada 7); skill podaje komendę | brak |
| npm: nowy projekt, instalacja, `run build` | skill (instalacje jako zadania z dłuższym timeoutem, zasada 3) | w panelu |
| npm: `run dev` | użytkownik w terminalu (zasada 8 i brak procesów w tle); skill zwraca komendę | brak |
| docker | wyłącznie skill z listą dozwolonych podkomend (sekcja 4.5a) | w panelu; `config`, `ps`, `logs --tail` bez pytania |
| proxy i zmienne środowiskowe | skill `env-info` (odczyt z maskowaniem) | bez pytania dla odczytu |

Wniosek: `python`, `venv`, `activate`, `call`, `set` nie wymagają obsługi powłoki; zamiast aktywacji wywołujemy interpreter z venv. Procesy działające długo (`npm run dev`, serwery) zostają po stronie użytkownika; mechanizm zadań w tle (P6) wraca do rozważenia po fazie 8, jeśli okaże się potrzebny.

### 4.5a Docker: skill, nie `execute_command`
Podstawa: projekt referencyjny CSM (`docker-compose.yml`, `docker-compose.dev.yml`, `.env.example`, `start-dev.bat`, `start-prod.sh`). Docker daje de facto dostęp administratora do hosta, więc `docker` nie jest dozwolony w `execute_command` (blokada w `command_policy`); obsługuje go wyłącznie skill.

Ryzyka:
| # | Ryzyko | Środek |
|---|---|---|
| D1 | Montowanie (`-v C:\:/host`, bind mount w pliku compose) omija path guard i tryb `ro` | brak `docker run`, `-v`, `--mount` w poleceniu; `compose config` przed `up`/`build` wykrywa mounty poza rootami i pokazuje je w panelu |
| D2 | `privileged: true`, `security_opt: seccomp:unconfined`, `network_mode: host` (jest w dev CSM: mongo-express i frontend) | panel wyróżnia te ustawienia przy `up`/`build`; edycja plików compose to zadanie `sensitive` |
| D3 | `down -v` kasuje wolumeny (u CSM `mongo_data`, czyli dane bazy) | osobne ostrzeżenie w panelu, zawsze ręczne zatwierdzenie |
| D4 | `compose config` wstawia wartości z `.env`, więc ujawnia hasła | wynik maskowany (jak w odczycie plików); pełne wartości tylko przez `secret_reveal` |
| D5 | Porty 8100-8102 (MCP, panel) wystawione przez kontener lub przekierowane | `config` sprawdzany pod kątem tych portów (T3) |

Lista podkomend skilla `docker` (wstępna, do potwierdzenia przy fazie 8; plik compose wskazywany jawnie jak w Twoich komendach `-f docker-compose.dev.yml`):
- Bez pytania (odczyt): `compose config` (maskowany), `compose ps`, `compose logs --tail N` (bez `-f`).
- W panelu: `compose build`, `compose up -d`, `compose restart`, `compose cp` (tylko ścieżki wewnątrz rootów `rw`), `compose exec <usługa> <komenda>` z listy (`python -m unittest ...`, `npm install`, `npm run build`).
- Ostrzeżenie dodatkowe: `compose down` (z `-v` osobno, patrz D3), `compose up --build`.
- Zablokowane: `docker run`, `--privileged`, `-v`, `--mount`, `exec -it` (brak TTY), `compose exec` z dowolnym napisem poza listą.
- Komendy bez końca (`up` bez `-d`, `logs -f`): skill zwraca gotową komendę dla Twojego terminala (P6).
- Drugi projekt referencyjny (FastAPI + PostgreSQL) przyda się dopiero w fazie 8 do doprecyzowania podkomend bazy (`alembic`, `pg_dump`, `psql`); nie blokuje fazy 5.
