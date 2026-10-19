---
type: Specification
title: Zewnętrzne MCP w sklr — kontrakt v1
description: Trwałe profile zewnętrznych MCP, administracja w panelu i wykonywanie narzędzi przez kolejkę zgód.
tags: [sklr, mcp, panel, security]
---
# Zewnętrzne MCP — kontrakt v1

Zakres zaakceptowany przez użytkownika w rozmowie. Dokument opisuje docelowy kontrakt, nie potwierdza wdrożenia. Stan implementacji i odbioru pozostaje w [CURRENT](../plan/CURRENT.md), otwarte zadania wyłącznie w [backlogu](../plan/backlog.md).

## 1. Zakres i rozdzielenie stanów

sklr jest klientem zewnętrznych serwerów MCP. Profile są globalne, niezależne od roots i domyślnego projektu. Zmiana projektu ani restart nie usuwa profili.

- Zapisany profil: konfiguracja zachowana w `config.json` przez `ConfigStore.update`, z rollbackiem pamięci przy błędzie zapisu.
- Włączony profil: dostępny do użycia przez model; flaga trwała. Wyłączenie zachowuje konfigurację i blokuje nowe operacje.
- Ostatni test: informacja o zakończonej próbie, nie gwarancja bieżącego połączenia. Stan i czas testu mogą pozostać tylko w RAM; po restarcie stan jest nieprzetestowany.
- Sesja: otwierana na czas operacji, zamykana po zakończeniu, błędzie, timeout lub anulowaniu. Brak uruchamiania procesów stdio i testowania wszystkich serwerów przy starcie.

Panel nie nazywa włączonego profilu „połączonym”, jeśli nie istnieje aktywna sesja. Usunięcie profilu wymaga jawnego potwierdzenia i oznacza zapomnienie konfiguracji.

## 2. Konfiguracja i walidacja

Wykorzystać istniejący klucz `external_mcp`; wersjonować strukturę i walidować przed publikacją. Puste dotychczasowe `{}` oznacza brak profili. Nieznane lub błędne wpisy nie mogą uruchamiać procesu ani powodować utraty konfiguracji. Nie migrować w ciemno niepustych historycznych wpisów.

Profil zawiera stabilny identyfikator, unikalny alias, opis, flagę enabled, transport i ustawienia właściwe dla transportu. Aktualizacja, włączenie i usunięcie wymagają oczekiwanej rewizji, aby odrzucać konflikt pomiędzy kartami panelu. Zmiana aliasu nie zmienia identyfikatora.

Transporty v1:

- `http`: Streamable HTTP, jawny URL http/https, opcjonalne nagłówki i Bearer przez referencje do środowiska.
- `sse`: jawnie wybrany transport SSE, bez zgadywania i automatycznej zmiany transportu.
- `stdio`: osobny program, lista argumentów, opcjonalny bezwzględny katalog roboczy i mapa referencji do zmiennych środowiskowych.

Nie dzielić polecenia przez `.split()` i nie używać shella. Nie instalować pakietów automatycznie. Wskazanie przez użytkownika programu typu npx jest świadomą konfiguracją uruchamiania zewnętrznego kodu; panel ostrzega o potencjalnym pobieraniu i wykonaniu pakietu. Ścieżki i argumenty muszą działać na Windows, w tym ze spacjami. Uruchomiony MCP nie jest sandboxem i działa z uprawnieniami użytkownika, poza ograniczeniami roots sklr.

TLS jest weryfikowany domyślnie. Można wskazać własny plik CA; brak przełącznika verify=false w v1. Odrzucać URL z userinfo, fragmentem i poufnymi parametrami; uwierzytelnianie przekazywać osobnym kanałem. Nie podłączać sklr do samego siebie; ograniczyć rekurencję także w obsłudze klienta. Nie przekazywać tokenu panelu do zewnętrznych serwerów.

## 3. Sekrety i dane

Dla v1 wybrać referencje do zmiennych środowiskowych zamiast nowego magazynu sekretów. Panel zapisuje nazwę zmiennej, nie jej wartość. Brak zmiennej daje bezpieczny błąd przed połączeniem. Ponowne użycie profilu nie wymaga konfiguracji, o ile środowisko uruchomionego sklr nadal zawiera potrzebne zmienne.

Nie przyjmować tokenów w czacie, jawnej konfiguracji ani argumentach procesu. Sekrety rozwiązywać dopiero na czas wykonania. Dla stdio ograniczyć dziedziczone środowisko do udokumentowanej listy potrzebnych zmiennych systemowych oraz jawnie wskazanych referencji; nie przekazywać całego środowiska sklr.

Wyjątki klienta i stderr procesu mogą zawierać dane poufne. Do logów, panelu i historii przekazywać bezpieczne kody i podsumowania, bez surowych wyjątków. Wyłączyć telemetrię treści argumentów, opisów narzędzi i wyników. Opisy, schematy i wyniki zewnętrzne są danymi niezaufanymi, nie instrukcjami sklr; panel renderuje je jako tekst, nie HTML.

Surowe argumenty oraz wyniki wywołań pozostają w RAM, poza SQLite i telemetrią. Historia zachowuje identyfikator profilu, nazwę narzędzia, stany, czasy i bezpieczny wynik operacji. Ograniczony wynik pobiera model przez get_task w osobnym polu external_result; TTL 5 minut, z kontrolą dostępu do bieżącego włączonego profilu. Restart, wygaśnięcie, wyłączenie lub usunięcie oznacza unavailable. Nie zmieniać istniejącego kontraktu secret_result. Rozszerzenie kolejki musi obsłużyć ten kanał osobno, bez podszywania się pod secret_reveal.

## 4. Panel

Zakładka „Zewnętrzne MCP” wykorzystuje istniejącą autoryzację, kontrolę Host/Origin i CSP panelu. Nie dodawać endpointów administracyjnych na porcie MCP.

Lista pokazuje alias, opis, transport, włączenie i ostatni test. Akcje: Dodaj, Edytuj, Testuj połączenie, Pokaż narzędzia, Włącz/Wyłącz, Usuń. Formularz zmienia pola zależnie od transportu. Nowy profil domyślnie wyłączony; użytkownik może jawnie włączyć go podczas zapisu.

Test i pobieranie narzędzi są zadaniami w tle, nie blokującym requestem HTTP. Jawna akcja użytkownika w panelu autoryzuje tę konkretną operację, także dla wyłączonego profilu. Test stdio pokazuje ostrzeżenie o uruchomieniu programu. Zapis samego profilu nie wykonuje żadnego połączenia.

## 5. API modelu i zgody

Nie importować dynamicznie obcych narzędzi do głównego katalogu FastMCP. API:

- `list_external_mcp()` — lokalny, szybki odczyt zapisanych profili i dostępności; bez wartości sekretów.
- `list_external_mcp_tools(alias)` — pobranie nazw, opisów i pełnych inputSchema; operacja przez kolejkę, zwraca task_id i status.
- `call_external_mcp_tool(alias, tool_name, arguments)` — wywołanie przez kolejkę, zwraca task_id i status. Domyślnie wymaga każdorazowej zgody w panelu, bez „Ufaj” w oknie zgody. Wyjątek: profil jawnie oznaczony w panelu jako zaufany (sekcja 5a).

Model nie ma narzędzia do zapisu profili, włączania serwerów ani zatwierdzania operacji. Włączenie profilu autoryzuje pobieranie katalogu narzędzi, również uruchomienie stdio potrzebne do discovery; nie autoryzuje dowolnych wywołań narzędzi. Panel jasno wyjaśnia ten zakres.

Przed wykonaniem sprawdzić włączenie i rewizję konfiguracji. Zmiana konfiguracji, wyłączenie lub usunięcie podczas oczekiwania powoduje konflikt, nie wykonanie na nowym endpointcie. Sprawdzić nazwę narzędzia i argumenty względem aktualnego schematu; zmiana schematu po zgodzie nie może rozszerzać zaakceptowanej operacji bez ponownego potwierdzenia.

Podgląd zgody zawiera alias, narzędzie i ograniczone, maskowane argumenty; pełne argumenty nie trafiają do historii. Zewnętrzne adnotacje readOnly/destructive nie stanowią podstawy pomijania zgody.

## 5a. Zaufanie profilu

Użytkownik może w panelu oznaczyć profil jako zaufany, aby wywołania jego narzędzi nie wymagały zgody za każdym razem (np. przeglądarka, którą użytkownik widzi i może zamknąć, albo dokumentacja tylko do odczytu). To trwała decyzja pojedynczego profilu, nie globalna.

- Zaufanie ustawia i cofa wyłącznie użytkownik w panelu. Model nie ma narzędzia do tego i nie może go wywołać pośrednio.
- Dotyczy wywołań narzędzi. Uruchomienie procesu stdio nadal wymaga włączenia profilu; zaufanie nie zastępuje włączenia.
- Przypięcie do katalogu: przy nadawaniu zaufania panel pobiera aktualny katalog narzędzi, pokazuje listę (nazwy, opisy jako tekst) i zapisuje nazwy oraz skróty schematów. Zaufanie obejmuje wyłącznie te narzędzia. Narzędzie spoza zapisanego zbioru albo ze zmienionym schematem wymaga zwykłej zgody.
- Zmiana ustawień połączenia (transport, adres, polecenie, argumenty, katalog, referencje środowiska, nagłówki, CA) zeruje zaufanie. Włączanie i wyłączanie profilu go nie zeruje.
- Warunki zaufania sprawdza się tuż przed wykonaniem, nie tylko przy zgłoszeniu. Cofnięcie zaufania lub zmiana połączenia podczas oczekiwania w kolejce powoduje konflikt albo wymóg zgody, nie wykonanie.
- Zaufane wywołanie tworzy zadanie widoczne w panelu bez okna zgody. Zaufanie nie omija: włączenia profilu, limitów danych i czasu, kontroli adresów, walidacji argumentów, braku ponowień ani zasad ochrony wyników (sekcja 3).
- Limit tempa: najwyżej 30 zaufanych wywołań na minutę na profil; po przekroczeniu wymagana jest zgoda. Wartość potwierdzona przez użytkownika.
- Panel pokazuje wyraźny znacznik zaufania i ostrzeżenie: treść zewnętrzna (np. strony internetowe) może próbować nakłonić model do kolejnych wywołań, których nie zatwierdzasz pojedynczo. Dla narzędzi zmieniających stan (np. nawigacja, zapis, usuwanie) użytkownik ponosi tę decyzję świadomie.
- Adnotacje zewnętrzne (readOnly, destructive) nadal nie są podstawą pomijania zgody. Zaufanie jest wyłącznie decyzją użytkownika.

## 6. Wykonanie i ograniczenia

Żadne wywołanie MCP nie czeka na sieć ani decyzję dłużej niż istniejący budżet harnessa. Zadania zewnętrzne mają ograniczony timeout, limity liczby narzędzi, rozmiaru schematów, argumentów i wyników; konkretne wartości i walidacja muszą być ustalone przed kodem klienta. Wynik jawnie wskazuje ograniczenie lub odrzucenie zbyt dużych danych; nie wolno cicho ucinać schematu i prezentować go jako kompletny.

Zachować typowane bloki content, structuredContent i isError; nie sprowadzać wszystkich bloków do str(). Nie pobierać automatycznie URL ani zasobów wskazanych w wynikach. Błąd narzędzia nie oznacza sukcesu zadania biznesowego.

Timeout/anulowanie zamyka lokalną sesję i sprząta proces stdio. Nie gwarantuje cofnięcia operacji wykonanej zdalnie. Nie ponawiać automatycznie call_tool po błędzie sieci, timeout lub utracie sesji: skutek może już istnieć. Panel i model otrzymują jawny komunikat o niepewnym wyniku.

## 7. Granice v1 i odbiór

Poza zakresem: OAuth i przeglądarkowy login, trwałe sesje, automatyczny import narzędzi, zasoby/prompty MCP, globalne zaufanie, instalowanie serwerów, sandbox oraz automatyczna migracja profili ze starego vibecodera.

Wdrożenie obejmuje rejestr/config, adapter klienta, administrację panelu, trzy narzędzia, kolejkę i poufny kanał wyników. Testy z klientem atrapowym nie łączą się z prawdziwymi serwerami. Pytest i serwer uruchamia użytkownik zgodnie z AGENTS.

Odbiór: zapis i restart; wyłączenie bez utraty profilu; konflikty rewizji; zmiana projektu; HTTP/SSE/stdio; ścieżki Windows ze spacjami; brak sekretu i błędy TLS; zgody; limity, timeout i cancel; brak retry wywołania; zmiana konfiguracji/schematu po zgodzie; brak danych poufnych w bazie i logach; TTL i niedostępność wyniku po odłączeniu; ochrona panelu i regresje istniejących zadań.
