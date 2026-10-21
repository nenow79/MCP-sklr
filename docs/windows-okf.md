---
type: Procedure
title: Aktualizacja i odbiór sklr na Windows
description: Przekazanie wersji, testy sesji i wznowienia oraz ręczny odbiór MCP, panelu i OKF na Windows.
tags: [sklr, okf, sessions, windows, testing]
---
# Wersja sklr do odbioru na Windows

Development: Linux. System docelowy: Windows. To kandydat do końcowych testów,
nie deklaracja zweryfikowanego wdrożenia na Windows.

Stan dokumentu: 2026-10-21. Priorytetem są sesje i autonomia według kontraktu; lokalny kod zawiera A2b3 i podłączenie opt-in A2b4, z zaliczonymi testami A2b4 i wskazaną regresją Linux; bez odbioru rzeczywistego MCP/panelu i Windows. Automatyczny wykonawca testów/poprawek pozostaje niewdrożony. Zadania przygotowania wydania są wyłącznie w [backlogu](plan/backlog.md), stan potwierdzeń w [CURRENT](plan/CURRENT.md).

## Zakres przekazania bety

Obecny kod zawiera opt-in sesje/pauzę/wznowienie, fundamenty A2b1/A2b2, runtime A2b3 i lokalne podłączenie API/UI/bootstrapu A2b4. Paczka 820 passed in 24.67s jest wcześniejszym wynikiem wskazanej regresji A2b3 na Linux; A2b4: 48 passed in 1.50s, szersza regresja: 941 passed in 59.45s, DOM Node: 4/4 na Linux. Dokładny zakres w CURRENT i raporcie A2b4; pełnej regresji repo nie powtarzano. Włączenie wymaga w lokalnym configu obu flag work_sessions.enabled=true i autopilot.enabled=true oraz restartu; nie zmieniono config.json i nie uruchomiono docelowej aplikacji. Sama flaga nie zatwierdza delegacji: potrzebna decyzja w panelu i handshake planu.

Betę z działającym A2 można przekazać po walidacji lokalnej integracji A2b3/A2b4, dostępnej regresji i przygotowaniu konfiguracji/publikacji (W1–W4). Pierwszy pełny odbiór prawdziwego MCP/panelu i mechanizmów Windows pozostaje pracą na maszynie docelowej (A2b5, S2–S5, W5). Numerów etapów używamy zgodnie z backlogiem; ta procedura nie jest dodatkową listą zadań.

A3/A3b i A4 nie muszą blokować bety samego A2, ale taka beta nie obejmuje automatycznego uruchamiania długich testów ani cykli poprawek. Jeśli te funkcje mają być celem pierwszego odbioru na Windows, ich kontrakty i implementacje muszą wejść przed publikacją tego kandydata. Nie określać bety A2 jako zakończenia całej autonomii A2–A4.

## Co jest dostępne

- Sesje pracy opt-in: Start, „Wstrzymaj po kroku”, checkpoint RAM, przygotowanie wznowienia i potwierdzenie zaktualizowanego planu przez model. Jedna sesja obejmuje wszystkie rozmowy instancji. Domyślnie work_sessions.enabled=false; po włączeniu brak Startu blokuje narzędzia robocze. [Kontrakt](specs/work-session-pause.md).
- Zewnętrzne MCP: profile i trust w panelu, HTTP/SSE/stdio, katalog i wywołania przez model. Odbiór częściowy; bez deklaracji pełnej ochrony SSRF lub sandboxu. [Kontrakt](specs/external-mcp-support.md).
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

Indeks roboczy jest w RAM, z podpisanym cache SQLite na dysku i uzgodnieniem źródeł w tle. Nie powstaje index.md. Pliki pozostają źródłem prawdy; cache nie zastępuje kontroli aktualności.

Nie automatyzujemy semantycznego dzielenia historii, wyboru obowiązującego
checkpointu, tworzenia nowych typów ani dopisywania generated/verified. Błędny
YAML, kolizje nazw, reserved index/log, mixed EOL oraz CP1250 są raportowane
jako wyjątki wymagające osobnej edycji/decyzji. Nie blokują pozostałej pracy.
Skill okf-curator, pełny selektor bieżącej sekcji i pomiar kontekstu są dalszymi
ulepszeniami; audyt i konwersja działają bez instalowania dodatkowego skilla.

## Punkt odniesienia i dostarczenie wersji

Baza przeglądu: `9176967` (`feat: enable work-session pause and resume`), obejmująca wcześniejsze OKF i zewnętrzne MCP. Przed commitem przekazania odłączonych A2b1/A2b2 odczyt SSH zdalnego main potwierdził tę bazę. Punkt kontynuacji to commit zawierający niniejszą aktualizację, poprawkę S1, pakiet autopilot/ i jego testy; hash publikacji sprawdź w raporcie przekazania oraz przez git log -1 --oneline po aktualizacji. [Prompt startowy](continue-work.md) opisuje wejście w dalszą pracę. Ten commit nie jest jeszcze betą z aktywnym A2; dokładny kandydat bety powstanie po A2b3/A2b4 i przygotowaniu wydania. Dawny manifest i odwołania do /tmp są [historią](archive/pre-okf-2026-10-15/windows-okf.md).

Aktualny zakres potwierdzeń: [CURRENT](plan/CURRENT.md). Wcześniejsze cache ready/35→36 i start z cache odebrano; nie są bieżącą liczbą dokumentów na Windows. Przed odbiorem odczytaj aktualny stan/reasons i diagnostykę zamiast zakładać kompletność.

**Konfiguracja po W1:** repo zawiera przenośny `config.example.json`, a lokalny
`config.json`, jego plik tymczasowy i kopie `.bak*` są ignorowane. Nazwa, lokalizacja,
odczyt, zapis oraz uzupełnianie brakujących kluczy domyślnymi pozostają zgodne.
Istniejący config zachowujemy w całości; przykładu nie kopiujemy na jego miejsce.
Usunięcie ze śledzenia nie usuwa dawnych wersji configu z historii Git.

## Aktualizacja na Windows

Pierwsze przejście z wersji śledzącej config wymaga poniższej migracji.
Wykonaj ją dopiero po publikacji uzgodnionego kandydata zawierającego W1.
Zatrzymaj MCP i panel, przejdź do katalogu repo, sprawdź gałąź i `git status`.
Zachowaj lokalne zmiany kodu przed aktualizacją; nie używaj `reset --hard` ani
wymuszonego pull. Osobno zachowaj lokalne bazy tasks/work_sessions/autopilot
oraz cache wiedzy z kluczem. Nie kopiuj środowiska ani konfiguracji z Linuksa.

Blok PowerShell tworzy unikalną kopię configu w sąsiednim katalogu poza checkoutem,
weryfikuje ją przez SHA-256 i odmawia działania przy staged zmianach configu.
Po sprawdzeniu kopii przywraca wyłącznie roboczy config do bieżącej wersji Git,
aby lokalne ustawienia nie blokowały usunięcia pliku ze śledzenia podczas pull.
`finally` odtwarza własny config także po nieudanej aktualizacji.
Błąd zatrzymuje procedurę; nie uruchamiaj aplikacji przed sprawdzeniem pliku.

```powershell
$ErrorActionPreference = 'Stop'
$repoDirectory = (Get-Location).Path
$configPath = Join-Path $repoDirectory 'config.json'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw 'Brak lokalnego config.json; sprawdź kopię przed migracją.'
}
git diff --cached --quiet -- config.json
if ($LASTEXITCODE -ne 0) { throw 'Config ma staged zmiany; rozstrzygnij je przed migracją.' }
$backupDirectory = Join-Path (Split-Path -Parent $repoDirectory) ('sklr-config-backup-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backupDirectory | Out-Null
$backupPath = Join-Path $backupDirectory 'config.json'
$originalHash = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash
Copy-Item -LiteralPath $configPath -Destination $backupPath
if ((Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash -ne $originalHash) {
    throw 'Kopia configu różni się od oryginału; aktualizacja zatrzymana.'
}
Write-Host "Kopia konfiguracji: $backupPath"
try {
    $trackedConfig = git ls-files -- config.json
    if ($LASTEXITCODE -ne 0) { throw 'Nie udało się sprawdzić indeksu Git.' }
    if ($trackedConfig) {
        git restore --worktree -- config.json
        if ($LASTEXITCODE -ne 0) { throw 'Nie udało się przygotować configu do aktualizacji.' }
    }
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw 'Aktualizacja nie powiodła się; sprawdź Git przed dalszą pracą.' }
}
finally {
    Copy-Item -LiteralPath $backupPath -Destination $configPath -Force
    if ((Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash -ne $originalHash) {
        throw "Config nie został poprawnie przywrócony; zachowana kopia: $backupPath"
    }
}
$trackedConfig = git ls-files -- config.json
if ($LASTEXITCODE -ne 0) { throw 'Nie udało się sprawdzić indeksu po aktualizacji.' }
if ($trackedConfig) { throw 'Pobrana wersja nadal śledzi config.json; W1 nie jest zastosowane.' }
git check-ignore -- config.json
if ($LASTEXITCODE -ne 0) { throw 'Config nie jest ignorowany; sprawdź pobraną wersję.' }
git log -1 --oneline
```

Zachowaj zewnętrzną kopię do zakończenia odbioru. Porównaj pobrany commit
z raportem publikacji; sukces pull bez właściwego commita nie potwierdza migracji.
Przy kolejnych aktualizacjach config pozostaje ignorowany; wystarczy kopia zapasowa
oraz zwykłe `git pull --ff-only`, bez przywracania configu z Git.

Aktywuj istniejący venv Windows. Sprawdź zależności i ich wersje; instalacje i nowe
środowiska wymagają osobnej decyzji. Następnie wykonaj uzgodnione testy i uruchom
MCP jak dotychczas. Odśwież panel przez Ctrl+F5 oraz połączenie klienta MCP.
Poniższe komendy testowe wykonuj po migracji, nie przed zachowaniem configu.

```powershell
.\venv\Scripts\Activate.ps1
python --version
python -m pip check
python -m pytest tests/test_session_activation.py tests/test_session_tools.py tests/test_tasks_sessions.py tests/test_tasks_resume.py tests/test_session_resume_recovery.py tests/test_panel_work_sessions.py tests/test_panel_session_resume.py -q -rs --durations=20
python -m pytest tests/test_okf_discovery.py tests/test_project_knowledge.py tests/test_project_tools_registration.py tests/test_panel_static.py tests/test_panel_api.py tests/test_panel_roots.py tests/test_panel_skills.py -q -rs
python -m pytest tests/test_okf_write_policy.py tests/test_file_changes.py tests/test_file_transaction.py tests/test_edit_tools.py tests/test_tree_tools.py tests/test_folder_tools.py tests/test_roots_registry.py -q -rs
python -m pytest tests/test_okf_bundles.py tests/test_okf_documents.py tests/test_okf_scanner.py tests/test_okf_search.py tests/test_okf_service.py tests/test_knowledge_tools.py tests/test_knowledge_tools_registration.py tests/test_knowledge_write_tools.py tests/test_knowledge_write_registration.py tests/test_secret_tools_registration.py -q -rs
python -m pytest tests/test_file_transaction.py tests/test_file_changes.py tests/test_okf_paths.py tests/test_okf_writer.py tests/test_okf_preview.py tests/test_okf_parser.py tests/test_okf_public_view.py -q -rs
```

Po przejściu paczek wystarczy jeden pełny pytest repo. Jeśli masz Node,
sprawdź istniejące testy interfejsu (bez doinstalowywania bibliotek):

```powershell
python -m pytest -q -rs
node --test (Get-ChildItem tests/test_panel_*.cjs).FullName
```

Test rzeczywistego junctiona może wymagać możliwości utworzenia junctionów.
Raportuj wynik i przyczynę skip, zamiast traktować skip jako test zaliczony.
Testy zewnętrznych skilli zależą od ich dostępności na danej maszynie.

Starsze wyniki paczek są w CURRENT i archiwum; nie oznaczają pełnej regresji przekazywanego commita. Wszystkie powyższe komendy są instrukcją dla użytkownika, nie raportem ich wykonania. Komenda Node korzysta z PowerShell, aby przekazać całą aktualną listę testów panelu.

## Odbiór sesji w rzeczywistym panelu i harnessie

Pracuj na testowym roocie i niepoufnych danych. W lokalnym configu ustaw work_sessions na obiekt z enabled=true, zrestartuj przez dotychczasowe uruchomienie (python main.py w lokalnym venv) i odśwież narzędzia klienta. Domyślne false nie testuje bramki. Panel wymaga nowego linku/tokena po restarcie.

1. Przed Startem odczytaj get_session_state i spróbuj krótkiego read_file. Oczekuj session_not_selected/execution_blocked i braku odczytu; get_session_state działa nadal. Sam odczyt stanu nie potwierdza odmowy wykonania.
2. Wybierz testowe roots i Start. Odczyt roboczy ma działać z session_active. Nie próbuj przełączać przypiętej sesji jako sposobu obejścia pauzy.
3. Uruchom znany ograniczony testowy skill/MCP o dłuższym działaniu. W panelu „Wstrzymaj po kroku”: nowe działanie ma być odrzucone, rozpoczęte ma się zakończyć w swoim limicie z dostępnym wynikiem i metainformacją pauzy. Licznik spada do zera, stan paused. „Anuluj” pozostaje osobną decyzją.
4. Podczas pauzy model zapisuje bezpieczny checkpoint przez save_session_checkpoint z aktualną rewizją; sprawdź potwierdzenie i tekst w panelu. Model kończy odpowiedź i czeka. Odmowa kolejnego read_file ma nadal działać. sklr nie budzi modelu samodzielnie.
5. Odrębny przebieg z faktycznymi awaiting_approval/queued: przygotuj je przed pauzą w testowym zakresie, sprawdź zgodę/wygaśnięcie podczas pauzy bez wykonania. Przygotowanie wznowienia pokazuje rzeczywistą liczbę anulowań; potwierdź ją i sprawdź anulowane statusy. Nie deklaruj odbioru anulowania, gdy licznik wynosił zero.
6. Dodaj uzupełnienie i przygotuj wznowienie. Bramka pozostaje zamknięta; model pobiera get_resume_context i potwierdza zaktualizowany plan bieżącą rewizją/tokenem. Sprawdź plan w panelu, active i udany odczyt. Nowe zadania wymagają obecnych zgód.
7. Zrestartuj MCP: brak automatycznego wyboru/wznowienia. Jawnie odzyskaj zapisaną sesję: paused, brak RAM checkpointu. Przygotuj wznowienie z osobnym potwierdzeniem brakującego checkpointu, potem potwierdź plan przez model. Nie odtwarzaj automatycznie poufnych wyników ani nie ponawiaj operacji o niepewnym skutku.

Zapisz commit, wersje Python/SDK, wynik każdej próby i ograniczenia w raporcie odbioru; do CURRENT przenieś zwięzłe potwierdzenia. Błędy trafiają wyłącznie do backlogu. Te próby odbierają fundament sesji; delegację A2 sprawdza następna procedura, A3/A3b i A4 pozostają niewdrożone.

## Odbiór delegacji A2 przez MCP i panel

Procedura A2b5 realizuje sekcję 10 [kontraktu A2](specs/autopilot-contract.md).
Nie jest raportem wykonania. Na Windows użyj osobnego testowego roota rw poza
katalogiem działającego sklr. Przygotuj w nim istniejące katalogi `allowed` i `outside`.
Włącz lokalnie work_sessions.enabled=true oraz autopilot.enabled=true i zrestartuj
aplikację jak dotychczas; zachowaj pozostałe ustawienia. Decyzje podejmuje użytkownik
w uwierzytelnionym panelu. Nie rozszerzaj delegacji na repo sklr ani jego instrukcje.

1. Przed Startem potwierdź odmowę read_file i brak nowego zadania. Wybierz testowy
   root, Start, następnie pauzę i poczekaj na zero rozpoczętych działań. W delegacji
   wybierz ten root, wyłącznie read_file/create_file, zakres tree=`allowed`,
   overwrite=false, cel i konkretny plan utworzenia sztucznego pliku.
   Przygotuj podgląd, sprawdź przypięcia i limity, zatwierdź. Bramka pozostaje zamknięta.
2. Przygotuj wznowienie; przy braku checkpointu potwierdź go osobno. Model pobiera
   get_resume_context i potwierdza aktualny plan przez confirm_resume_plan.
   Utwórz `allowed/first.txt` z niepoufną treścią: oczekuj zadania done, rzeczywistego
   pliku i jednej zużytej operacji bez osobnego Allow. Obserwacja kolejki zgodnie
   z AGENTS: jedno wait_task(seconds=8), bez pętli; awaiting_approval oznacza STOP.
3. Spróbuj utworzyć `outside/denied.txt`: oczekuj odmowy, braku pliku i wstrzymania
   autopilota/sesji. Nie ponawiaj innym narzędziem ani z innym aliasem.
   Przed dalszą próbą jawnie potwierdź delegację i wykonaj nowy handshake.
4. Sprawdź overwrite=false na istniejącym pliku; treść ma pozostać bez zmian.
   W osobnym przebiegu sprawdź batch obejmujący allowed i outside: odmowa całej
   operacji, bez częściowego odczytu. Na Windows uwzględnij case, aliasy, UNC
   i junction poza zakresem; brak możliwości próby zapisz jako nieodebrany scenariusz.
5. Po dozwolonym działaniu wstrzymaj sesję, zapisz checkpoint i odśwież delegację.
   Samo potwierdzenie planu nie zastępuje ponownej decyzji panelowej. Potwierdź ten sam
   zakres i pozostałe limity, przygotuj wznowienie i potwierdź nowy plan. Licznik
   zużycia nie maleje, a czas pauzy zmniejsza pozostały czas delegacji.
6. W osobnych delegacjach sprawdź max_operations=1 i ważność 1 minuty. Po zużyciu
   jednej operacji kolejne działanie jest blokowane. Przy wygaśnięciu nie wywołuj
   narzędzi modelu przez minutę; ręczne odświeżenie panelu ma pokazać expired i pauzę.
7. Dla znanego, wcześniej zaufanego centralnego skilla lub MCP wykonaj osobny
   przebieg z wąską regułą skryptu/narzędzia, argumentów i timeoutu (MCP także discovery).
   Cofnij delegację podczas rozpoczętej operacji: nowe działania zablokowane,
   rozpoczęta może zakończyć się w swoim limicie z wynikiem i metainformacją pauzy.
   Sprawdź utratę trust lub zmianę hash/schema bez automatycznego fallbacku i retry.
   Bez odpowiedniego testowego wykonawcy pozostaw ten scenariusz otwarty.
8. Sprawdź konflikt dwóch kart i zmianę zasobów po podglądzie: stara decyzja nie
   nadpisuje nowszej zgody. Cofnięcie podczas resume_pending unieważnia stary plan.
   Przejście do ręcznych zgód wymaga osobnego wyboru w panelu i nowego wznowienia.
9. Zrestartuj aplikację: brak wybranej sesji, reguł i delegacji. Odzyskanie sesji
   nie przywraca zgody. Jeśli panel pokazuje nierozstrzygniętą operację, najpierw
   sprawdź jej rzeczywisty skutek; jawne rozstrzygnięcie nie wykonuje jej ponownie.

Raportuj commit i lokalne zmiany, wersje środowiska, nazwy narzędzi, statusy,
liczniki, rzeczywistą zawartość plików i scenariusze niewykonane. Nie wpisuj do
raportu sekretów, tokenów panelu ani tokenu handshake. Potwierdzenia krótko do
CURRENT, usterki wyłącznie do backlogu. Odbiór A2 nie obejmuje runnera A3b ani A4.

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
9. Zrestartuj MCP: nowe rewizje, cache_unchecked załadowany z SQLite, uzgodnienie w tle i docelowy stan ready/partial z jawnymi reasons. Przy braku cache indeks startuje pusty. Sesje nie są wznawiane automatycznie. Po cleanup failure sprawdź committed i plik przed retry.
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
