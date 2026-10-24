---
type: Procedure
title: Kontynuacja pracy nad autonomią sklr na innym komputerze
description: Kontynuacja po lokalnej implementacji A2b4; priorytet walidacji i odbioru autonomii.
tags: [sklr, autonomy, handoff, windows]
---
# Prompt startowy

## Najnowsze przekazanie: pełna regresja Windows po 58c7986

Przed historycznym promptem poniżej przeczytaj pierwszą sekcję `docs/plan/CURRENT.md`, **D15 w `docs/plan/backlog.md`** i oczyszczony `docs/full_tests_20261224.txt`. Pełne repo: **20 failed, 3231 passed, 31 skipped / 1:22:40**; A/B/C i Node wcześniej przeszły. Nie deklaruj D14 naprawionego: błędy wróciły w pełnym przebiegu. Nie zaczynaj od kolejnej 82-minutowej regresji. Kolejność: błędne przypadki i izolacja → zaakceptowana naprawa → dedykowane testy → moduł → mała regresja → pełne repo na końcu. D15 zawiera wszystkie 20 nazw, znane D7/D8, tropy i ograniczenia. Bez zmian kodu/środowiska lub uruchamiania pytest/serwerów bez wymaganej zgody. Najnowsze wyniki i priorytety są nadrzędne wobec historycznego promptu.

Po aktualizacji checkoutu i sprawdzeniu gałęzi/commita wklej tekst poniżej do nowej rozmowy. Ten dokument jest instrukcją wejścia; bieżące wyniki i zadania pozostają w [CURRENT](plan/CURRENT.md) i [backlogu](plan/backlog.md).

```text
Kontynuujemy pracę nad MCP-sklr. Celem jest beta autonomicznej pracy według
zaakceptowanego kontraktu A2, a potem odbiór na Windows z działającym MCP/panelem.

Zacznij od AGENTS.md, docs/plan/CURRENT.md i docs/plan/backlog.md.
Następnie przeczytaj docs/specs/autopilot-contract.md,
docs/specs/autopilot-rules.md i odpowiednie fragmenty docs/windows-okf.md.
Dokumentacja docs/ ma format OKF z metadanymi YAML.

Baza lokalnej paczki: 425f91f, gałąź main. S1/A2b1/A2b2 są w bazie; A2b3/A2b4
są zaimplementowane po akceptacji planów; kandydat obejmuje także W1–W3.
Commit przekazania identyfikuje raport release-preparation.md w archiwum
state-review-2026-10-21. Sprawdź faktyczny stan publikacji W4 w CURRENT.
Aktualna paczka S6 usuwa checkpoint RAM, jego narzędzie MCP i osobne
potwierdzenie braku checkpointu; pauza i handshake wznowienia pozostają.
Przed pracą przeczytaj najnowszą sekcję CURRENT oraz bieżący kontrakt sesji.
Po aktualizacji odśwież panel i katalog MCP; nie odtwarzaj usuniętej funkcji.
Zachowaj wszystkie te zmiany. A2b4 podłącza runtime opt-in do main/bootstrap,
panelowe API/UI decyzji, watchdog i bezpieczne zamknięcie. Zawartości config.json nie zmieniono;
domyślnie autopilot.enabled=false. Nie uruchamiano docelowej aplikacji; testy A2b4 zaliczone (wyniki poniżej).
Kontrakt A2, format A2b2 i plan A2b4 są zaakceptowane.
Końcowa wcześniejsza paczka A2b3 Linux: 820 passed in 24.67s. Pełny wcześniejszy przebieg:
30 failed, 3166 passed, 10 skipped, przed poprawką wyścigu A2b3; całego przebiegu
nie powtórzono. Nie deklaruj zielonej pełnej regresji ani odbioru Windows.
Szczegóły: docs/archive/state-review-2026-10-21/A2b3.md.

Priorytet zatwierdzony przez użytkownika: praca autonomiczna według kontraktu.
Przegląd A2b3 oraz plan i implementacja A2b4 są wykonane; szczegóły w CURRENT
i backlogu. Walidacja A2b4 zakończona po poleceniu użytkownika: 48 passed in 1.50s;
szersza regresja 941 passed in 59.45s; DOM Node 4/4. Python 3.14.4/Linux,
istniejący venv, pytest poza sandboxem. Zakres: raport A2b4 w archiwum.
Najbliższy krok: A2b5 (ręczna procedura w docs/windows-okf.md) i przygotowanie
przekazania W1–W4. Nie powtarzaj zaliczonych paczek bez nowej przyczyny.
Zapisano test_autopilot_activation.py, test_autopilot_admin.py,
test_panel_autopilot.py i test_panel_autopilot_view.cjs.
Nie pytaj ponownie o przyjęcie kontraktu A2/planu A2b4 i nie implementuj ich od nowa.

D6–D8 (ścieżki POSIX, atrapy rejestracji, wyścig anulowania cache) pozostają
w backlogu. Nie zaczynaj od naprawienia wszystkich 29 pozostałych błędów:
nie są automatycznym warunkiem A2b4. Włącz konkretną poprawkę do głównej ścieżki
tylko po wykazaniu blokady używanego scenariusza autonomii, zgód lub poufności.
Plan zbiorczej naprawy D6–D8 nie został zaakceptowany, kod tych poprawek nie powstał.

A2b5 — pełna regresja i rzeczywisty odbiór Windows pozostają otwarte.
A3/A3b (w tym długie testy) i A4 (cykle poprawek) pozostają osobnymi kontraktami.
Nie myl 8 s wait_task, timeoutu zadania i terminu ważności delegacji.
Obecne wykonawce skills/MCP mają domyślne 60 s i maksimum 300 s.

Sprawdź git status, gałąź i ostatni commit przed pracą; zachowaj lokalne zmiany.
W1 zakończone lokalnie po akceptacji: config.example.json, ignorowanie configu,
usunięcie config.json wyłącznie ze śledzenia (plik lokalny bez zmian) i procedura
migracji Windows z kopią poza checkoutem. Zachowaj wyłączenie ze śledzenia; nie dodawaj
configu ponownie. W2 zakończone (przypadkowy plik wyłączony ze śledzenia, kopia lokalna zachowana).
W3 Linux zaliczone: dodatkowo 277 passed/2 skipped, panel Node 8/8 plików, pip check OK.
Publikacja W4 i rzeczywisty odbiór W5 pozostają otwarte.
Kontrole W1 poprawne; PowerShell/migracja Windows jeszcze niewykonane.
Nie przenoś venv/conda, baz, kluczy, danych wiedzy ani sekretów między systemami.
W poprzednim czacie użytkownik wyjątkowo zezwolił na lokalne venv, instalację
zależności i testy; venv istnieje lokalnie i jest ignorowane. Nie traktuj tego
jako trwałej zmiany AGENTS ani zgody na uruchomienie docelowego MCP/panelu.
Pytest ani serwera API/uvicorn nie uruchamiaj bez wyraźnego polecenia; podawaj
komendy do wykonania w istniejącym środowisku. Nie instaluj i nie twórz środowisk
automatycznie. Prace i kolejne plany zapisuj tylko w backlogu, krótki stan
i potwierdzone wyniki w CURRENT, zakończone szczegóły w archiwum.
Nie przywracaj dawnych .vibecoder_context.md/.vibecoder_checkpoints.md do korzenia.
```

Przed aktualizacją na drugim komputerze sprawdź lokalny git status i zachowaj kopię config.json poza checkoutem. Przy lokalnych zmianach nie używaj reset --hard ani nadpisania configu. Po bezpiecznej aktualizacji commit punktu przekazania identyfikuj przez git log -1 --oneline i porównaj z hashem podanym przy publikacji. Szczegóły docelowego odbioru: [Windows](windows-okf.md).

## Kluczowe doprecyzowanie: obecny ubogi harness

Użytkownik potwierdził, że pracujemy na bazie commitów `425f91f` (`feat: add autopilot policy foundation`) i `a6c564c` (`feat: enable autopilot`). To baza implementacji, nie potwierdzenie pełnej regresji ani rzeczywistego odbioru Windows. Zachowaj ich kod i kontrakty; nie przywracaj starszych opisów „autopilot niewdrożony”. Wcześniejsze wzmianki tego promptu o nieopublikowanej paczce opisują etap przygotowania; aktualny checkout i publikację sprawdź w Git. To doprecyzowanie nie zamyka odbiorów A2b5/W5 ani prac A3/A3b/A4.

- Harness jest ubogą stroną w przeglądarce z chatem LLM. Pozwala podłączyć **jeden MCP — sklr**. Nie ma własnych narzędzi plikowych ani alternatywnej ścieżki wykonania. Skille i zewnętrzne MCP są dostępne za pośrednictwem sklr.
- **Pętlę kolejnych kroków prowadzi obecny LLM**, w ramach przebiegu rozpoczętego przez użytkownika. sklr dostarcza informacje, wykonuje działania i egzekwuje zatwierdzony kontrakt. Osobny wykonawca integrujący model nie jest potrzebny dla tej architektury pierwszej wersji.
- **Zamknięcie bramki sklr odcina nowe informacje i wykonania robocze**, więc zatrzymuje dalszy postęp pracy narzędziowej: harness nie ma alternatywnych narzędzi. sklr zwraca jednoznaczny komunikat sterujący, a nie pusty wynik. Model ma zakończyć odpowiedź, nie ponawiać żądań i nie szukać obejścia.
- Dostępna pozostaje wyłącznie ograniczona ścieżka sterująca zgodna z kontraktem sesji: stan, domknięcie już rozpoczętych działań i przygotowane przez użytkownika wznowienie. Nie służy ona nowym poszukiwaniom. „Wstrzymaj po kroku” blokuje nowe działania, lecz nie cofa skutków i nie zabija rozpoczętych; kończą się one w swoich limitach. Wewnętrzna pętla pojedynczego skilla/MCP nie ma automatycznie punktu pauzy.
- Blokada wykonania **nie jest technicznym zatrzymaniem generowania tekstu ani rozumowania LLM**. Model może jeszcze odpowiedzieć lub spróbować wywołania, ale sklr ma odmówić dalszej pracy. Zachowanie rzeczywistego chatu wymaga ręcznego odbioru; atrapy nie dowodzą zakończenia jego przebiegu.
- sklr **nie budzi modelu**, nie wysyła kolejnej wiadomości do chatu i nie prowadzi niezależnej pętli w tle. Po zakończeniu odpowiedzi, limicie harnessu lub utracie połączenia kontynuację inicjuje użytkownik. Przygotowanie wznowienia w panelu samo nie uruchamia LLM.
- Tryb Zwykły zachowuje dotychczasowy przepływ zgód/trust. Autopilot umożliwia kolejne operacje bez osobnej decyzji tylko w zakresie zatwierdzonej delegacji A2. Model nie włącza ani nie rozszerza zgody. Tryb i stan sesji są odrębne; przełączenie nie omija pauzy ani handshake. Testy, automatyczne poprawki, instalacje, sekrety, publikacja/commit/push, produkcja i działania destrukcyjne nie otrzymują zgody od samego włączenia trybu — obowiązują wyłączenia kontraktu A2.

To doprecyzowanie specyfiki harnessu nie zmienia przyjętych reguł A2, budżetów, implementacji ani zasad AGENTS. Kontynuuj od istniejącego kontraktu i procedury odbioru, nie projektuj autopilota od nowa.
