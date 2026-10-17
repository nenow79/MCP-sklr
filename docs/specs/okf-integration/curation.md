---
type: Specification
title: "OKF: konwencja kuracji"
tags: [sklr, okf, curation]
---
# OKF: konwencja kuracji

Kontrakt lokalny sklr dla implementacji w `e6cee29`; odbiór i ograniczenia testów: [CURRENT](../../plan/CURRENT.md).
Mapa i pozostałe kontrakty: [obsługa OKF](../okf-support.md). Standard formatu: [OKF 0.2](../OKF/SPEC.md).

### 5.1 Konwencja kuracji i kontrola rozrostu typów

Uzgodniona polityka opracowywania wiedzy przez LLM: typy są otwarte, ale nowe lokalne typy wprowadzamy wyłącznie za jawną zgodą użytkownika. To konwencja kuracji, nie zamknięta lista wartości w parserze OKF ani nowy mechanizm API. Nieznane typy istniejących/importowanych dokumentów pozostają poprawne; nie zmieniamy ich automatycznie.

- type określa rodzaj i cel dokumentu, a tags oraz ścieżka jego temat. SAP, RedLake, Oracle, M-BO, fabryka i Bosch IT są tematami, nie powodem do tworzenia osobnych wariantów każdego typu.
- Mały słownik startowy: Reference (opis/fakty), Process Description (jak działa proces), Procedure (wykonanie zadania krok po kroku), Data Dictionary (znaczenie i relacje danych), Specification (wymagania/kontrakty), Troubleshooting Guide (diagnostyka i rozwiązania), Link Collection (kuratorowana lista odnośników). Typy projektowe, np. Architecture Decision, Implementation Plan, Project State i Playbook, pozostają dostępne obok nich.
- LLM najpierw sprawdza istniejące typy według znaczenia, unikając synonimów i typów tworzonych tylko dla nowej dziedziny. Przy niepewności proponuje szeroki Reference, jeśli pasuje; gdy nie pasuje, pozostawia klasyfikację do decyzji zamiast wymuszać wybór.
- Gdy żaden istniejący typ nie pasuje, LLM wnioskuje do użytkownika: proponowana nazwa i definicja, dlaczego istniejące typy są niewystarczające, przykłady dokumentów oraz korzyść dla struktury lub wyszukiwania. Nie ustanawia typu samodzielnie.
- Dopiero jawna zgoda użytkownika pozwala dodać nowy typ do lokalnej konwencji i proponować jego użycie w nowych dokumentach. Brak zgody oznacza brak wprowadzenia/użycia proponowanego typu. Zgoda na sam zapis dokumentu nie zastępuje zgody na nowy typ, jeśli wniosek nie został jawnie przedstawiony.
- Zmiany typów istniejących dokumentów i konsolidacja synonimów wymagają osobnego uzgodnienia oraz standardowej zgody na zapis; bez automatycznego przeklasyfikowania.
- Konwencję sprawdzamy na małej próbce rzeczywistych dokumentów, nie projektujemy pełnej taksonomii z góry. W przyszłym skillu kuracji należy stosować tę politykę; nie dodajemy klasyfikatora do C2 i nie tworzymy teraz produkcyjnych bundles/danych.


Ta konwencja obowiązuje przy opracowywaniu dokumentów przez LLM. Parser nie wymusza słownika typów. Nie ma wdrożonego klasyfikatora ani skilla okf-curator; proponowane prace wymienia [backlog](../../plan/backlog.md).
