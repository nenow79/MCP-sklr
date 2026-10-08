# Backlog (krok 6: development nowego MCP)

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
- [ ] `workspace_overview`: AGENTS.md + docs/plan/CURRENT.md, fallback do `.vibecoder_context.md`
- [ ] Skills startowe: złote zasady (git-commits, python-env, react-init, api-testing, change-planning), excel-analysis, project-bootstrap
- [ ] Przełączenie na nowe MCP; usunięcie `.vibecoder_context.md`
