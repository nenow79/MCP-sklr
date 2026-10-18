---
type: Reference
title: Obsługa OKF w sklr — mapa kontraktów
description: Nawigacja po lokalnych kontraktach integracji OKF i ich granicach.
tags: [sklr, okf, documentation, navigation]
---
# Obsługa OKF w sklr — mapa kontraktów

Baza implementacji: `e6cee29` (2026-10-15), rozszerzona o automatyczne podpinanie projektowego docs. Dostępność funkcji nie oznacza pełnego odbioru: [stan i testy](../plan/CURRENT.md).

Czytaj tylko dokument właściwy dla zadania:

| Temat | Dokument |
|---|---|
| Bundles, roots, limity, konfiguracja i panel Wiedza | [Bundles i panel](okf-integration/bundles-and-panel.md) |
| YAML, maskowanie, zaufanie i bezpieczeństwo | [Format i poufność](okf-integration/format-and-security.md) |
| Opaque revision, outline, section_id | [Rewizje i sekcje](okf-integration/revisions-and-sections.md) |
| knowledge_bundles/search/get, ranking i budżety | [Odczyt i wyszukiwanie](okf-integration/read-and-search.md) |
| knowledge_save, preview, zgody, konflikty i rollback | [Zapis](okf-integration/write.md) |
| Zgodność starszych projektów, wymóg OKF i ochrona przed utratą formatu | [Polityka zapisu](okf-integration/write-policy.md) |
| Obecny indeks RAM, task odświeżenia, partial i restart | [Indeks](okf-integration/index-and-refresh.md) |
| Cache SQLite → RAM, szybki start i zakres aktualizacji przyrostowych | [Trwały indeks](okf-integration/persistent-index.md) |
| Profile docs/legacy/mixed, prepare_project, audit/convert | [Projekt i konwersja](okf-integration/project-and-conversion.md) |
| Typy i zasady opracowywania wiedzy | [Kuracja](okf-integration/curation.md) |

[Standard OKF 0.2](OKF/SPEC.md) opisuje format, a powyższe dokumenty lokalne API i wymagania bezpieczeństwa. Katalog `OKF/` zawiera standard, a `okf-integration/` lokalną integrację sklr; nazwy nie różnią się wyłącznie wielkością liter.

Źródłami prawdy pozostają oryginalne dokumenty. Publiczny podpisany cache SQLite odtwarza indeks RAM przy starcie; dostęp i świeżość nie wynikają z cache. Nie tworzymy index.md ani kopii wiedzy w docs/knowledge/. Brak konwersji lub AGENTS nie blokuje pracy w domyślnym trybie zgodności. Jawny wymóg OKF dotyczy zapisu wybranego zakresu; odczyty nadal działają.

Pierwotny kontrakt C0 i jego aktualizacje zachowano w [archiwum](../archive/pre-okf-2026-10-15/specs/okf-support.md). Nie są listą prac do ponownego wdrożenia. Decyzje: [ADR 0004](../decisions/0004-okf-knowledge.md).
