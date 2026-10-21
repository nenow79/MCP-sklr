---
type: Specification
title: Wstrzymanie sesji pracy i checkpointy
description: Kontrakt pierwszej paczki A1/A1b, komunikacja pauzy z LLM i granice zachowania ciągłości.
tags: [sklr, sessions, pause, checkpoints, safety]
---
# Wstrzymanie sesji pracy i checkpointy

## Status i zakres

Użytkownik zaakceptował kierunek oraz przebieg: pauza w sklr → jawny komunikat dla LLM → checkpoint → oddanie sterowania użytkownikowi. Ten dokument precyzuje kontrakt pierwszej paczki; zatwierdzenie zapisu nie oznacza wdrożenia. Plan konkretnej paczki kodu wymaga akceptacji przed edycją kodu.

Zakres A1/A1b: sesje pracy, blokada nowych działań, panel, komunikacja z modelem, checkpoint i wznowienie. Autopilot, automatyczne poprawki i wykonawca testów pozostają poza zakresem. Obecne zgody, trust, limity, poufność i reguły uruchamiania testów nadal obowiązują. Lista zadań i kolejność wyłącznie w [backlogu](../plan/backlog.md); stan implementacji w [CURRENT](../plan/CURRENT.md).

## 1. Sesja i przypisanie działań

Sesja identyfikuje jedną pracę i jej projekt/roots. Nie jest sesją uwierzytelnienia panelu ani sesją transportową MCP. Równoległe prace muszą mieć odrębne identyfikatory; zmiana domyślnego roota nie może przenosić sesji do innego projektu.

Obecny model Task nie zapewnia takiego przypisania. Przed implementacją ustalić jawny sposób przekazania session_id do narzędzi modelu i trwałego przypisania zadań. Nie zakładać, że pochodzenie da się wiarygodnie odgadnąć z treści argumentów. Istniejące zadania bez session_id pozostają w dotychczasowym trybie i nie mogą być przedstawiane jako objęte pauzą. Panel pokazuje zakres kontroli.

W obrębie sesji kontrola obejmuje kolejne wykonania przez sklr, również zaufane skille i zewnętrzne MCP. Nie wolno ominąć pauzy przez pominięcie lub podmianę session_id: sposób wiązania i obsługę braku identyfikatora trzeba uzgodnić w planie integracji.

## 2. Stany i żądanie pauzy

Stan wykonania sesji: active → pause_requested → paused. paused oznacza, że nie ma rozpoczętych działań sesji; nie oznacza zapisanego checkpointu. Checkpoint ma odrębny stan: pending, saved albo unavailable. Powód i rewizja stanu są jawne.

Przycisk panelu „Wstrzymaj po kroku” natychmiast ustanawia bramkę blokującą nowe wykonania sesji. Żądanie jest idempotentne i wraca szybko, bez oczekiwania na koniec długiego MCP. Mutacje panelu wymagają obecnych zabezpieczeń sesji, Host i Origin. Model nie ma uprawnienia samodzielnego wznowienia.

Kontrola bramki i rozpoczęcie lokalnego wykonania mają jeden zdefiniowany punkt rozstrzygnięcia pod synchronizacją. Zadanie, które przekroczyło go przed pauzą, jest rozpoczęte; pozostałe nie mogą startować. Żądanie nie ustawia cancel_event i nie zabija rozpoczętych procesów.

Rozpoczęte działania kończą się w swoich dotychczasowych limitach. Panel pokazuje ich liczbę; pauza nie zwiększa timeoutów. Nie obiecywać cofnięcia zdalnej operacji ani zatrzymania każdego żądania, które zdążyło wejść w rozpoczęte wykonanie.

Oczekujące zadania zachowują dane i dotychczasową decyzję. Zatwierdzenie podczas pauzy nie uruchamia zadania; nadal obowiązują wygaśnięcia zgód. Wstrzymane zadania nie zajmują wątku workera ani nie tworzą aktywnej pętli ponowień. Dokładną reprezentację w kolejce ustalić w planie kodu, z kontrolą limitów i bez automatycznego uznania nowego statusu za terminalny.

„Anuluj” pozostaje odrębne. Stop w harnessie przeglądarkowym jest środkiem awaryjnym i może uniemożliwić modelowi zapis checkpointu.

## 3. Komunikat dla LLM

Nie używać pustego wyniku, zwykłego błędu transportu ani pozornego sukcesu. Odpowiedź sterująca ma jednoznaczny kod pauzy, reason=user_requested_pause, session_id, rewizję, stan checkpointu i informację, że nie wolno kontynuować pracy.

Przykładowa treść instrukcji generowanej przez sklr:

> Użytkownik wstrzymał pracę. Nie ponawiaj wywołań i nie używaj innych narzędzi ani nowej sesji do kontynuowania tego zadania. Zapisz bezpieczny checkpoint dozwoloną ścieżką. Zakończ odpowiedź i czekaj na uzupełnienie użytkownika. Nie deklaruj zapisu bez potwierdzenia.

To zaufana metainformacja sklr, oddzielona od niezaufanej treści zewnętrznego MCP. Wynik narzędzia nie może nadpisywać kodu ani instrukcji pauzy. Zewnętrzny tekst i checkpointy pozostają danymi, nie źródłem uprawnień.

Po żądaniu pauzy każda następna odpowiedź związana z sesją powinna informować model o pauzie. Wynik rozpoczętego zadania pozostaje dostępny zgodnie z dotychczasowym kontraktem, z osobną metainformacją sterującą. get_task/wait_task mogą służyć domknięciu już rozpoczętych działań, nie rozpoczynaniu nowych poszukiwań. Odczyt stanu i zapis checkpointu pozostają dostępne; wyszukiwanie nowych danych, wykonywanie skilli/MCP i mutacje robocze są blokowane w tej sesji.

Jeśli model kontynuuje mimo komunikatu, bramka blokuje nowe działania, bez zakładania nowych zadań za każdym ponowieniem. Nie organizować automatycznego pollingu przez LLM. sklr nie potrafi obudzić modelu: pauza staje się dla niego widoczna przy kolejnym wywołaniu/odpowiedzi. Panel musi odróżniać blokadę wykonania od potwierdzonego odbioru komunikatu i checkpointu.

## 4. Checkpoint

Zapis checkpointu ma odrębną, ograniczoną ścieżkę dozwoloną podczas pauzy. Nie jest obejściem blokady dla dowolnego zapisu plików i nie nadaje zgody na inne działania.

Checkpoint obejmuje: cel, etap, bezpieczne ustalenia ze źródłami, identyfikatory wykonanych zadań, odnośniki do wyników/logów, otwarte pytania, następny planowany krok i jawnie niepewne wyniki. Zawiera rewizję sesji; spóźniony zapis ze starego przebiegu nie zastępuje aktualnego stanu. Interfejs raportuje zapis i błędy, nie traktując samego zgłoszenia jako saved.

Trwały zapis ograniczyć do metadanych i treści dopuszczonych przez istniejącą politykę. Nie utrwalać automatycznie sekretów, poufnych wyników MCP ani ich parafraz. Maskowanie heurystyczne nie stanowi gwarancji bezpiecznej automatycznej persystencji dowolnego tekstu modelu. Plan musi określić rozdzielenie: bezpieczne metadane trwałe; swobodne podsumowanie w RAM albo trwałe dopiero po jawnej decyzji użytkownika. Zapis checkpointu nie przedłuża TTL istniejących secret_result/external_result.

Odnośnik do poufnego wyniku nie jest kopią wyniku. Po TTL, restarcie lub utracie dostępu jawnie wskazać niedostępność; nie pobierać ponownie automatycznie. Nie obiecywać zachowania całego kontekstu modelu ani wyników, których obecny kontrakt zabrania utrwalać.

Model po potwierdzonym zapisie kończy odpowiedź: „Praca wstrzymana, checkpoint zapisany. Czekam na uzupełnienie.” Przy niepowodzeniu podaje rzeczywisty stan. Brak modelu lub awaryjny Stop w harnessie nie mogą być przedstawiane jako sukces checkpointu.

## 5. Wznowienie i restart

Użytkownik może dodać uzupełnienie i zlecić wznowienie przez uwierzytelniony panel. Uzupełnienie nie rozszerza automatycznie uprawnień. Model sam nie wznawia sesji ani autopilota.

Nie uruchamiać starej kolejki bez ponownego planu. Potrzebny etap przygotowania wznowienia: udostępnienie checkpointu/uzupełnienia, potwierdzenie przez model aktualizacji planu, wybór nadal potrzebnych zadań i kontrola aktualnych zgód, hashy, rewizji i dostępu przed wykonaniem. Techniczny handshake określić w planie kodu. Nowa generacja/re wizja musi uniemożliwiać spóźnionym odpowiedziom poprzedniego przebiegu zmianę stanu. Nie ponawiać operacji z niepewnym skutkiem zdalnym.

Po restarcie nie wznawiać sesji automatycznie. Zachować bezpieczny stan trwały; trwające zadania pozostają interrupted zgodnie z obecną kolejką. Nie odtwarzać funkcji executor z SQLite ani utraconych wyników RAM. Pokazać braki checkpointu i potrzebę jawnego wznowienia/replanowania.

## 6. Granica kontroli

Zewnętrzne MCP używane w tym przepływie są wywoływane przez sklr, więc bramka obejmuje ich kolejne wywołania. LLM ma otrzymać powód pauzy i zakończyć pracę z checkpointem, zamiast próbować naprawiać brak informacji.

sklr egzekwuje blokadę własnych wykonań; respektowanie instrukcji, podsumowanie ustaleń i zakończenie odpowiedzi zależą od LLM/harnessu. Nie gwarantować zatrzymania samego rozumowania ani utrwalenia checkpointu bez jego potwierdzenia. Narzędzia dostępne poza sklr nie są technicznie kontrolowane przez tę bramkę; model ma zakaz używania ich do obejścia pauzy.

Pętla wewnątrz pojedynczego wywołania obcego MCP/skilla nie ma automatycznie bezpiecznego punktu pauzy. Bez współpracy narzędzia bieżące wywołanie kończy się w istniejącym limicie, a blokowane są następne. Kooperacyjne zatrzymywanie wewnętrznych pętli wymaga osobnego protokołu i nie jest obietnicą pierwszej paczki.

## 7. Kryteria odbioru kontraktu

Odbiór musi wykazać: izolację sesji i brak obejścia przez identyfikator; wyścig start/pauza; kilka już rozpoczętych działań; zachowanie oczekujących zadań i limitów; zatwierdzenie i wygaśnięcie zgody podczas pauzy; brak zajęcia workerów przez oczekiwanie na wznowienie; jawny komunikat przy wyniku i próbie następnego wywołania; poprawne zachowanie ignorującego komunikat modelu na granicy narzędzi; niezależność pauzy od checkpointu; konflikt rewizji i błąd zapisu; brak wycieku poufnych danych do SQLite/panelu/telemetrii; TTL bez przedłużania; restart bez automatycznego dispatch; wznowienie z replanowaniem; regresję trust, zgód i anulowania.

Testy logiki mogą używać pytest zgodnie z istniejącym frameworkiem sklr. Atrapy LLM nie potwierdzają zachowania rzeczywistego harnessu: konieczny osobny ręczny scenariusz pauza → komunikat → checkpoint → zakończenie odpowiedzi → uzupełnienie → wznowienie. Nie uruchamiać pytest ani serwera bez wyraźnego polecenia użytkownika.
