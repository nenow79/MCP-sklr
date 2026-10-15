> **Archiwum sprzed porządków 2026-10-15.** To historia, nie bieżący stan, instrukcje ani backlog. [Mapa archiwum](../../README.md). Treść źródłowa poniżej; poprawiono wyłącznie ścieżki odnośników.

# Backlog (krok 6: development nowego MCP)

## OKF — bieżący kierunek

- [x] Implementacja kandydata Windows: C3c panel Wiedza, project docs/legacy/mixed, plain Markdown read/search, prepare_project i szablon AGENTS do zgody, audyt/konwersja obu układów. Autoryzacja użytkownika: domknąć wersję bez blokowania na konwersji. Polityka advisory; szczegóły CURRENT i docs/windows-okf.md. Odbiór nowych testów nadal otwarty.
- [ ] Publikacja C2c-C3/projekty (agent ma .git tylko do odczytu), końcowe testy Windows, junctiony i ręczny odbiór panelu/MCP/migracji. Manifest przekazania i instrukcja w docs/windows-okf.md.
- [ ] Dalsze ulepszenia po odbiorze Windows: semantyczna kuracja historii/selector bieżącej sekcji, opcjonalny okf-curator, opcjonalny hard guard projektu dopiero po osobnym kontrakcie. Nie blokują pracy ani konwersji.
- [x] Plan i dokumentacja C0: docs/archive/pre-okf-2026-10-15/plan/okf.md (#152), ADR 0004 i docs/specs/okf-support.md (#153) oraz domknięcie statusów/limitów/przekazania (#154): allow/done/exit0, wyniki i zapisy sprawdzone odczytem. Kierunek i kontrakt zaakceptowane w rozmowie; implementacja dopiero na nowym chat.
- [x] C1a: modele, bezpieczny parser YAML/UTF-8, publiczny widok po maskowaniu Markdown i rewizje HMAC; samodzielne testy (145, zielone wg użytkownika). Commit 36371ae. Bez integracji serwera.
- [x] C1b: rejestr centralnych bundles, ścieżki/root binding, limity/defaults i testy; poprawki bezpieczeństwa #189 zapisane. Wynik użytkownika: 261 passed, 2 skipped (rzeczywista kolizja nazw i utworzenie linku niedostępne); symulacje kolizji/reparse przeszły. Bez automatycznego dostępu do ~/.agents/, bez integracji MCP. Commit 22a3cd1; pełny pytest po C1b niewykonany.
- [x] C2a: sections.py i test_okf_sections.py; publiczne body -> ATX/Setext, pomijanie kodu, s0/s1..., hierarchia, direct/subtree, fizyczne linie i limit 2000. Odbiór użytkownika: 101 passed dla sections/public_view; commit 1395a06.
- [x] C2b: documents/scanner/index/search na publicznej treści, limity/ranking/partial i atomowe generacje. Wyniki użytkownika: 118 passed; pozostała paczka silnika 377 passed, 2 skipped. Commit 4462aab potwierdzony w Git.
- [x] C2c: service, knowledge_bundles/search/get, TaskManager refresh, main.py, testy integracji; dostępność, rewizje, confidential i budżety JSON. Odbiór użytkownika: 79 passed in 1.36s. Commit oczekuje; pełny pytest i ręczny test po restarcie niepotwierdzone. Szczegóły w CURRENT.md.
- [ ] C3: knowledge_save, maskowany preview, zgody/revisions/conflicts i panel Wiedza.
- [x] C3a: writer/preview i testy. Wynik użytkownika: 1 failed, 208 passed in 2.03s; po poprawce wyłącznie testu ponowienie preview: 21 passed in 0.22s. Odebrane testowo, commit oczekuje. Bez nowego pełnego przebiegu paczki/repo; szczegóły w CURRENT.md.
- [x] C3b: knowledge_save z kolejką/transakcjami, guard, dokładny snapshot i odświeżanie indeksu. Pierwszy wynik 26 failed, 294 passed, 1 skipped; po poprawce stagingu na Linuksie transakcje/zapisy: 86 passed, 1 skipped in 2.17s (junction Windows). Odebrane na Linuksie; testy docelowego Windows i commit oczekują. Szczegóły w CURRENT.md.
- [x] C3c: panel Wiedza — administracja bundles i indeksu zaimplementowana po szerokiej autoryzacji domknięcia wersji. Odbiór API/UI na Windows otwarty.
- [x] P0 advisory: prepare_project, root/nested AGENTS, szablon brakującego pliku do zgody, audyt i wskazówki startowe. Brak hard guarda i dowodu przeczytania zasad przez LLM.
- [x] P1/P2 minimalne: profile docs/legacy/mixed, plain Markdown read/search oraz audyt/konwersja frontmatter obu układów. Body/komentarze/BOM/EOL zachowane; reorganizacja historii osobno. Nowe testy oczekują na wykonanie na Windows.
- [ ] C4: produkcyjny root/bundle wybrany z użytkownikiem, pilot i pomiar kontekstu. Odbiór centralnej wiedzy przed projektem.
- [ ] P1-P4: projektowe docs, tryb migracyjny, audit/convert, skill okf-curator i pilot dokumentacji sklr; szczegóły osobno przed etapem projektowym.
- [x] Użytkownik potwierdził commit create_tree i zielony pełny pytest; brak logu/liczby testów. Testy sklr we własnym venv, bez conda; agent testów nie uruchamiał.
- [x] Commit dokumentacji C0 potwierdzony (51818d8). Wybór rzeczywistych danych C4 nie blokuje C1.

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
