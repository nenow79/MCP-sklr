---
type: Specification
title: Autopilot projektu i sesji — kontrakt A2 v1
description: Zaakceptowany kontrakt delegacji wybranych działań, kontroli przed wykonaniem, budżetów i cofania zgody.
tags: [sklr, autonomy, sessions, approvals, security]
status: accepted
---
# Autopilot projektu i sesji — kontrakt A2 v1

## Status i granice

**Kontrakt zaakceptowany, niewdrożony.** Użytkownik zaakceptował dalszą pracę po korekcie rozdzielającej oczekiwanie narzędzia, timeout zadania i ważność delegacji. Przyjęty zakres i limity opisuje sekcja 11. Akceptacja kontraktu nie włącza autopilota, nie zmienia AGENTS ani obecnych zasad zgód; plan konkretnej paczki kodu nadal wymaga akceptacji przed edycją.

A2 pozwala kontynuować pracę przez sklr w jawnie zatwierdzonym zakresie bez osobnej decyzji dla każdego dopuszczonego zapisu. Nie uruchamia samodzielnie modelu i nie zapewnia nieprzerwanej pętli agenta. A3/A3b (konfiguracja i wykonawca testów), A4 (automatyczne cykle poprawek) i ogólny shell pozostają poza A2.

Kontrakty bazowe: [sesje i wznowienie](work-session-pause.md), [pliki i zgody](files-and-approvals.md), [skills](skills-support.md), [zewnętrzne MCP](external-mcp-support.md), [polityka OKF](okf-integration/write-policy.md). Bieżąca implementacja: [CURRENT](../plan/CURRENT.md); jedyna lista prac: [backlog](../plan/backlog.md).

## 1. Co autoryzuje użytkownik

Kontrakt jest ograniczoną delegacją użytkownika dla jednej wybranej sesji całej instancji sklr. Nie jest globalnym trust i nie izoluje rozmów korzystających z tej instancji. Panel pokazuje ten zakres przed włączeniem. Projekt, cel i zaakceptowany plan są widoczne dla użytkownika; swobodny tekst celu/planu pozostaje w RAM jak obecne wznowienie.

Uprawnienie jest przecięciem: dostępnych roots i ich trybów, reguł narzędzia, polityki wrażliwych operacji, istniejącego trust tam, gdzie jest wymagany, reguł kontraktu i jego pozostałych budżetów. Kontrakt nie znosi snapshotów, kontroli rewizji, hashy, walidacji JSON Schema, limitów transportu ani require_okf.

Instrukcje projektu pozostają obowiązujące dla modelu. W szczególności wymaganie planu/akceptacji przed zmianami kodu nie znika po włączeniu autopilota. Użytkownik musi wyraźnie zaakceptować konkretny zakres prac; kontrakt nie jest sposobem na zmianę własnych instrukcji przez model. sklr technicznie kontroluje operacje, ale nie potrafi dowieść semantycznej zgodności każdej zmiany z celem zapisanym w języku naturalnym.

Wyłącznie uwierzytelniony panel tworzy, zatwierdza i cofa delegację. Model może otrzymać opis obowiązujących ograniczeń, ale nie może zapisać polityki, zwiększyć budżetu, zmienić zegara, wybrać sesji ani samodzielnie włączyć autopilota. Treść pliku, checkpointu, strony, opisu narzędzia lub wyniku MCP nie jest decyzją użytkownika.

## 2. Zakres działań v1

Domyślny nowy kontrakt nie deleguje zapisów ani wykonań. Użytkownik wybiera jawne reguły; pusty zestaw nie oznacza „wszystko”. Nieobsługiwane pola i niejednoznaczne reguły są odrzucane.

| Działanie | Proponowana reguła A2 v1 |
|---|---|
| Odczyt plików, listowanie i search | Jawne narzędzia oraz roots/podkatalogi; zachowane maskowanie i limity |
| Odczyt wiedzy | Jawne bundles i zakres dokumentów; scope=all nie rozszerza kontraktu |
| edit_file, create_file, create_tree | Możliwa delegacja wyłącznie niewrażliwych zapisów w wybranych zakresach rw; overwrite jako osobne uprawnienie, domyślnie false |
| manage_folder | Tylko create w jawnym zakresie; rename wymaga osobnej decyzji poza autopilotem v1 |
| knowledge_save, knowledge_convert, prepare_project z create_agents=true | Osobna decyzja poza autopilotem v1; ich obecne sensitive nie jest zdejmowane |
| Skills | Jawny centralny skill/skrypt/hash i reguły argumentów; tylko istniejący trust oraz vibecoder-confirm=false pozwalają ominąć osobną zgodę |
| Skills dev/project lub centralne wymagające potwierdzenia | Zachowują ręczną zgodę; A2 v1 nie zmienia ich polityki |
| Wywołanie zewnętrznego MCP | Włączony profil, istniejący trust, konkretne narzędzie/schema i reguły argumentów; kontrakt tylko zawęża trust |
| Discovery zewnętrznego MCP | Jawnie wybrany profil; panel wyjaśnia, że discovery stdio może uruchomić proces |
| secret_reveal, request_root i zmiany konfiguracji/trust | Poza delegacją v1 |
| Testy, instalacje, shell, publikacja, płatności, produkcja, usuwanie danych | Poza delegacją v1; brak uprawnienia przez sam wybór skilla/MCP |

Wszystkie pliki i katalogi operacji wieloelementowej muszą mieścić się w kontrakcie. Jedna pozycja poza zakresem blokuje cały plan; bez cichego dzielenia i częściowego wykonania.

Kod/panel/konfiguracja działającego sklr, jego bazy/klucze, .git, pliki sekretów oraz inne ścieżki rozpoznane przez istniejącą politykę wrażliwości pozostają poza automatycznymi zapisami. Nie dodaje się wyjątku „pozwól na sensitive”. Zmiany plików instrukcji, reguł trust i samego kontraktu również wymagają ręcznej decyzji. Prace nad samym sklr nie otrzymują prawa do automatycznej zmiany własnej bramki.

Flaga sensitive ma różne zastosowania: przy plikach oznacza ochronę zapisu, przy external_mcp także poufny kanał argumentów/wyników. Zaufanego MCP nie odrzuca się wyłącznie z powodu tej flagi, ani nie zdejmuje jej w celu delegacji. Kwalifikację wyznacza adapter konkretnej operacji, nie boolean przekazany przez model.

Zewnętrzny skrypt/MCP działa z uprawnieniami użytkownika i nie jest ograniczony do roots przez ten kontrakt. Panel wymaga świadomego wyboru konkretnego wykonawcy. Deklaracja readOnly/destructive obcego serwera nie jest dowodem bezpieczeństwa; semantyki i wszystkich skutków jego kodu sklr nie kontroluje. Znanej operacji publikacji/produkcji/sekretów nie wolno dopuścić pod ogólną nazwą „narzędzie zaufane”.

## 3. Tożsamość i reguły dopasowania

Kontrakt zawiera identyfikator, własną rewizję, identyfikator wybranej sesji oraz identyfikator jej bieżącego przebiegu. Autoryzację wiąże niezmienny serwerowy odcisk zatwierdzonych reguł. Model nie podaje tych wartości jako uprawnienia; otrzymuje co najwyżej metadane do odczytu.

- Root jest przypięty do rzeczywistej ścieżki i trybu, nie samego aliasu. Alias to etykieta; zmiana ścieżki/trybu, dostępności, usunięcie lub ponowne dodanie roota unieważnia zależną delegację.
- Reguła plikowa wskazuje jawny plik albo podkatalog z zakresem potomków. W v1 brak dowolnych regex/globów, wildcardu wszystkich rootów oraz niejawnych wyjątków. Ścieżka jest sprawdzana po kanonizacji i przez obecne guardy Windows/POSIX również przed wykonaniem; prefiks tekstowy nie dowodzi przynależności.
- Brak root, alias domyślny, ścieżka absolutna i root w każdym wpisie batch są rozwiązywane do konkretnych celów przed dopasowaniem. Zmiana domyślnego roota nie przenosi kontraktu. Scope=* i scope=all nie dają dostępu do pominiętych projektów.
- Bundle jest przypięty do konfiguracji źródła i aktualnego dostępu; wyłączenie/usunięcie/podmiana źródła unieważnia regułę. Dla automatycznego docs wiązanie obejmuje tożsamość roota i podkatalog docs.
- Skill wskazuje źródło centralne, nazwę, hash i konkretny względny skrypt. Zmiana/shadowing skilla, interpreter lub zmiana istotnej konfiguracji wykonania unieważnia regułę. Opt-in proxy/CA nadal działa według kontraktu skills; nie daje kontraktowi prawa do wartości zmiennych.
- MCP wskazuje stabilny profil, odcisk ustawień połączenia, nazwę narzędzia i skrót schematu. Zmiana połączenia/schematu/trust nie może dostać automatycznie nowego uprawnienia przez ponowne discovery.

Argumenty skryptu w v1 to dokładna lista argumentów zatwierdzona przez użytkownika; bez podstawiania shella i dowolnych fragmentów poleceń. Reguły argumentów MCP mają zamknięty format: dokładne wartości JSON albo obiekt z jawnymi polami, typami, dozwolonymi wartościami, zakresami liczb i maksymalnymi długościami. Nieznane pola, niezgodny typ (także bool zamiast liczby) i nieobsługiwane reguły są odrzucane. Brak, null i pusty napis są różnymi wartościami. [Format reguł A2b2](autopilot-rules.md) przedstawia szczegóły techniczne zaakceptowane wraz z planem paczki.

Adresy URL dopuszcza się przez jawny scheme/host/port i ograniczenia ścieżki, nie porównanie prefiksu stringa. Nie ma dowolnego „pozostałe argumenty”. Polityka nie zawiera wartości sekretów, kodu, eval ani funkcji walidujących dostarczonych przez model. Pełną walidację inputSchema zachowuje niezależnie klient MCP. Konkretny serializowany schemat reguł musi być częścią zaakceptowanego planu walidatora przed implementacją.

## 4. Włączenie, pauza i wznowienie

Wymagane work_sessions.enabled=true i wybrana sesja. Kontrakt nie działa w trybie bez kontrolera sesji. Wyłączenie A2 w konfiguracji zachowuje obecne zachowanie serwera; nie zmienia domyślnego false sesji ani istniejącego trust.

Włączenie proponuje się wyłącznie dla sesji paused bez rozpoczętych działań. Użytkownik przegląda w panelu zakres, konkretne narzędzia, wyjątki i budżety, a następnie jawnie zatwierdza delegację. Przygotowanie wznowienia odbywa się obecnym przepływem TaskManager: aktualna rewizja/liczba anulowań, uzupełnienie i osobna decyzja o brakującym checkpointcie. Do przygotowania przypinany jest odcisk kontraktu.

Model pobiera kontekst zawierający opis ograniczeń jako zaufaną metainformację obok niezaufanego tekstu uzupełnienia. Potwierdzenie planu nie tworzy zgody: otwiera wykonania tylko przy zgodnej sesji, rewizji, tokenie i zatwierdzonym kontrakcie. Zmiana/utrata kontraktu unieważnia przygotowanie. Błąd publikacji lub niepewny commit pozostawia bramkę zamkniętą.

Każda pauza zawiesza delegację. Sam confirm_resume_plan nie reaktywuje jej bez aktualnej decyzji panelowej. Wznowienie wymaga jawnego potwierdzenia tego samego zakresu i pozostałych limitów albo zatwierdzenia nowego kontraktu. Pauza nie zeruje liczników i nie przedłuża terminu ważności. Użytkownik może wybrać wznowienie w zwykłym trybie ręcznych zgód, bez autopilota.

Edycja aktywnego kontraktu najpierw wstrzymuje sesję; nowa rewizja wymaga ponownego przeglądu i decyzji panelowej. Model nie potwierdza rozszerzenia za użytkownika. Po nowej decyzji stare zadania są anulowane przez istniejące przygotowanie wznowienia, nie przenoszone do nowej delegacji.

## 5. Budżety v1

Przyjęte wartości startowe i twarde pułapy v1 są poniżej. Panel pozwala wyłącznie wybrać dodatnie wartości nieprzekraczające tych pułapów; brak wartości nie oznacza nieskończoności.

| Budżet | Wartość domyślna | Twardy pułap v1 |
|---|---|---|
| Czas ważności od zatwierdzenia w panelu, razem z pauzami | 30 minut | 120 minut |
| Rozpoczęte operacje robocze | 100 | 500 |
| Równocześnie rozpoczęte operacje robocze | 1 | 1 |
| Timeout pojedynczego zadania | Jawnie wybrany dla narzędzia/profilu; 60 sekund tylko jako propozycja początkowa zwykłych operacji | Limit konkretnego wykonawcy; A2 nie narzuca wspólnego pułapu 120 sekund |

Zwykłe limity max_queued, transportów, rozmiaru plików/odpowiedzi i tempa MCP nadal obowiązują; decyduje najniższy limit. Początkowy limit współbieżności 1 upraszcza zatrzymanie i odbiór, nie ogranicza wewnętrznych wątków obcego MCP.

### Czas odpowiedzi a czas zadania w tle

Limit oczekiwania pojedynczego wywołania narzędzia nie jest timeoutem wykonania zadania. Kolejka zwraca task_id, a get_task pozwala później odczytać wynik. Obecne wait_task czeka maksymalnie 8 sekund; zwrot statusu running po tym czasie nie przerywa executora i nie oznacza timeoutu zadania. Odczyt statusu nie przedłuża deadline zadania ani uprawnienia autopilota. Dla awaiting_approval nadal obowiązuje jedno wait_task i STOP według AGENTS; bez automatycznej pętli pollingu LLM.

Aktualne wykonawce skills i zewnętrznych MCP mają domyślne 60 sekund oraz maksimum 300 sekund. Asynchroniczna kolejka sama nie znosi tych ograniczeń. Długi pytest może wymagać znacznie więcej czasu; przyszły runner A3b musi otrzymać oddzielny, jawny timeout profilu testów i obsługę przerwania/sprzątania/logów, zamiast dziedziczyć 60 sekund albo zakładać, że wystarczy ponawiać odczyt statusu. Wartości i pułapy profili testowych zostaną ustalone w kontrakcie A3/A3b; A2 ich nie implementuje.

Termin ważności delegacji ogranicza przyjmowanie nowych działań. Zadanie rozpoczęte przed jego upływem może dokończyć się w swoim zatwierdzonym limicie także po wygaśnięciu delegacji; potem nie startuje następne. Wygaśnięcie nie skraca automatycznie czasu trwającego pytest i nie przedłuża ważności zgody. Oczekujące zadanie, które nie rozpoczęło się przed wygaśnięciem, nie może ruszyć na podstawie starej delegacji.

Jednostką jest logiczna operacja użytkowa: bezpośredni odczyt liczy się raz, zadanie robocze raz przy rozpoczęciu wykonania. Wrapper narzędzia zgłaszającego zadanie i jego późniejszy executor nie mogą naliczać go podwójnie. Wieloplikowy zapis to jedna operacja z niezmienionymi limitami liczby/rozmiaru plików. No-op bez skutku i odrzucenie przed przyjęciem nie zużywają operacji; błąd po przyjęciu zużywa próbę, bez zwrotu budżetu.

Przy zgłoszeniu zadania rezerwuje się miejsce w budżecie, aby nie przyjąć nieograniczonej kolejki. Anulowanie przed rozpoczęciem zwalnia rezerwację. Przejście rezerwacja → rozpoczęcie jest jednokrotne i zsynchronizowane; bez przenoszenia rezerwacji między kontraktami. Niespójność liczników blokuje dalsze działania.

Narzędzia sterujące do odczytu stanu/domknięcia już istniejących zadań oraz checkpoint/replanowanie nie zużywają budżetu roboczego i pozostają dostępne podczas pauzy. Są nadal ograniczone swoimi limitami. Wewnętrzne odświeżanie indeksu nie jest dodatkową delegacją modelu i zachowuje własne limity; nie może posłużyć do wykonania zablokowanego narzędzia.

Czas mierzy zegar monotoniczny procesu. UTC służy wyłącznie prezentacji/audytowi; przesunięcie zegara systemowego nie wydłuża uprawnienia. Restart odbiera uprawnienie zamiast rekonstruować deadline. Lekki serwerowy nadzór wykrywa wygaśnięcie również bez następnego wywołania modelu; każda próba przyjęcia/dispatch niezależnie kontroluje termin. Nie używa się pollingu LLM.

Suma czasów zadań, koszt płatnych usług, liczba tokenów i zużycie pamięci procesów nie są obiecywanymi egzekwowanymi budżetami A2. Tokeny/koszty wymagają danych i współpracy harnessu; timeout i limit operacji nie gwarantują ograniczenia rachunku za obce narzędzie.

## 6. Kontrola przed skutkiem i wyścigi

Kontrola obejmuje ścieżki bezpośrednie oraz zadania kolejki, także te dopuszczane wcześniej bez zgody przez trust. Ręczna zgoda na zadanie nie omija aktualnej pauzy, budżetu ani zakresu aktywnej delegacji. Niedopuszczona operacja nie może opaść do starej ścieżki trust i wykonać się poza kontraktem.

1. Adapter rozwiązuje rzeczywisty cel, parametry i plan bez wykonywania żądanego skutku. Wrapper blokuje wywołania podczas pauzy; nie tworzy zadania za każdym ponowieniem.
2. Serwer klasyfikuje operację, sprawdza wyłączenia i dopasowuje pełny plan do zatwierdzonego kontraktu. Dla kolejki wiąże task_id, sesję/przebieg, odcisk kontraktu i rezerwację.
3. Przed rozpoczęciem wykonania ponownie sprawdza aktualne roots/profile/trust, hash/schema, rewizje/snapshoty, kontrakt, termin i budżet. Dotychczasowe weryfikacje nadal obowiązują; przygotowanie nie zastępuje kontroli bezpośrednio przed publikacją pliku/tools-call.
4. Przyjęcie rozpoczętej operacji i cofnięcie/pauza mają jeden punkt rozstrzygnięcia pod synchronizacją. Cofnięcie wygrywające przed tym punktem nie dopuszcza działania. Operacja przyjęta wcześniej jest rozpoczęta i podlega zasadom domknięcia, nawet gdy sam skutek nastąpi później. Nie obiecuje się cofnięcia wysłanego żądania.
5. Licznik i metadane autoryzacji są zapisane przed dopuszczeniem skutku; błąd zapisu lub niepewny commit zamyka bramkę. Brak wspólnej transakcji z systemem plików, siecią i osobnymi bazami nie może prowadzić do ponownego wykonania.
6. Po wyniku zwalnia się współbieżność i zapisuje bezpieczny rezultat próby. Błąd finalizacji nie zmienia otrzymanego wyniku w sukces ani nie uruchamia ponowienia; zamyka kolejne wykonania.

Nie trzyma się blokad kontraktu/sesji podczas sieci, procesu lub oczekiwania na użytkownika. Plan implementacji musi określić jedną kolejność blokad zgodną z obecną manager → kontroler → magazyny oraz punkty synchronizacji lokalnych zapisów i zewnętrznego dispatch. Guard obcego MCP może potrzebować discovery przed tools-call; kontrakt jawnie dopuszcza ten etap tylko dla wskazanego profilu, a kontrola samego wywołania jest powtarzana po discovery.

Zmiana zakresu, błąd walidacji planu, wycofany trust lub polityka wymagająca osobnej decyzji zatrzymują autopilota i wskazują potrzebę decyzji. Nie tworzy się automatycznie serii awaiting_approval. Użytkownik może jawnie wznowić zwykły tryb i zlecić świeżą operację; A2 nie przerabia starej rezerwacji na nowe uprawnienie. Limiter MCP nadal może wymagać ręcznej zgody — w A2 oznacza to wstrzymanie przed tools-call, bez obchodzenia limitera.

## 7. Cofnięcie, koniec budżetu i wynik niepewny

Panel udostępnia „Cofnij zgodę autopilota” niezależnie od „Anuluj zadanie”. Cofnięcie jest idempotentne, natychmiast zamyka nowe przyjęcia i żąda pauzy całej sesji. Rozpoczęte działania kończą się w swoich istniejących limitach; nie gwarantuje to rollbacku ich skutków. Queued nie przechodzą automatycznie na ręczne zgody ani nie ruszają po ponownym włączeniu.

Wygaśnięcie lub wyczerpanie budżetu powoduje ten sam rodzaj blokady z odrębnym reason. Bez samoczynnego resetu, przedłużenia lub powrotu do trybu legacy. Niespójność pamięci/magazynu to fail-closed, nie zwykłe zużycie limitu. Brak rozpoczętych działań oznacza paused, ale nie potwierdza checkpointu.

Timeout, zerwane połączenie po wysłaniu lub niepewny lokalny commit zatrzymują kolejne działania wymagające ustalenia skutku. Wynik jest oznaczony jako niepewny, nie jako „nic nie wykonano”. Bez automatycznego retry, innego profilu, nowej sesji lub alternatywnego narzędzia. Model może zachować bezpieczne ustalenia w checkpointcie; dalsza praca wymaga jawnego rozstrzygnięcia użytkownika. A2 nie jest silnikiem samonaprawy A4.

## 8. Metadane sterujące i panel

Stan autopilota jest odrębny od stanu sesji i obecności checkpointu. Proponowane stany: disabled, prepared, active, suspended, revoked, expired, exhausted, recovery_required. prepared nie otwiera bramki. Session active bez autopilota nadal oznacza zwykły dotychczasowy tryb, o ile użytkownik jawnie go wybrał przy wznowieniu.

Odpowiedź zawiera serwerowe metadane: stan, bezpieczny reason, identyfikator/rewizję kontraktu, blokadę wykonania, czas pozostały i budżet. Oddziela je od wyników narzędzi. Kod/metainstrukcja pochodzi z serwera; obca treść nie może nadpisać tych pól. Dokładny schemat jest częścią planu API przed kodem.

Przy zatrzymaniu instrukcja dla modelu wymaga: nie ponawiaj działań, nie obchodź ograniczeń, zapisz dozwolony bezpieczny checkpoint po spełnieniu warunków sesji, zakończ odpowiedź i czekaj. Polecenie replanowania nie oznacza prawa do rozszerzenia delegacji. Wyniki rozpoczętych działań pozostają dostępne zgodnie z dotychczasowym TTL.

Panel pokazuje zakres i wyłączenia, tryb zwykły/autopilot, limity z rezerwacjami/zużyciem, termin, rozpoczęte działania i ostatni powód zatrzymania. Mutacje wymagają aktualnej sesji panelu, Host/Origin, potwierdzenia oraz oczekiwanej rewizji; konflikty kart nie nadpisują decyzji. Brak MCP do zatwierdzania i brak automatycznego pollingu modelu. Interfejs nie przedstawia pauzy jako odebranego przez model komunikatu ani zapisanego checkpointu.

## 9. Restart, trwałość i poufność

Aktywna delegacja, pełne reguły argumentów, rezerwacje wykonawcze i tokeny są w RAM. Restart zawsze unieważnia uprawnienie; bez automatycznego wznowienia, zadań lub eksportu treści. Zapisane metadane nie są źródłem uprawnienia. Użytkownik po restarcie odzyskuje sesję zablokowaną i zatwierdza nową delegację/plan.

Trwały dziennik zawiera wyłącznie: identyfikatory sesji/kontraktu/zadania/reguły, rewizję, bezpieczny rodzaj działania, czas, zmianę stanu, licznik i kod wyniku/niepewności. Bez surowych parametrów, planów, treści plików, URL, nagłówków, checkpointu, wyników secret_result/external_result lub wartości środowiska. Jeśli potrzebny jest odcisk parametrów, używa się HMAC z lokalnym kluczem, nie publicznego hasha umożliwiającego zgadywanie sekretów.

Proponowana retencja dziennika: 30 dni i maksymalnie 1000 zdarzeń na instancję; usuwanie tylko historii, nie metadanych niezbędnych do rozstrzygnięcia bieżącego wykonania. Reguły i identyfikatory nie nadają verified/generated. Cache wiedzy i dziennik nie są magazynem poufnych wyników. Nie zwiększa się TTL ani nie gwarantuje zeroizacji pamięci.

Ręczny eksport kontraktu/raportu pozostaje osobną zatwierdzaną ścieżką z podglądem i kontrolą poufności. W v1 nie zapisuje się automatycznie pełnego szablonu delegacji w config.json. To ogranicza przenoszenie aktywnych zgód między maszynami.

## 10. Kryteria odbioru

Odbiór implementacji musi potwierdzić:

- Brak A2 zachowuje istniejący tryb; niepoprawna włączona konfiguracja nie uruchamia pracy bez bramki. Model ani nieautoryzowane wywołanie panelu nie włącza/rozszerza delegacji.
- Dozwolony niewrażliwy zapis działa bez osobnego Allow, ma zadanie i ślad decyzji kontraktowej; brak zgodności choć jednego pliku blokuje całość. Przypadki sensitive/overwrite/zmiana snapshotu/polityki OKF nie uzyskują obejścia.
- Narzędzia zaufane, bezpośrednie odczyty i kolejka podlegają zakresowi/budżetowi. Default root, batch root, scope=all, inne bundle, alias, Windows case/UNC/junction i podmiana ścieżki nie rozszerzają zgody.
- Skill wymaga zgodnego centralnego trust/hash/skryptu/argumentów; zmieniony/shadowed skill i dev/project nie dostają automatycznej zgody. MCP wymaga istniejącego trust/schema/połączenia oraz zgodnych argumentów; sensitive kanału wyników pozostaje zachowane.
- Wyścigi równoległego submit/dispatch/cofnięcia/pauzy kończą się zgodnie z punktem przyjęcia; jedna logiczna operacja nie zużywa dwóch jednostek. Rezerwacje, anulowania, ostatnia jednostka i współbieżność 1 nie przekraczają limitów.
- Wygaśnięcie działa bez ruchu modelu; zmiana czasu systemowego, pauza i reconnect nie przedłużają kontraktu. Cofnięcie/budżet nie przechodzą do starej ścieżki trust. Rozpoczęte działanie może zakończyć się z wynikiem i metainformacją pauzy.
- Zmiana kontraktu podczas resume_pending unieważnia handshake; model nie otwiera bramki samym potwierdzeniem starego planu. Zwykły tryb po cofnięciu wymaga jawnej decyzji użytkownika.
- Błędy SQLite/RAM, publikacji wyniku i niepewny commit są fail-closed. Restart nie odtwarza wykonawców, reguł ani zgód. Brak retry operacji o niepewnym skutku.
- SQLite/WAL, logi i telemetria nie otrzymują poufnych treści; wyniki zachowują dotychczasowy TTL i dostęp. Retencja nie usuwa rozliczenia rozpoczętej operacji.
- Regresja obecnych zgód/trust/sesji/checkpointów i panelu pozostaje zielona. Osobny ręczny odbiór Windows: panel → zatwierdzenie kontraktu → potwierdzenie planu → dozwolony zapis → blokada poza zakresem → checkpoint → nowe wznowienie; również cofnięcie podczas działania i restart.

Testy logiki i atrap nie zastępują odbioru rzeczywistego MCP/panelu/harnessu. Pytest i serwery uruchamiane wyłącznie na wyraźne polecenie. Ten projekt nie stanowi wyniku testów ani zgody na ich wykonanie.

## 11. Przyjęte rozstrzygnięcia v1

Przyjęty zakres po korekcie timeoutów:

1. Delegacja niewrażliwych zapisów w jawnych zakresach; pozostałe operacje z tabeli pozostają ręczne lub wyłączone. Skills/MCP muszą już spełniać obecny trust, a kontrakt dodatkowo je ogranicza.
2. Włączenie/zmiana tylko przez panel z pauzy i z obecnym handshake planu; każda pauza wymaga ponownego potwierdzenia delegacji. Cofnięcie/wygaśnięcie/wyczerpanie budżetu wstrzymuje całą sesję.
3. Limity delegacji 30 min/100 operacji/1 rozpoczęta naraz oraz pułapy 120 min/500/1. Timeout zadania jest jawny per narzędzie/profil i ograniczony wykonawcą, bez wspólnego pułapu 120 s; długie testy otrzymają osobny profil w A3b. Oczekiwanie na odpowiedź narzędzia i timeout pracy w tle są odrębne. Czas pauzy wliczany, liczniki nie resetują się automatycznie.
4. Reguły/aktywna zgoda tylko w RAM; po restarcie nowa decyzja. Dziennik wyłącznie metadanych, retencja 30 dni/1000 zdarzeń.
5. Odmowa poza zakresem lub potrzeba osobnej zgody wstrzymuje autopilota; powrót do zwykłego trybu jest jawny. Bez automatycznego retry i bez omijania ograniczeń innym narzędziem.

Plan modułów, schematów/API i testów konkretnej paczki wymaga akceptacji przed zmianą kodu. Lista etapów implementacji pozostaje w backlogu. Akceptacja A2 nie obejmuje jeszcze kontraktów ani limitów przyszłego runnera A3b i automatycznych poprawek A4.
