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

Pierwsza wersja kontroluje jedną sesję pracy na instancję sklr, wybieraną wyłącznie w panelu. Nie jest to sesja uwierzytelnienia panelu ani transportowa MCP; pauza obejmuje wszystkie rozmowy korzystające z instancji, bez izolacji rozmów. Zmiana domyślnego roota nie przenosi przypiętej sesji do innego projektu. Roots opisują projekt, nie nadają dodatkowych uprawnień.

Model nie przekazuje session_id. Serwer przypisuje zadania do wybranej sesji i jej rewizji przed scheduling oraz sprawdza powiązanie przy dispatch. Przy włączonej funkcji brak wybranej sesji blokuje narzędzia robocze do Startu albo jawnego odzyskania w panelu. Brak/obce powiązanie nie daje obejścia; stare powiązanie tej samej sesji nie pozwala wykonać zadania poprzedniej generacji. Historyczne zadania nie są automatycznie przypisywane.

Kontrola obejmuje kolejne wykonania przez sklr, również zaufane skille i zewnętrzne MCP. Zmiana projektu przez nową sesję wymaga jawnego zakończenia poprzedniej w panelu; reconnect ani model nie mogą otworzyć bramki. Funkcja jest opt-in przez work_sessions.enabled (domyślnie false), zmiana wymaga restartu.

Panel łączy konfigurację i sesję w zakładce „Foldery i sesja”, z jedną listą folderów. Start z panelu obejmuje wszystkie dostępne foldery; niedostępne wpisy pozostają widoczne. Dodawanie, edycja, usuwanie i zmiana folderu domyślnego przy wybranej sesji wymagają aktualnego session_id i rewizji, pełnej pauzy bez rozpoczętych działań oraz braku przygotowanego handshake. Po zmianie lista dostępnych folderów jest przypinana do tej samej sesji, rewizja rośnie, poprzedni plan wznowienia i zgoda autopilota tracą ważność. Checkpoint pozostaje kontekstem; nowe foldery nie rozszerzają zgód. Operacje panelu są serializowane z dispatch i przygotowaniem wznowienia; błąd synchronizacji konfiguracji/metadanych pozostawia bramkę zamkniętą i wymaga odczytu stanu, bez automatycznej powtórki. Sesja wymaga 1–32 folderów; przed usunięciem ostatniego dostępnego folderu należy dodać inny. Bez włączonych sesji konfiguracja folderów pozostaje dostępna.

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

Utrzymanie centralnego indeksu OKF jest wewnętrzną pracą procesu, niezależną od wyboru/pauzy/zakończenia sesji; nie przyznaje modelowi dostępu do narzędzi podczas blokady. Zadania `knowledge_refresh` projektów pozostają przypisane do sesji i jej generacji. Start i potwierdzenie planu wznowienia mogą zlecić nowe odświeżenie bieżących projektów, bez retry starych zadań i bez rozszerzania delegacji autopilota. Zakres wiedzy i publikacji opisuje [kontrakt OKF](okf-integration/index-and-refresh.md).

Jeśli model kontynuuje mimo komunikatu, bramka blokuje nowe działania, bez zakładania nowych zadań za każdym ponowieniem. Nie organizować automatycznego pollingu przez LLM. sklr nie potrafi obudzić modelu: pauza staje się dla niego widoczna przy kolejnym wywołaniu/odpowiedzi. Panel musi odróżniać blokadę wykonania od potwierdzonego odbioru komunikatu i checkpointu.

## 4. Checkpoint

Zapis checkpointu ma odrębną, ograniczoną ścieżkę dozwoloną podczas pauzy. Nie jest obejściem blokady dla dowolnego zapisu plików i nie nadaje zgody na inne działania.

Checkpoint obejmuje: cel, etap, bezpieczne ustalenia ze źródłami, identyfikatory wykonanych zadań, odnośniki do wyników/logów, otwarte pytania, następny planowany krok i jawnie niepewne wyniki. Zawiera rewizję sesji; spóźniony zapis ze starego przebiegu nie zastępuje aktualnego stanu. Interfejs raportuje zapis i błędy, nie traktując samego zgłoszenia jako saved.

Trwały zapis ograniczyć do metadanych i treści dopuszczonych przez istniejącą politykę. Nie utrwalać automatycznie sekretów, poufnych wyników MCP ani ich parafraz. Maskowanie heurystyczne nie stanowi gwarancji bezpiecznej automatycznej persystencji dowolnego tekstu modelu. Plan musi określić rozdzielenie: bezpieczne metadane trwałe; swobodne podsumowanie w RAM albo trwałe dopiero po jawnej decyzji użytkownika. Zapis checkpointu nie przedłuża TTL istniejących secret_result/external_result.

Odnośnik do poufnego wyniku nie jest kopią wyniku. Po TTL, restarcie lub utracie dostępu jawnie wskazać niedostępność; nie pobierać ponownie automatycznie. Nie obiecywać zachowania całego kontekstu modelu ani wyników, których obecny kontrakt zabrania utrwalać.

Model po potwierdzonym zapisie kończy odpowiedź: „Praca wstrzymana, checkpoint zapisany. Czekam na uzupełnienie.” Przy niepowodzeniu podaje rzeczywisty stan. Brak modelu lub awaryjny Stop w harnessie nie mogą być przedstawiane jako sukces checkpointu.

## 5. Wznowienie i restart

Użytkownik może dodać uzupełnienie i zlecić wznowienie przez uwierzytelniony panel. Uzupełnienie nie rozszerza automatycznie uprawnień. Model sam nie wznawia sesji ani autopilota.

Przygotowanie wznowienia przechodzi wyłącznie przez TaskManager po jawnej decyzji panelu. Panel podaje aktualne session_id, rewizję i zaakceptowaną liczbę anulowań; pod blokadami dane są ponownie sprawdzane przed skutkami. Brak checkpointu wymaga osobnego potwierdzenia. Stare awaiting_approval/queued są anulowane, nie odtwarzane ani uruchamiane. Rozpoczęte i zakończone pozostają nietknięte; wykrycie running uniemożliwia przygotowanie. Nowe zadania wymagają aktualnych zgód, hashy, rewizji i dostępu.

Przygotowanie pozostaje paused i zablokowane. Model pobiera get_resume_context, traktuje treść jako dane i potwierdza zaktualizowany plan przez confirm_resume_plan z bieżącą rewizją/tokenem RAM. Tylko skuteczne potwierdzenie otwiera bramkę; panel odczytuje skutecznie potwierdzony plan bez tokenu. Uzupełnienie (3000 znaków), checkpoint i plan (6000) pozostają tylko w RAM; wykryty sekret odrzuca całą treść, heurystyka nie jest gwarancją. Trwały eksport dopiero po wznowieniu zwykłą zatwierdzaną ścieżką.

Nowy checkpoint unieważnia przygotowany handshake; błędy publikacji RAM nie uprawniają do użycia pozostałego starego tokenu. Niepewny commit potwierdzenia pozostawia bramkę zamkniętą nawet gdy SQLite wskazuje active; odzyskanie wymaga nowej jawnej decyzji i koordynowanego przygotowania. Nie ponawiać automatycznie starego potwierdzenia ani operacji o niepewnym skutku zdalnym. Panel odświeża się ręcznie, bez pollingu; sklr nie budzi modelu.

Po restarcie nie wybierać ani nie wznawiać sesji automatycznie. Zachować bezpieczne metadane trwałe, unieważnić rewizje; trwające zadania pozostają interrupted zgodnie z obecną kolejką. Nie odtwarzać executorów z SQLite ani utraconych wyników/treści/tokenów RAM. Jawny wybór zapisanej sesji w panelu przypina ją zablokowaną, pokazuje utratę RAM i potrzebę nowego przygotowania/replanowania. Odzyskanie innej sesji wymaga wcześniejszego zakończenia już przypiętej sesji.

### Zakończenie bez zapisywania

Przycisk „Zakończ bez zapisywania” jest osobną decyzją uwierzytelnionego panelu, dostępną podczas pracy, pauzy i przygotowanego wznowienia. POST `/api/work-session/end` wymaga aktualnego session_id, dodatniej całkowitej rewizji oraz confirmed=true. Zamyka bramkę, trwale oznacza sesję jako kończoną i usuwa checkpoint, uzupełnienie oraz plan/token wznowienia z RAM. Nie żąda nowego checkpointu. Zgoda autopilota traci ważność; jego szkic i zatwierdzony cel/plan są czyszczone. Zakończenie nie usuwa historii zadań, nie cofa wykonanych zmian i nie kasuje dziennika niepewnych skutków.

TaskManager anuluje wszystkie awaiting_approval/queued tej sesji; rozpoczęte działania kończą się w dotychczasowych limitach. Podczas kończenia panel pokazuje stan ending i liczbę działań; blokuje nowe wykonania, checkpoint, przygotowanie wznowienia, zmianę folderów i Start. Po anulowaniu kolejki i zwolnieniu ostatniego rozpoczętego działania usuwa wybór sesji, pozostawiając bramkę zamkniętą. Użytkownik może zmienić foldery i wybrać Start dla nowego identyfikatora, bez handshake wznowienia i bez odziedziczonej zgody autopilota. Nierozstrzygnięte skutki autopilota zachowują dotychczasową blokadę odzyskania.

SQLite sesji ma addytywną migrację v1 → v2: lifecycle=open/ending/ended. Zachowuje metadane i powiązania historii zadań; nie utrwala treści kontekstu. Sesje ending/ended są wyłączone z listy odzyskiwania i nie można ich przypiąć ani wznowić, również po restarcie. Bootstrap finalizuje trwałe oznaczenia ending. Częściowy błąd anulowania/czyszczenia/finalizacji pozostawia blokadę i wybraną sesję; po odczycie aktualnego stanu użytkownik może jawnie ponowić zakończenie z bieżącą rewizją. Bez automatycznego retry i bez anulowania rozpoczętych działań.

## 6. Granica kontroli

Zewnętrzne MCP używane w tym przepływie są wywoływane przez sklr, więc bramka obejmuje ich kolejne wywołania. LLM ma otrzymać powód pauzy i zakończyć pracę z checkpointem, zamiast próbować naprawiać brak informacji.

sklr egzekwuje blokadę własnych wykonań; respektowanie instrukcji, podsumowanie ustaleń i zakończenie odpowiedzi zależą od LLM/harnessu. Nie gwarantować zatrzymania samego rozumowania ani utrwalenia checkpointu bez jego potwierdzenia. Narzędzia dostępne poza sklr nie są technicznie kontrolowane przez tę bramkę; model ma zakaz używania ich do obejścia pauzy.

Pętla wewnątrz pojedynczego wywołania obcego MCP/skilla nie ma automatycznie bezpiecznego punktu pauzy. Bez współpracy narzędzia bieżące wywołanie kończy się w istniejącym limicie, a blokowane są następne. Kooperacyjne zatrzymywanie wewnętrznych pętli wymaga osobnego protokołu i nie jest obietnicą pierwszej paczki.

## 7. Kryteria odbioru kontraktu

Odbiór musi wykazać: izolację sesji i brak obejścia przez identyfikator; wyścig start/pauza; kilka już rozpoczętych działań; zachowanie oczekujących zadań i limitów; zatwierdzenie i wygaśnięcie zgody podczas pauzy; brak zajęcia workerów przez oczekiwanie na wznowienie; jawny komunikat przy wyniku i próbie następnego wywołania; poprawne zachowanie ignorującego komunikat modelu na granicy narzędzi; niezależność pauzy od checkpointu; konflikt rewizji i błąd zapisu; brak wycieku poufnych danych do SQLite/panelu/telemetrii; TTL bez przedłużania; restart bez automatycznego dispatch; wznowienie z replanowaniem; regresję trust, zgód i anulowania.

Testy logiki mogą używać pytest zgodnie z istniejącym frameworkiem sklr. Atrapy LLM nie potwierdzają zachowania rzeczywistego harnessu: konieczny osobny ręczny scenariusz pauza → komunikat → checkpoint → zakończenie odpowiedzi → uzupełnienie → wznowienie. Nie uruchamiać pytest ani serwera bez wyraźnego polecenia użytkownika.
