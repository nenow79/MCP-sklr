# Backlog (krok 6: development nowego MCP)

## Aktualne uwagi po obsłudze skilli (2026-10-13)
- [x] Zbiorcze tworzenie struktury katalogów i plików: create_tree, jedno zadanie tree_create, lista ścieżek i maskowane diffy, jedna decyzja. Implementacja #147-#149, testy tree_tools/folder_tools zielone wg użytkownika; smoke #150 done/exit0 (4 katalogi, 2 pliki), odczyty sprawdzone. Bez zmian manage_folder/create_file. Limity i ograniczenia rollbacku: README.md oraz CURRENT.md.
- [ ] Oddzielić testy logiki skilli od testów MCP: tests/ przy każdym skillu, własna instrukcja i środowisko; migracja test_cost_centers_skill.py i test_skill_validator.py po osobnym planie. Bez repo conftest i dokładania zależności serwera do środowisk skilli. Obecnie zewnętrzne ścieżki i skip przy braku skilla.
- [ ] TrustStore.trust/revoke starej ścieżki Ufaj i uruchom: brak rollbacku pamięci po błędzie save. Panel używa ConfigStore.update z rollbackiem; starszej ścieżki nie naprawiano w T1/T2.
- [ ] Dodać automatyczne testy filtrów źródeł Skille (#111): localStorage, brak/uszkodzone storage, domyślne źródła, licznik i pusty wynik, brak wpływu na dostępność/trust. Ręcznie działa wg użytkownika; testy po #111 niepotwierdzone.
- [ ] Cost-centers: poprawić zdanie SKILL.md o zgodzie po wprowadzeniu vibecoder-confirm=false (dev/project zawsze; central może pominąć przy zgodnym trust). Zmiana wymaga ponownego ręcznego deployu i zaufania nowemu hashowi.
- [ ] Sprawdzić task #109 i odczyt dev frontmatter po zakończeniu przekazania; centralną deklarację potwierdziła activate_skill i udana próba #110.

## Historyczna lista (nie wyznacza bieżącego stanu; tkinter wycofany)


- [ ] Plan implementacji i MVP (do akceptacji)
- [ ] `state.py`: `config.json` (referencje, zewnętrzne MCP, wyłączone skills, hashe zaufania, potwierdzenia, host/CORS)
- [ ] `gui/`: główne okno tkinter (Workspace, MCP, Skills, API, Log) w głównym wątku; serwer MCP w wątku w tle
- [ ] `ApprovalService`: kolejka zatwierdzeń (diff, komendy, skrypty) niezależna od UI, dźwięk/powiadomienie
- [ ] `skills_engine/`: scanner (centralny + projektowy), parser SKILL.md (tolerancja błędnego YAML), walidacja
- [ ] `skills_engine/`: trust (hash SKILL.md + scripts/), flaga `vibecoder-confirm` tylko dla katalogu centralnego
- [ ] `skills_engine/`: runner (conda python.exe, cwd = katalog skilla, timeout, limit outputu, bez shella)
- [ ] Narzędzia MCP: `list_skills`, `activate_skill`, `run_skill_script`, `save_progress`
- [ ] `policy.py`: twarde reguły (pytest, uvicorn) wymuszane w kodzie
- [ ] Przeniesienie narzędzi plikowych/komend/referencji/zewnętrznych MCP ze starego MCP (refaktor na moduły)
- [ ] `workspace_overview`: AGENTS.md + docs/plan/CURRENT.md; w fazie 7 rozstrzygnąć opcjonalny legacy fallback dla starych projektów. Archiwum vibecodera tego repo nie jest aktywnym źródłem.
- [ ] Skills startowe: złote zasady (git-commits, python-env, react-init, api-testing, change-planning), excel-analysis, project-bootstrap
- [ ] Domknięcie przełączenia na nowe MCP wg planu faz; aktywna praca już przez sklr. Archiwizacja wykonana ręcznie przez użytkownika: .vibecoder_context.md i .vibecoder_checkpoints.md w docs/archive/vibecoder/, brak w korzeniu repo potwierdzony listowaniem; bez dalszych aktualizacji.
