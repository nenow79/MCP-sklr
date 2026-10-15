# Wymagane testy komend — propozycja

Niewdrożone w `e6cee29`. [Status, korekty i mapa propozycji](command-execution-proposal.md) są częścią kontraktu. Wzmianki o ADR 0003 oznaczają planowany dokument. Testy opisują wymagania, nie wyniki.

### 4.6 Testy automatyczne (`pytest` uruchamia użytkownik)
- `command_policy`: tabela przypadków (zablokowane, dozwolone, ostrzeżenie), w tym obejścia składniowe: wielkość liter, `.exe`, cudzysłowy, `--port=8102`, `127.0.0.1:8102/api`, `[::1]:8102`, `localhost.`, zapis dziesiętny/hex IP, `cd` z `/d`, ścieżki ze spacjami.
- Tłumaczenie `cd`: poprawny wzorzec, `cd` poza rootem, `cd` do roota `ro`, `cd a && b && c`, `cd a; b`.
- Tokenizer: metaznaki, cudzysłowy, nowe linie, Unicode (RLO), bardzo długie wejścia.
- Brak tokenu panelu w `clean_env()`, configu, `tasks.db`, odpowiedziach narzędzi.
- Maskowanie sekretów: `.env` z `*_PASSWORD`, `*_TOKEN`, `://user:pass@host`; `search_code` po `PASSWORD` nie zwraca wartości; `.env.example` nie jest maskowany; `masked=true` i licznik; `secret_reveal` wymaga zatwierdzenia.
- `command_env_passthrough`: przekazywane tylko istniejące zmienne z listy; nazwa pasująca do `TOKEN|SECRET|PASSWORD|KEY` odrzucona przy zapisie listy; zmienne tokenu panelu/MCP nigdy nie trafiają do procesu potomnego.
- Blokada `.bat`/`.cmd`/`.ps1` i `docker` w `execute_command` (z komunikatem "użyj skilla").
- Path guard (lista z 2.4), TOCTOU zapisu, atomowość, rollback `edit_file` przy błędzie w jednej z wielu zmian.
- Roots: walidacja (profil użytkownika, katalog główny dysku, zagnieżdżenia), tryb `ro` blokuje zapis, migracja ze starego configu, `request_root` bez efektu przed zatwierdzeniem.
- Integracja: `edit_file` -> `awaiting_approval` -> `allow` -> zapis; `deny` -> plik nietknięty; `cancel` w oczekiwaniu; zmiana pliku po wygenerowaniu diffu -> `failed`.
- Panel: render każdego `kind`, brak "Ufaj" dla `command`/`sensitive`/`root_change`, `/api/roots` wymaga sesji i `Origin`/`Host`.
- Migracja `tasks.db`: stara baza otwiera się, stare zadania czytelne. Regresja `exit_code=None`. Komenda czekająca na wejście kończy się timeoutem, nie zawieszeniem.

### 4.7 Testy ręczne (z harnessa)
1. Dodanie roota `rw` i `ro` w panelu; model widzi je w `list_roots`; zapis do `ro` odrzucony.
2. `edit_file` z wieloma plikami: jeden diff, decyzja po dłuższej przerwie (> 8 s), zapis lub odrzucenie.
3. `execute_command` `git status` w panelu; `cd x && git status` przetłumaczone na `cwd`.
4. `curl http://127.0.0.1:8102/api/tasks`: odrzucone przez policy; po tymczasowym wyłączeniu guardu 401 (potwierdzenie T1).
5. `python -m http.server 8102` przy działającym panelu: zablokowane.
6. Start sklr przy zajętym 8102: brak linku z tokenem, serwer kończy start bez fallbacku.
7. Token jednorazowy: ponowne użycie linku nie działa; historia przeglądarki nie daje dostępu.
8. Edycja `panel/app.py` jako `sensitive`: plakietka, brak "Ufaj".
