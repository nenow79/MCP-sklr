---
type: Implementation Plan
title: Aktualny backlog projektu sklr
description: Jedyna lista otwartych prac, odbiorów i propozycji dalszego rozwoju sklr.
tags: [sklr, backlog, planning]
---
# Aktualny backlog — 2026-10-16

Jedyna lista otwartych prac. Stan wdrożenia: [CURRENT](CURRENT.md); historyczne zadania i wyniki: [archiwum](../archive/README.md). C1–C3 i minimalne P0/P1/P2 są zaimplementowane w `e6cee29`; nie są ponownie otwartymi paczkami kodu.

## Odbiór i przygotowanie konwersji

- [ ] Dokończyć ręczny odbiór polityki formatu dokumentacji: stare projekty w compatible, zakres i wyjątki require_okf, restart i zmiana aliasu, zapis po zmianie polityki podczas oczekiwania na zgodę oraz scenariusze Windows. Testy potwierdzone przez użytkownika: paczka 8 plików z jednym błędem testu, następnie zielony test_project_knowledge po jego poprawce; 2 pominięcia specyficzne dla Windows.
- [ ] Dokończyć ręczny odbiór automatycznych docs: panelowy przełącznik i jego trwałość, dodanie/usunięcie roota, zniknięcie/powrót docs, pierwszeństwo ręcznych wpisów oraz junctiony Windows. Paczka 5 plików testowych z CURRENT przeszła według potwierdzenia użytkownika.
- [ ] Domknąć diagnostykę wydajności sklr-okf: task 351 potwierdził partial/26, 9 pominięć, scan_budget i refresh_budget po poprawce task 349. Zmierzyć inventory/read/parse/mask/access/publish i liczbę powtarzanych ocen zbiorów. Przyczyna partial to budżety; udział poszczególnych kosztów nie został zmierzony. Potwierdzić wynik paczki testów task 349; nie uznawać tego za pełny odbiór.
- [ ] Potwierdzić najnowsze rozszerzenia i regresje na Windows: testy i ręczny odbiór panelu/MCP, central/docs/legacy/mixed, restartu i junctionów według [instrukcji Windows](../windows-okf.md). Brak aktualnego pełnego pytest; starsze wyniki mają zakres zapisany w CURRENT.
- [ ] Dokończyć pilot centralny i projektowy oraz pomiar kontekstu katalog → search → outline/section wobec odczytu pełnych źródeł. Ręczne dodanie wiedzy centralnej jest potwierdzone, pełny pilot nie.
- [ ] Domknąć walidację wybranego zakresu konwersji: 22 dokumenty otrzymały metadane w taskach 339, 341, 343–345, 3 wcześniejsze OKF pozostawiono bez zmian. Potwierdzić format i kompletność po naprawie ograniczeń audytu/indeksu. Standard, przykłady i archiwum pozostają poza konwersją; bez docs/knowledge/ i bez automatycznego require_okf.

## Trwały indeks — zaakceptowany kierunek, niewdrożony

Kontrakt: [SQLite → RAM i indeksowanie przyrostowe](../specs/okf-integration/persistent-index.md). Plan nie jest raportem implementacji.

- [ ] Rozstrzygnąć miejsce i konfigurację knowledge_index.sqlite3 poza zbiorami, retencję/czyszczenie, schemat i bezpieczny fingerprint; uzupełnić kontrakt przed integracją.
- [ ] Dostarczyć cache SQLite: walidacja rekordów, limity, transakcje, wersje parsera/maskowania, odbudowa i testy sztucznych danych; bez FTS i zmiany rankingu.
- [ ] Dostarczyć dedykowany worker wiedzy partiami, ograniczoną kolejkę z deduplikacją, generacje SQLite/RAM, anulowanie i odrzucanie spóźnionych wyników. Optymalizować powtarzane oceny dostępu na podstawie pomiarów bez osłabienia guardów.
- [ ] Podłączyć committed konkretnych ścieżek dla knowledge_save, konwersji i zwykłych create/edit/tree oraz rename; zachować niepewne wyniki rollbacku jako wymagające uzgodnienia.
- [ ] Dodać start z cache_unchecked i okresową kontrolę zmian zewnętrznych, usunięć i pełnego uzgodnienia; odróżnić Odśwież od Przebuduj. Nie kasować niewidzianych wpisów po niepełnej inwentaryzacji.
- [ ] Przeprowadzić odbiór Windows: edycja jednego dokumentu parsuje jeden dokument, ciepły start korzysta z cache, aktualizacje i sekcje/revizje są spójne. Sprawdzić awarie SQLite/RAM, utratę dostępu, junctiony, restart, zmianę maskowania, brak treści w logach i koszty zimnego/ciepłego indeksowania.

## Potwierdzony dług i dalszy rozwój

- [ ] Rozdzielić testy zewnętrznych skilli od testów serwera: test_cost_centers_skill.py i test_skill_validator.py przenieść do skilli po osobnym planie; bez repo conftest i zależności serwera w środowiskach skilli.
- [ ] TrustStore.trust/revoke w starszej ścieżce „Ufaj i uruchom”: zapewnić rollback pamięci przy błędzie save. Panel używa ConfigStore.update z rollbackiem; starszej ścieżki nie naprawiano w T1/T2.
- [ ] Dodać automatyczne sprawdzenia filtrów źródeł Skille: localStorage, brak/uszkodzone storage, domyślne źródła, licznik i pusty wynik, niezależność od dostępności/trust. Ręczne działanie potwierdzono, testów po zmianie filtrów nie.
- [ ] Domknąć zewnętrzne skille z użytkownikiem: cost-centers SKILL.md po vibecoder-confirm=false (dev/project zawsze pytają; central zależy od trust), kontrola dev frontmatter/task #109, obsługa .ps1 w skill-writer validator/rules i ręczny deploy git-ops. To starsze niepotwierdzone czynności poza repo; najpierw sprawdzić, czy nadal potrzebne.

## Propozycje wymagające osobnego kontraktu i priorytetu

To kierunki, nie istniejące funkcje ani warunki uruchomienia:

- [ ] Rozszerzyć audyt/konwerter o jawny dowolny podkatalog, np. doc/, zgodnie z zakresem indeksowania profilem docs.
- [ ] Rozważyć semantyczny selector stanu i skill okf-curator, z zachowaniem [zasad kuracji](../specs/okf-integration/curation.md).
- [ ] Rozstrzygnąć opcjonalny hard guard projektu; obecnie brak AGENTS/konwersji nie blokuje pracy.
- [ ] Wrócić do niewdrożonego execute_command/command_policy i ADR 0003, integracji zewnętrznych MCP oraz dalszych skilli dopiero po odbiorze OKF. [Wymagania i granice bezpieczeństwa komend](../specs/command-execution-proposal.md) muszą poprzedzić kod.
- [ ] Rozważyć krótkie oczekiwanie na decyzję w łącznym budżecie 8 s. Obecnie obowiązują natychmiastowy task_id i reguła jednego wait_task z AGENTS; bez zmiany kontraktu nie wprowadzać pollingu.
