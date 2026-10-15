# Wersja OKF do odbioru na Windows

Development: Linux. System docelowy: Windows. To kandydat do końcowych testów,
nie deklaracja zweryfikowanego wdrożenia na Windows.

## Co jest dostępne

- Panel Wiedza: dodanie konfiguracji bundle, wyłączenie/włączenie, usunięcie
  konfiguracji bez usuwania plików, diagnostyka i odświeżenie indeksu.
- Bezpośrednie <root>/docs podpina się automatycznie przy starcie i przed użyciem
  wiedzy. Panel umożliwia wyłączenie przypięte do ścieżki projektu. Ręczne wpisy
  zachowują pierwszeństwo; nie tworzymy dokumentów ani metadanych.
- knowledge_bundles/search/get/save: centralne oraz projektowe zbiory. Odczyt
  outline/sekcji/dokumentu, maskowanie, rewizje i zapis po zgodzie w panelu.
- Profile project: docs (jawny podkatalog, zwykle docs), legacy (dokładnie dwa
  pliki .vibecoder_context.md i .vibecoder_checkpoints.md w korzeniu), mixed
  (docs/ i te dwa pliki). Bez skanowania całego projektu. Archiwum i AGENTS nie
  trafiają do domyślnego indeksu projektu. Root i subpath muszą już istnieć.
- Domyślne search scope=all obejmuje centralne zbiory i projekt domyślnego roota.
  Inny projekt wybieramy jawnie przez bundle_id. Nie ma przesłaniania wyników.
- Projektowy Markdown bez frontmattera ma format=markdown, type=unknown,
  metadata={} i warning unconverted. Można go czytać i wyszukiwać od razu.
- Polityka zapisu: domyślny compatible zachowuje obsługę starszych projektów.
  W obu trybach poprawny OKF jest chroniony przed degradacją. W panelu można
  jawnie włączyć require_okf dla wybranych podkatalogów z wyjątkami; konwersja
  nie włącza tego trybu. [Kontrakt i granice](specs/okf-integration/write-policy.md).
- prepare_project zwraca root/nested AGENTS, rewizje, jawne oznaczenie skróconych
  instrukcji, kandydatów pliku stanu, audyt i szablon brakującego AGENTS.
  create_agents=true tworzy zadanie do zgody; nie nadpisuje istniejących zasad.
- knowledge_audit bada docs/ i dwa root legacy pliki. Dowolny inny podkatalog, np. doc/, można indeksować profilem docs, ale audyt/konwerter go jeszcze nie obsługują. knowledge_convert dodaje brakujące metadane
  do jawnych plików i wymaga expected_revision z audytu. Jedna zgoda na maks.
  10 plików, każdy ograniczony limitem dokumentu. Body zachowane byte-for-byte,
  BOM i EOL zachowane. Istniejący YAML/komentarze nie są zastępowane dumpem.

Polityka przygotowania i migracji jest **advisory**. Domyślny tryb zgodności nie wymaga konwersji; jawny require_okf kontroluje wyniki zapisu wskazanego zakresu. Serwer przypomina o AGENTS,
lecz nie blokuje zwykłych odczytów, zapisów i skryptów z powodu braku pliku lub
konwersji. Stare projekty można dalej obsługiwać dotychczasowymi narzędziami.
Brak MCP-harnessowej gwarancji, że LLM przeczyta lub zrozumie instrukcje.

Indeks jest w pamięci; nie powstaje index.md na dysku.

Nie automatyzujemy semantycznego dzielenia historii, wyboru obowiązującego
checkpointu, tworzenia nowych typów ani dopisywania generated/verified. Błędny
YAML, kolizje nazw, reserved index/log, mixed EOL oraz CP1250 są raportowane
jako wyjątki wymagające osobnej edycji/decyzji. Nie blokują pozostałej pracy.
Skill okf-curator, pełny selektor bieżącej sekcji i pomiar kontekstu są dalszymi
ulepszeniami; audyt i konwersja działają bez instalowania dodatkowego skilla.

## Punkt odniesienia i dostarczenie wersji

Implementacja jest już w commicie `e6cee29` (`feat: complete OKF knowledge workflow`). Dawna instrukcja publikacji niezacommitowanej paczki, manifest i odwołania do /tmp są [historią](archive/pre-okf-2026-10-15/windows-okf.md). Bieżący status publikacji remote nie został sprawdzony; przed aktualizacją upewnij się, że wybrane zdalne repo zawiera ten commit. Ta instrukcja nie wymaga ponownego tworzenia commita implementacji.

Aktualny zakres potwierdzeń: [CURRENT](plan/CURRENT.md). Użytkownik potwierdził dodanie wiedzy centralnej; projektowy sklr-okf miał partial/3 dokumenty o nieustalonej przyczynie. Przed pełnym odbiorem odczytaj reasons i diagnostykę, nie zakładaj kompletności indeksu.

## Aktualizacja na Windows

1. Zatrzymaj stary proces MCP i panel. Zachowaj kopię lokalnego config.json.
2. W checkout main wykonaj git pull --ff-only po publikacji na origin/main.
   Nie kopiuj configu, venv, tasks.db ani katalogu wiedzy z Linuksa.
3. Aktywuj lokalny venv Windows. Nie zmieniono zależności requirements.txt.
4. Uruchom testy, następnie serwer jak dotychczas, a w panelu wykonaj Ctrl+F5.
   Nowe narzędzia MCP mogą wymagać ponownego połączenia klienta.

```powershell
git pull --ff-only
.\venv\Scripts\Activate.ps1
python --version
python -m pip check
python -m pytest tests/test_okf_discovery.py tests/test_project_knowledge.py tests/test_project_tools_registration.py tests/test_panel_static.py tests/test_panel_api.py tests/test_panel_roots.py tests/test_panel_skills.py -q -rs
python -m pytest tests/test_okf_write_policy.py tests/test_file_changes.py tests/test_file_transaction.py tests/test_edit_tools.py tests/test_tree_tools.py tests/test_folder_tools.py tests/test_roots_registry.py -q -rs
python -m pytest tests/test_okf_bundles.py tests/test_okf_documents.py tests/test_okf_scanner.py tests/test_okf_search.py tests/test_okf_service.py tests/test_knowledge_tools.py tests/test_knowledge_tools_registration.py tests/test_knowledge_write_tools.py tests/test_knowledge_write_registration.py tests/test_secret_tools_registration.py -q -rs
python -m pytest tests/test_file_transaction.py tests/test_file_changes.py tests/test_okf_paths.py tests/test_okf_writer.py tests/test_okf_preview.py tests/test_okf_parser.py tests/test_okf_public_view.py -q -rs
```

Po przejściu paczek wystarczy jeden pełny pytest repo. Jeśli masz Node,
sprawdź istniejące testy interfejsu (bez doinstalowywania bibliotek):

```powershell
python -m pytest -q -rs
node --test tests/test_panel_notifications.cjs tests/test_panel_skills_view.cjs tests/test_panel_secret_view.cjs tests/test_panel_details_stability.cjs
```

Test rzeczywistego junctiona może wymagać możliwości utworzenia junctionów.
Raportuj wynik i przyczynę skip, zamiast traktować skip jako test zaliczony.
Testy zewnętrznych skilli zależą od ich dostępności na danej maszynie.

Ostatnie potwierdzone wyniki użytkownika na Linuksie przed rozszerzeniem o
panel/projekty/migrację: C2c 79 passed; C3a początkowo 208 passed + błąd testu,
potem preview 21 passed; C3b po poprawce stagingu 86 passed, 1 skipped.
Nowe rozszerzenie ma kontrolę AST/importów, nie potwierdzony wynik pytest.
Agent nie uruchamiał pytest, Node ani serwera.

## Ręczny odbiór po uruchomieniu

1. Panel Foldery: upewnij się, że Windows roots i ich tryby są aktualne.
   Dodaj wąski root dla istniejącego katalogu ~/.agents/okf, jeśli chcesz wiedzę
   centralną. Nie nadawaj dostępu rw do całego ~/.agents.
2. Panel Wiedza: dodaj centralny bundle w istniejącym podkatalogu. Projekt z docs/
   ma pojawić się automatycznie. Wyłącz/włącz go, zmień alias i zrestartuj serwer:
   preferencja ma pozostać. Usuń root: źródło i wyniki mają zniknąć przy następnym
   użyciu wiedzy. Sprawdź też zniknięcie/powrót docs oraz docs utworzone po starcie.
   Dla ręcznego projektowego bundle docs wybierz subpath=docs; dla legacy/mixed pusty.
   Sprawdź odrzucenie duplikatu, overlap i brakującego roota oraz diagnostykę.
3. knowledge_save create -> task_id -> Allow -> get_task. Następnie search
   (odczekaj task odświeżenia) -> get outline -> get section z expected_revision.
   Update wymaga rewizji z get. Sprawdź Deny, no-op i stale revision.
4. Wyłącz bundle w panelu: get/search/save mają odmówić zwrotu jego treści.
   Włącz ponownie. Usuń konfigurację: pliki mają nadal istnieć.
5. W testowym projekcie z docs albo dwoma .vibecoder_* wywołaj prepare_project.
   Sprawdź root/nested AGENTS i warningi skrócenia. Brak AGENTS ma dać propozycję,
   bez automatycznego zapisu; create_agents=true wymaga Allow w panelu.
6. Użyj automatycznego docs lub ręcznego bundle project z właściwym profilem. Wyszukaj zwykły Markdown przed
   konwersją: ma być format=markdown, bez zmyślonego typu lub weryfikacji.
7. knowledge_audit: wybierz convertible=true, zaproponuj metadata_yaml, przekaż
   filepath i expected_revision do knowledge_convert. Sprawdź diff, najpierw
   Deny, potem Allow. Body/BOM/EOL mają pozostać identyczne, powtórzenie to no-op.
   Zmień plik po audycie: stara rewizja ma odmówić konwersji.
8. Zmień domyślny root: search all nie ma mieszać treści starego projektu.
   Jawny bundle_id pozwala wrócić do innego włączonego projektu.
9. Zrestartuj MCP: nowe rewizje, pusty indeks odbudowany przez task; stare plany
   nie są wznawiane z bazy. Po cleanup failure sprawdź committed i plik przed retry.
10. W starym projekcie pozostaw compatible: zwykły Markdown ma dać się czytać,
    tworzyć i edytować. Przekonwertuj jeden dokument: usunięcie frontmattera przez
    edit_file/create_file ma być odrzucone, a tworzenie kolejnego zwykłego Markdown
    nadal dozwolone. Konwersja nie może zmienić trybu.
11. W panelu ustaw require_okf, wybierz podkatalogi i wyjątki. Zwykły create/edit/tree
    w zakresie ma być odrzucony; poprawny OKF i zwykły Markdown w wyjątkach mają
    działać. Wyjątek nie pozwala degradować już poprawnego OKF. Odczyt starego
    Markdown działa także w require_okf. Sprawdź trwałość po restarcie i zmianie aliasu.
12. Przygotuj zapis Markdown w compatible, następnie przed Allow włącz require_okf:
    zapis ma zostać odrzucony. Powrót do compatible przywraca zwykły zapis Markdown,
    lecz nadal chroni już poprawny OKF. Rename przecinający zakres require_okf jest blokowany.

Używaj sztucznych plików. Prawdziwy bundle i zakres danych wybierasz sam w panelu.
