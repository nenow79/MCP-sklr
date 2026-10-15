# Aktualny backlog — 2026-10-15

Jedyna lista otwartych prac. Stan wdrożenia: [CURRENT](CURRENT.md); historyczne zadania i wyniki: [archiwum](../archive/README.md). C1–C3 i minimalne P0/P1/P2 są zaimplementowane w `e6cee29`; nie są ponownie otwartymi paczkami kodu.

## Odbiór i przygotowanie konwersji

- [ ] Dokończyć ręczny odbiór polityki formatu dokumentacji: stare projekty w compatible, zakres i wyjątki require_okf, restart i zmiana aliasu, zapis po zmianie polityki podczas oczekiwania na zgodę oraz scenariusze Windows. Testy potwierdzone przez użytkownika: paczka 8 plików z jednym błędem testu, następnie zielony test_project_knowledge po jego poprawce; 2 pominięcia specyficzne dla Windows.
- [ ] Dokończyć ręczny odbiór automatycznych docs: panelowy przełącznik i jego trwałość, dodanie/usunięcie roota, zniknięcie/powrót docs, pierwszeństwo ręcznych wpisów oraz junctiony Windows. Paczka 5 plików testowych z CURRENT przeszła według potwierdzenia użytkownika.
- [ ] Wyjaśnić `sklr-okf`: partial i 3 dokumenty. Zebrać reasons, stan taska odświeżenia, limity i faktyczny zakres/wykluczenia. Przyczyna nieustalona; nie uznawać tego za pełny odbiór.
- [ ] Potwierdzić najnowsze rozszerzenia i regresje na Windows: testy i ręczny odbiór panelu/MCP, central/docs/legacy/mixed, restartu i junctionów według [instrukcji Windows](../windows-okf.md). Brak aktualnego pełnego pytest; starsze wyniki mają zakres zapisany w CURRENT.
- [ ] Dokończyć pilot centralny i projektowy oraz pomiar kontekstu katalog → search → outline/section wobec odczytu pełnych źródeł. Ręczne dodanie wiedzy centralnej jest potwierdzone, pełny pilot nie.
- [ ] Wybrać zakres ewentualnej konwersji uporządkowanych źródeł po przeglądzie audytu. Bez masowej konwersji standardu, przykładów i archiwum; bez kopii w docs/knowledge/. To odrębna czynność od zakończonych porządków dokumentacji.

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
