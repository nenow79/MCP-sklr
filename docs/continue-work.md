---
type: Procedure
title: Kontynuacja pracy w sklr — selektywny kontekst
description: Krótka procedura wejścia i prompt dla persistent stdio bez ładowania historii projektu.
tags: [sklr, handoff, persistent-stdio, context]
---
# Kontynuacja ze świeżym kontekstem

## Zasady wejścia

Ten dokument jest procedurą, nie checkpointem ani zgodą wykonawczą. Stan i wyniki
są w [CURRENT](plan/CURRENT.md), jedyna lista otwartych prac w
[backlogu](plan/backlog.md), wymagania w odpowiednich docs/specs/.
Nie kopiować tutaj kolejnych wyników testów ani historycznych commitów.
Dawną treść zachowano w [archiwum](archive/continue-work-before-context-cleanup.md);
nie używać jej jako aktualnego promptu.

1. Sprawdź stan sesji oraz dostępne roots/skille. Stan active nie odtwarza zgód
   poprzedniego czatu. Pauza/handshake wymagają właściwej procedury sterowania;
   nie próbuj ich obchodzić przez odczyty robocze lub nowe zadania.
2. Użyj prepare_project dla faktycznego katalogu pracy. Przeczytaj pełne obowiązujące
   AGENTS, także zagnieżdżone, ale nie pobieraj ponownie instrukcji już zwróconych
   w całości. Doczytaj instrukcje truncated/incomplete.
3. Wybierz temat i następny etap. Czytaj OKF outline → potrzebne section,
   nie cały CURRENT/backlog i nie wszystkie dokumenty z odnośników. Przy znanej
   ścieżce nie trzeba najpierw przeszukiwać całego zbioru. Istotne wymagania i
   zależności doczytuj stopniowo; oszczędność kontekstu nie uzasadnia ich pominięcia.
4. Kod poznawaj przez symbol i potrzebne zakresy, potem zależności. Pełny dokument
   jest potrzebny m.in. przy pełnym update OKF; nie zapisuj samej sekcji jako body.
5. Ustal konkretną paczkę i aktualną akceptację. TDD: RED → osobno zaakceptowana
   minimalna implementacja → GREEN → odpowiednia regresja. Pytest wymaga wyraźnego
   polecenia i test-runner; przed zgłoszeniem sprawdź list_tasks. Historyczne
   wyniki nie są wynikiem bieżącej paczki ani powodem powtarzania bez przyczyny.

## Prompt: persistent stdio / Power Automate, etap P3c-A

Poniższy punkt startowy odpowiada stanowi po zapisie testów #2805, przed RED.
Jeśli od tego czasu powstały zmiany, obowiązuje świeży CURRENT i kod, nie ten
skrót. Nie traktuj promptu jako polecenia uruchomienia testów lub zapisu kodu.

```text
Kontynuujemy persistent stdio external MCP w sklr, docelowo dla Power Automate.
Bieżący punkt: P3c-A, testy zapisane #2805, 15 przypadków, RED niewykonane.

Sprawdź stan sesji, roots i skille. Aktywuj power-automate z dev-skills i przeczytaj
jego krótkie docs/START.md. Użyj prepare_project dla sklr i przeczytaj pełne
obowiązujące AGENTS, bez ponownego pobierania instrukcji zwróconych w całości.

Czytaj selektywnie przez OKF outline → section: sekcję persistent w
 docs/plan/CURRENT.md, wpis MCP-P3c-A w docs/plan/backlog.md oraz potrzebne sekcje
zaakceptowanego docs/specs/external-mcp-persistent-stdio-proposal.md:
właściciel/izolacja, supervisor i kontekst operacji, kontrole/granica dispatch,
lifecycle oraz kryteria P04–P07. Kontrakt v1, WorkSession/autopilot i plan domenowy
skilla doczytuj przy konkretnej zależności. Nie czytaj archiwów, całego
CURRENT/backlogu ani historii PoC na zapas.

Przejrzyj tests/external_mcp_persistent_admission_helpers.py oraz
 tests/test_external_mcp_persistent_admission.py i potrzebne fragmenty aktualnego
kodu. API testowe zaakceptowane, ale implementacja jeszcze nie powstała;
sprawdź aktualny stan. Najbliższy krok to RED na moje osobne wyraźne polecenie,
nie kolejny zapis tych samych testów ani ponowne projektowanie kontraktu.
Nie powtarzaj odebranych zakresów wskazanych w CURRENT bez konkretnej przyczyny.

Nowy czat nie dziedziczy zgody na kolejne zmiany ani testy. Pytest tylko na moje
wyraźne polecenie przez test-runner w istniejącym venv\Scripts\python.exe,
Python 3.14.7; przed zgłoszeniem sprawdź list_tasks, żeby nie dublować zadań.
Testy z procesami potomnymi wymagają osobnej zgody.

Bez instalacji, restartów, zmian profili/configu/upstream/SKILL.md i operacji
FlowAgent bez nowego uzgodnienia. Nie przełączaj flowagent ani nie usuwaj blokady
fallbacku persistent_runtime_required. Zapis flow nadal zablokowany.
Najpierw krótko potwierdź aktualny punkt i istotne blokady; nie odtwarzaj historii.
```

## Granice wykonania i harnessu

LLM prowadzi kolejne kroki rozpoczęte przez użytkownika; sklr dostarcza narzędzia,
stan i egzekwuje zgody. W tym harnessie skille i zewnętrzne MCP są dostępne przez
sklr; zamknięta bramka nie uprawnia do szukania alternatywnej ścieżki wykonania.
Blokada narzędzi nie zatrzymuje generowania tekstu, ale model ma zakończyć odpowiedź
zgodnie z komunikatem sterującym. sklr nie budzi LLM ani nie inicjuje nowych tur.
Przygotowanie wznowienia w panelu samo nie rozpoczyna pracy modelu.

Ręczny tryb/delegacja, sesja i trust są odrębne. Sam active/Allow/trust nie oznacza
sukcesu ani dowolnych uprawnień. Ręczne awaiting_approval: jedno wait_task do 6 s
na osobny task_id, potem STOP gdy brak decyzji. Po decyzji śledź to samo zadanie,
sukces sprawdź przed zależnym krokiem. Nie ponawiaj operacji o niepewnym skutku.
Nie przenoś configu/venv/baz/kluczy/sekretów między systemami ani nie odtwarzaj
historycznych executorów lub zgód. Git, instalacje, restart i zdalne skutki
wymagają właściwego aktualnego zakresu uzgodnienia.
