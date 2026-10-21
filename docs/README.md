---
type: Reference
title: Mapa dokumentacji sklr
description: Nawigacja po aktualnym stanie, kontraktach, decyzjach i archiwum projektu
  sklr.
tags:
- sklr
- documentation
- navigation
---
# Mapa dokumentacji

Zacznij od [AGENTS](../AGENTS.md) i krótkiego [CURRENT](plan/CURRENT.md). Otwarte prace są wyłącznie w [backlogu](plan/backlog.md). Nie trzeba czytać całego docs/ ani archiwum.

| Potrzeba | Czytaj |
|---|---|
| Uruchomienie i narzędzia bazowe | [README projektu](../README.md) |
| Aktualizacja i odbiór Windows | [Windows OKF](windows-okf.md) |
| Kontynuacja na innym komputerze | [Prompt startowy i punkt przekazania](continue-work.md) |
| Migracja starego projektu z plików .vibecoder do docs/ i OKF | [Instrukcja migracji](migracja-projektu-legacy-do-okf.md) |
| API wiedzy, panel, parser, bezpieczeństwo, konwersja | [Mapa lokalnych kontraktów OKF](specs/okf-support.md), potem jeden wybrany temat |
| Format OKF 0.2 | [Mapa standardu](specs/OKF/SPEC.md), potem wybrany rozdział |
| Skills i zaufanie | [Obsługa skills](specs/skills-support.md) |
| Pliki, roots, zgody i sekrety | [Bezpieczeństwo narzędzi](specs/files-and-approvals.md) |
| Sesje pracy — pauza, checkpoint RAM i wznowienie (opt-in) | [Kontrakt sesji](specs/work-session-pause.md) |
| Autopilot A2 — kontrakt zaakceptowany, niewdrożony | [Kontrakt autonomii](specs/autopilot-contract.md) |
| Reguły A2b2 — zaakceptowany format, moduł odłączony od wykonania | [Format reguł i ocena operacji](specs/autopilot-rules.md) |
| Autonomia — dalszy zakres i przygotowanie Windows | [Priorytety i warunki przekazania](plan/backlog.md) |
| Zewnętrzne MCP — wdrożony kontrakt v1, odbiór częściowy | [Profile, panel i zgody](specs/external-mcp-support.md) |
| Proponowane wykonywanie komend | [Propozycja i model zagrożeń](specs/command-execution-proposal.md) |
| Uzasadnienia decyzji | [Architektura](decisions/0001-architecture.md), [kolejka i panel](decisions/0002-task-queue-and-web-panel.md), [OKF](decisions/0004-okf-knowledge.md) |
| Wyniki dawnych sesji, stare plany | [Archiwum](archive/README.md) — tylko na żądanie |

CURRENT opisuje rzeczywisty stan i zakres potwierdzeń. Specyfikacje opisują kontrakty; propozycje są oznaczone. ADR zachowują uzasadnienia i historię zmian decyzji. Stare plany i ich checkboxy nie wyznaczają aktualnej pracy.

`specs/OKF/` (wielkie litery) zawiera standard, `specs/okf-integration/` lokalną integrację. Dokumenty źródłowe pozostają źródłem prawdy; indeks roboczy jest w pamięci, z podpisanym cache SQLite na dysku. Nie utrzymujemy równoległej kopii wiedzy w docs/knowledge/. Porządki nie są konwersją do OKF.
