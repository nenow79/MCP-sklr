---
type: Procedure
title: Migracja starego projektu z plików vibecoder do dokumentacji OKF
description: Kolejność dołączenia projektu, uporządkowania wiedzy i włączenia wymogu OKF bez blokowania pracy przed konwersją.
---

# Migracja starego projektu do OKF

Instrukcja dotyczy projektu bez `docs/`, z plikami `.vibecoder_context.md` i `.vibecoder_checkpoints.md` w korzeniu.

Nie trzeba tworzyć `docs/` przed dołączeniem projektu ani najpierw zapisywać dokumentacji jako zwykłego Markdown. Po uporządkowaniu treści nowe dokumenty można utworzyć od razu w OKF.

## 1. Dołącz projekt w trybie zgodności

Dodaj katalog projektu jako root w panelu Foldery. Pozostaw domyślny tryb zapisu **Zgodność** (`compatible`). Do proponowanych zapisów potrzebny jest root z prawem `rw`; samego przeglądu można dokonać z `ro`.

Brak `docs/`, AGENTS lub metadanych OKF nie wymaga wcześniejszej konwersji. LLM może odczytać stare pliki przez zwykłe narzędzia plikowe. `prepare_project` i `knowledge_audit` rozpoznają oba wskazane pliki legacy bez ręcznego rejestrowania zbioru wiedzy.

Jeśli potrzebujesz również wyszukiwania legacy przez narzędzia wiedzy, możesz tymczasowo dodać projektowy zbiór o profilu `legacy`, z pustym podkatalogiem. Nie jest to warunek migracji.

## 2. Przejrzyj źródła i ustal aktualność

Poproś LLM o przeczytanie zasad projektu oraz obu plików legacy i sprawdzenie ustaleń wobec kodu. Sam audyt metadanych nie zastępuje przeczytania treści.

LLM powinien rozdzielić:

- aktualny stan i obowiązujące ustalenia;
- architekturę, decyzje i instrukcje użyteczne na przyszłość;
- otwarte zadania;
- historię i nieaktualne checkpointy;
- sprzeczności lub informacje wymagające Twojego potwierdzenia.

Nie należy automatycznie uznawać ostatniego checkpointu za prawdziwy bieżący stan.

## 3. Zaakceptuj strukturę dokumentacji

Przed zapisem LLM przedstawia plan, docelowe ścieżki i proponowane typy dokumentów. Przykładowy podział to stan projektu, backlog, architektura, decyzje i procedury w odpowiednich podkatalogach `docs/`.

Nie trzeba tworzyć wszystkich tych kategorii, jeżeli projekt ich nie potrzebuje. Dobieraj typy zgodnie z [konwencją kuracji](specs/okf-integration/curation.md). Zasady dla agenta pozostają w `AGENTS.md`; nie wymuszamy na nim formatu OKF.

## 4. Utwórz dokumentację od razu w OKF

Po akceptacji planu LLM proponuje utworzenie katalogów i dokumentów z poprawnym frontmatterem OKF. Zapisy przez MCP wymagają zatwierdzenia w panelu.

Po pojawieniu się bezpośredniego `<root>/docs` dokumentacja zostaje wykryta przy następnym użyciu wiedzy lub odświeżeniu widoku panelu. Restart nie jest potrzebny. Indeks treści powstaje osobno, na żądanie; samo wykrycie folderu nie oznacza ukończonego indeksowania.

Zachowaj niezmienione oryginały legacy do sprawdzenia kompletności migracji. Docelowo mogą trafić do `docs/archive/legacy/`; archiwum pozostaje poza indeksem projektu. Jeśli je przeniesiesz, zaktualizuj odwołania i zasady projektu, aby LLM nie wymagał już plików w korzeniu. Obecne narzędzia MCP nie udostępniają ogólnego przenoszenia/usuwania plików, więc samo przeniesienie może wymagać ręcznej operacji.

Jeśli na czas migracji dodano ręczny zbiór `legacy` lub `mixed`, usuń jego konfigurację przed odbiorem automatycznego `docs/`. Ręczne nakładające się wiązanie ma pierwszeństwo i może zasłaniać automatyczne źródło. Usunięcie konfiguracji zbioru nie usuwa dokumentów.

## 5. Sprawdź rezultat i włącz wymóg OKF

Sprawdź, czy aktualna wiedza została zachowana, historia jest oddzielona, a niepewne informacje nie zostały przedstawione jako potwierdzone. Wykonaj audyt oraz sprawdź diagnostykę indeksu, wyszukiwanie i odczyt wybranych dokumentów. Wynik `partial` wymaga sprawdzenia powodów; nie potwierdza kompletności indeksu.

Dopiero potem w panelu Wiedza wybierz **Wymagaj OKF** (`require_okf`), wskaż chronione podkatalogi i wyjątki. Nie trzeba obejmować wymogiem archiwów, standardów upstream ani przykładów. Konwersja sama nie włącza tego trybu.

W trybie zgodności nadal można tworzyć zwykły Markdown. Już poprawne dokumenty OKF są chronione przed utratą formatu w obu trybach, również w wyjątkach. Szczegóły: [polityka zapisu](specs/okf-integration/write-policy.md).

## Szybka konwersja bez reorganizacji

`knowledge_audit` i `knowledge_convert` potrafią dodać brakujące metadane bezpośrednio do `.vibecoder_context.md` i `.vibecoder_checkpoints.md`, bez tworzenia `docs/`. Konwersja zachowuje body: nie dzieli dokumentów, nie rozstrzyga sprzeczności i nie oddziela historii od aktualnego stanu.

To wariant dla źródeł, których obecny układ chcesz zachować. Dla rozbudowanych checkpointów zalecana jest opisana wyżej reorganizacja. Kontrakt narzędzi: [projekt i konwersja](specs/okf-integration/project-and-conversion.md).

## Polecenie startowe dla LLM

> Przejrzyj zasady projektu, stare pliki `.vibecoder_context.md` i `.vibecoder_checkpoints.md` oraz kod projektu. Zaproponuj przeniesienie aktualnej wiedzy do uporządkowanego `docs/` w formacie OKF. Oddziel historię od bieżących ustaleń, wskaż sprzeczności i niepewności. Nie zakładaj, że ostatni checkpoint jest aktualny. Zachowaj oryginalne źródła. Najpierw przedstaw plan, strukturę plików i proponowane typy do akceptacji; nie zmieniaj jeszcze plików ani nie włączaj wymogu OKF.
