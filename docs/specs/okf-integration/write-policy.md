# OKF: polityka formatu dokumentacji projektu

Polityka dotyczy wyników zapisu plików `.md` przez MCP. Nie jest sandboxem skryptów ani zewnętrznych edytorów. Stan implementacji i odbioru: [CURRENT](../../plan/CURRENT.md).

## Domyślna zgodność

Brak wpisu polityki oznacza `compatible`. Stare projekty mogą być czytane, indeksowane i edytowane bez konwersji. Nowe dokumenty mogą być zwykłym Markdown; pozostają dostępne dotychczasowe kodowania narzędzi plikowych. Samo wykrycie docs, włączenie bundle lub wykonanie konwersji nie zmienia trybu.

W obu trybach istniejący dokument poprawny według lokalnego parsera OKF v1 musi pozostać poprawny po zmianie. Usunięcie frontmattera, wymaganego type lub uszkodzenie YAML daje `OKF_FORMAT_LOSS`. To kontrola formatu, nie prawdziwości treści ani zachowania każdego klucza metadanych. Mocniejsze reguły knowledge_save pozostają niezależne.

Ochrona istniejącego OKF obejmuje `<root>/docs`, dwa znane pliki legacy w korzeniu, jawne zakresy polityki oraz podkatalogi ręcznych projektowych bundles o profilu docs. Dotyczy również wyłączonych źródeł. Sprawdzamy fizyczną ścieżkę oraz ścieżki wejściowych wiązań; wybór zagnieżdżonego roota lub link-aliasu nie wyłącza ochrony nadrzędnego zakresu. Nie obejmujemy automatycznie wszystkich plików `.md` w repo ani formatów skills.

## Jawny wymóg OKF

Użytkownik włącza `require_okf` w panelu Wiedza po wyborze zakresu i wyjątków. Przed włączeniem powinien wykonać audyt oraz świadomą konwersję wybranych źródeł. Panel nie konwertuje plików ani nie deklaruje zgodności całego drzewa; nie ma twardej blokady włączenia z powodu nieprzekonwertowanych plików.

Nowy lub zmieniany `.md` w chronionym zakresie musi być poprawny według parsera v1, inaczej zapis jest odrzucany jako `OKF_REQUIRED`. Odczyt zwykłego Markdown nadal działa. Zapis identycznych bajtów nie wymusza konwersji. Po powrocie do compatible nadal nie wolno degradować już poprawnego OKF.

Zakres to lista niepustych względnych podkatalogów projektu, domyślnie `["docs"]`. Wyjątki są względne wobec roota: dokładna ścieżka pliku lub katalog zakończony `/`, bez wildcardów, ścieżek absolutnych i `..`. Obowiązują reguły nazw Windows. Maksymalnie 50 zakresów i 50 wyjątków, każdy do 500 znaków. Porównywanie ścieżek respektuje semantykę hosta.

Domyślne wyjątki to `docs/archive/` i `docs/archives/`. Pliki o nazwach AGENTS.md, index.md i log.md są wyjątkami od wymogu niezależnie od podkatalogu. Standardy i przykłady użytkownik może wyłączyć jawnie, np. `docs/specs/OKF/`. Wyjątki nie pozwalają degradować poprawnych dokumentów OKF.

## Konfiguracja i panel

Przykład ustawienia przez panel, nie polecenie zmiany lokalnego configu:

```json
{
  "knowledge": {
    "project_write_policies": [
      {
        "root_path": "C:/Projects/example",
        "mode": "require_okf",
        "subpaths": ["docs"],
        "exclusions": ["docs/archive/", "docs/archives/", "docs/specs/OKF/"]
      }
    ]
  }
}
```

Polityka jest przypięta do kanonicznej ścieżki zatwierdzonego roota. Zmiana aliasu i restart zachowują preferencję; nowa ścieżka pod starym aliasem jej nie dziedziczy. Usunięcie roota nie nadaje dalszego dostępu, a ustawienie pozostaje do ewentualnego ponownego podłączenia tej samej ścieżki. Polityka jest niezależna od włączenia automatycznych docs i bundles.

GET `/api/knowledge` zwraca `roots[].write_policy` z mode, subpaths, exclusions i bezpiecznym error. POST `/api/knowledge/policy` przyjmuje `{policy: {root, mode, subpaths, exclusions}, revision}`. Serwer wylicza root_path; nie ufa ścieżce podanej w payloadzie. Wymagane są sesja panelu, Host/Origin i aktualna opaque revision. Zapis idzie przez ConfigStore.update z rollbackiem. Błędna lub powielona konfiguracja nie wyłącza ochrony po cichu; zapisy są blokowane, a panel pokazuje diagnostykę.

prepare_project zwraca write_policy dla wybranego roota. Przy nakładających się roots zapis musi spełnić wszystkie obowiązujące polityki, również nadrzędną, której widok danego roota nie zastępuje.

## Egzekwowanie i granice

- Przygotowanie edit_file, create_file (także overwrite), dokładnych zapisów wiedzy/konwersji i create_tree sprawdza cały proponowany wynik przed zgłoszeniem zadania do zgody.
- FileTransaction powtarza sprawdzenie przy verify, wykonaniu i bezpośrednio przed publikacją pod blokadami zapisów i konfiguracji. Zmiana polityki podczas oczekiwania na zgodę nie pozwala wykonać sprzecznego starego planu.
- create_tree ponownie buduje i waliduje plan po zgodzie oraz pod blokadami przed wykonaniem. Odrzucenie nie tworzy katalogów ani plików.
- manage_folder odrzuca rename katalogu przecinającego zakres require_okf. Zapobiega to wniesieniu nieprzekonwertowanych plików przez zmianę nazwy; taka reorganizacja wymaga wcześniejszej zmiany zakresu w panelu. Create pustego katalogu pozostaje dostępne.
- Rollback może przywrócić poprzedni Markdown po niedokończonej konwersji, również w trybie require_okf. Polityka nie blokuje odtwarzania pierwotnych bajtów.

Walidacja korzysta z limitów parsera v1 (maks. 1 MiB dokumentu), nie dopisuje metadanych i nie naprawia YAML. Zmiana plików przez proces użytkownika lub skrypt skilla nie jest objęta tą kontrolą; dotychczasowe ograniczenia TOCTOU i best-effort rollback pozostają aktualne.
