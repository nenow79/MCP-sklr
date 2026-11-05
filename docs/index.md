# Start

* [AGENTS](../AGENTS.md) - zasady pracy agentów w projekcie; czytaj w pierwszej kolejności
* [README projektu](../README.md) - opis, instalacja, uruchomienie i narzędzia bazowe
* [CURRENT](plan/CURRENT.md) - aktualny stan i zakres potwierdzonej weryfikacji
* [Backlog](plan/backlog.md) - jedyna lista otwartych prac

Nie trzeba czytać całego docs/ ani archiwum. Wybierz jeden temat poniżej.

# Uruchomienie i przekazanie pracy

* [Windows OKF](windows-okf.md) - aktualizacja i odbiór na Windows
* [Prompt startowy i punkt przekazania](continue-work.md) - kontynuacja na innym komputerze
* [Migracja projektu legacy do OKF](migracja-projektu-legacy-do-okf.md) - przejście ze starych plików .vibecoder do docs/ i OKF

# Kontrakty i specyfikacje

* [Mapa lokalnych kontraktów OKF](specs/okf-support.md) - API wiedzy, panel, parser, bezpieczeństwo, konwersja; potem jeden wybrany temat
* [Standard OKF 0.2](specs/OKF/SPEC.md) - mapa standardu; potem wybrany rozdział
* [Obsługa skills](specs/skills-support.md) - skills i zaufanie
* [Bezpieczeństwo narzędzi](specs/files-and-approvals.md) - pliki, roots, zgody i sekrety
* [Projekty i sesje](specs/work-session-pause.md) - profile projektów, świeże sesje, pauza i wznowienie (sesje opt-in); zakres odbioru w CURRENT
* [Kontrakt autonomii](specs/autopilot-contract.md) - zaakceptowany kontrakt delegacji A2; implementacja i odbiór w CURRENT
* [Format reguł autopilota](specs/autopilot-rules.md) - zaakceptowany format i ocena operacji A2
* [Zewnętrzne MCP](specs/external-mcp-support.md) - wdrożony kontrakt v1: profile, panel i zgody; odbiór częściowy
* [Wykonywanie komend](specs/command-execution-proposal.md) - propozycja i model zagrożeń

# Decyzje i plany

* [Architektura](decisions/0001-architecture.md) - ADR 0001
* [Kolejka i panel](decisions/0002-task-queue-and-web-panel.md) - ADR 0002
* [OKF](decisions/0004-okf-knowledge.md) - ADR 0004
* [Priorytety autonomii i warunki przekazania](plan/backlog.md) - dalszy zakres i przygotowanie Windows

# Historia

* [Archiwum](archive/README.md) - wyniki dawnych sesji i stare plany; tylko na żądanie, nie jest listą prac

# Zasady korzystania

CURRENT opisuje rzeczywisty stan i zakres potwierdzeń. Specyfikacje opisują kontrakty; propozycje są oznaczone. ADR zachowują uzasadnienia i historię zmian decyzji. Stare plany i ich checkboxy nie wyznaczają aktualnej pracy.

`specs/OKF/` (wielkie litery) zawiera standard, `specs/okf-integration/` lokalną integrację. Dokumenty źródłowe pozostają źródłem prawdy; indeks roboczy jest w pamięci, z podpisanym cache SQLite na dysku. Nie utrzymujemy równoległej kopii wiedzy w docs/knowledge/. Porządki nie są konwersją do OKF.
