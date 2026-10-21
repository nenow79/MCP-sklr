---
type: Specification
title: Autopilot A2 — format reguł i ocena operacji
description: Schemat pierwszej wersji reguł, deskryptory przygotowanych operacji oraz zasady odmowy i dopasowania.
tags: [sklr, autonomy, authorization, validation, planning]
status: accepted
---
# Format reguł i ocena operacji A2b2

## Status i odpowiedzialność

Szczegóły techniczne zaakceptowane wraz z planem A2b2; dalsze prace w [backlogu](../plan/backlog.md). Dokument uściśla [zaakceptowany kontrakt A2](autopilot-contract.md); nie nadaje nowych uprawnień. Moduł walidacji i dopasowania nie odpowiada za rejestrację MCP, panel, I/O, zegary ani dispatch. Stan jego integracji i zakres potwierdzeń opisuje wyłącznie [CURRENT](../plan/CURRENT.md).

Walidator odpowiada za poprawność zamkniętego formatu i niemutowalną reprezentację. Matcher odpowiada za zgodność przygotowanej operacji z regułami i dostarczonym aktualnym kontekstem dostępu. Budżet A2b1, uwierzytelnienie panelu, rzeczywiste rozwiązywanie ścieżek, kontrola snapshotów, dziennik i synchronizacja przed skutkiem są osobnymi obowiązkami integracji A2b3.

Deskryptor przygotowanej operacji pochodzi wyłącznie z adaptera serwera. JSON modelu nie jest źródłem pól writable/trusted/sensitive ani dowodem wywołania guarda. Pozytywna decyzja matchera nie stanowi samodzielnej zgody na wykonanie.

## 1. Dokument reguł i limity wejścia

Jedyny format zewnętrzny to obiekt:

~~~json
{
  "version": 1,
  "rules": [
    {
      "id": "project-code",
      "kind": "files",
      "tools": ["read_file", "edit_file"],
      "root": {
        "platform": "posix",
        "path": "/work/demo",
        "mode": "rw",
        "epoch": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      },
      "scope": {"kind": "tree", "path": "src"},
      "overwrite": false
    }
  ]
}
~~~

Przykład ilustruje składnię; nie jest konfiguracją tej instancji ani udzieloną zgodą. Na Windows platform=windows i kanoniczna bezwzględna ścieżka, np. C:/work/demo. Puste rules jest poprawne i niczego nie deleguje. Nie ma domyślnego root/scope/tools i nie ma klucza allow_all.

Identyfikatory reguł: 1–64 znaki ASCII [a-z0-9_-], unikalne w całym dokumencie. version musi być int równy 1, nie bool. Wszystkie obiekty mają zamknięte zestawy pól. Nieobsługiwane rodzaje, narzędzia, operatory i dodatkowe pola powodują odrzucenie całego dokumentu stałym kodem, bez echo danych.

Pułapy walidacji: 128 reguł, 128 KiB UTF-8 całego dokumentu, 16 poziomów danych, 10000 węzłów wraz z kluczami, 4096 znaków pojedynczego napisu i najwyżej 100 elementów pojedynczej listy argumentów skilla. Lista rules ma osobny pułap 128. Wszystkie liczby skończone, int najwyżej 64 bity ze znakiem. Walidacja struktury/rozmiaru poprzedza kosztowne kodowanie i rekurencję; cykle, obiekty innych klas i klucze niebędące napisami są odrzucane. To limity aplikacyjne po dekodowaniu, nie limit transportu HTTP.

Parser przyjmuje wyłącznie zwykłe dict/list i podstawowe typy JSON; wynik ma zamrożone, typowane modele. Modyfikacja oryginalnych list/dict po parsowaniu nie zmienia reguł. Repr modeli reguł/argumentów jest ukryty, a wyjątki zawierają wyłącznie kody.

## 2. Przypięcie zasobu

RootRef ma dokładnie platform/path/mode/epoch jak w przykładzie. epoch to 32 małe znaki hex nadane przez serwer w RAM dla generacji zasobu, nie przez model. Tożsamość nie zależy od aliasu. Zmiana ścieżki/trybu, utrata dostępu, usunięcie i ponowne dodanie unieważniają generację nawet po powrocie do starych wartości.

W A2b2 parser sprawdza wyłącznie składnię deklaracji; nie potwierdza, że taki root istnieje. Matcher wymaga równego bieżącego przypięcia z serwerowego kontekstu. A2b3 musi zapewnić nadawanie/unieważnianie epoch przy zmianach ConfigStore i ponowną kontrolę dostępu. Obecny RootsRegistry nie ma takiej generacji; do czasu tej integracji nie wolno użyć pozytywnego dopasowania do wykonywania operacji.

Zasoby innego rodzaju również otrzymują serwerowy epoch:

- BundleRef: bundle_id, source_digest (64 małe hex) i epoch. Odcisk obejmuje rzeczywiste źródło, profil/podkatalog i przypięty root/generację, jeśli dotyczy. Wyłączenie/usunięcie/podmiana źródła unieważnia dopasowanie.
- SkillRef: name, source=central, skill_hash, execution_digest i epoch. Oba odciski po 64 małe hex; execution_digest obejmuje ścieżkę/interpreter, konfigurację środowiska i deklarację network-env, bez wartości sekretów.
- ProfileRef: profile_id (32 małe hex), connection_digest (64 małe hex) i epoch. Włączenie, bieżący trust i limity nadal są sprawdzane niezależnie; utrata dostępu lub odwołanie trust unieważnia delegację, również po późniejszym przywróceniu.

Serwer tworzy odciski w procesie zatwierdzania, nie przejmuje ich z niezaufanej odpowiedzi MCP. Algorytm kanonicznego kodowania dokumentu reguł: JSON UTF-8, sort_keys=true, separators=(',', ':'), ensure_ascii=false, allow_nan=false, z zachowaniem kolejności list. Odcisk rules_digest dla ContractBinding to HMAC-SHA256 z przekazanym lokalnym kluczem, minimum 32 bajty. Matcher porównuje odcisk przygotowanej polityki i powiązanie kontraktu; API nie zwraca klucza ani tekstu reguł. A2b2 nie czyta/generuje klucza na dysku.

## 3. Ścieżki i zakresy

Zakres ma dokładnie kind=file lub tree oraz path jako względną ścieżkę z separatorami /. Dla tree dozwolone "." oznacza świadomie wybrany cały konkretny root/bundle; brak path nie jest takim wyborem. Dla file "." jest błędem.

Niedozwolone: pusty segment, . lub .. wewnątrz ścieżki, ścieżka absolutna w zakresie, backslash, UNC/urządzenia, dwukropek/NTFS ADS, globy, kontrolne/bidi, końcowe kropki/spacje i nazwy urządzeń Windows. Reguły składni Windows zachowane także na Linuksie. Nazwy nie są arbitralnie stripowane ani naprawiane.

Bezwzględna ścieżka roota musi być kanoniczna leksykalnie: POSIX /... albo Windows z pełną literą dysku, bez drive-relative i UNC. Porównanie odbywa się po segmentach w ramach tej samej platformy. POSIX rozróżnia case; Windows normalizuje literę dysku, ale nazwy katalogów/plików w tej czystej warstwie muszą zgadzać się dokładnie. Dotyczy to także Unicode. Adapter powinien dostarczać kanoniczne nazwy z systemu plików; matcher nie zakłada globalnie, że każdy katalog Windows jest case-insensitive. Niejednoznaczność oznacza odmowę, bez rozszerzenia zakresu przez lower/casefold. Nie wolno traktować src2 jako potomka src ani dysku D: jako C:.

To nadal porównanie deklarowanych, wcześniej rozwiązanych celów. PurePath nie sprawdza junctiona, symlinka, istniejących rodziców, nazw 8.3 ani TOCTOU. Adapter A2b3 musi użyć istniejącego path_guard/FileTransaction i dostarczyć kanoniczny cel, efektywnego ownera i wszystkie powiązania; matcher nie importuje ani nie zastępuje tych mechanizmów.

Żądanie rekurencyjnego search/listowania lub skanu jest dopuszczone tylko, gdy cały żądany zakres mieści się w regule tree. Reguła pojedynczego pliku nie uprawnia do skanowania jego katalogu. Wyjście części odpowiedzi poza zakres nie jest naprawiane przez ciche odfiltrowanie po odczycie.

## 4. Rodzaje reguł

Każda reguła ma id/kind/tools oraz pola z właściwego wiersza. Zestaw tools niepusty, bez duplikatów. Narzędzie musi należeć do właściwego rodzaju; nie można uzyskać secret_reveal przez wpisanie go do reguły files.

| kind | Wymagane pola poza id/kind/tools | Dopuszczony zakres |
|---|---|---|
| files | root, scope, overwrite (bool) | list_directory, read_file, read_files_batch, search_code, edit_file, create_file, create_tree, manage_folder wyłącznie create |
| knowledge | bundle, scope | knowledge_search, knowledge_get; scope to ścieżka dokumentu z .md lub poddrzewo bundle |
| skill_read | skill, scope | activate_skill, read_skill_resource; jawny centralny skill i względna ścieżka zasobu/SKILL.md |
| skill_run | skill, script, args (dokładna lista napisów), max_timeout_s | run_skill_script; konkretny centralny skrypt, istniejący trust i brak wymaganej osobnej zgody |
| mcp_discovery | profile, max_timeout_s | list_external_mcp_tools |
| mcp_call | profile, tool_name, schema_digest, arguments, max_timeout_s | call_external_mcp_tool; konkretny zaufany tool i schemat |
| catalog | resources (niepusta lista przypięć określonego typu) | list_roots, knowledge_bundles, list_skills, list_external_mcp — każdy tool w osobnej regule, wyłącznie wskazane zasoby |

script jest względną ścieżką file i musi leżeć pod scripts/. schema_digest to 64 małe hex. max_timeout_s to dodatni int; dla obecnych skills/MCP maks. 300. Limit jest przypięty do reguły wykonawcy, nie do BudgetLimits. Przyszły rodzaj test_run i jego dłuższe limity wymaga kontraktu A3b; nie przechodzi przez nieznane kind.

overwrite=false jest obowiązkowe także dla reguł tylko odczytu; create_file z overwrite=true wymaga osobnej reguły z overwrite=true. Zwykłe edit_file może zmieniać istniejący plik bez tego prawa — overwrite dotyczy pełnego nadpisania przez create_file, nie zakazuje edycji. create_tree nadal nie nadpisuje istniejących plików.

Operacje katalogowe zwracają metadane wielu zasobów. Adapter przed dopuszczeniem musi ustalić cały ich zakres; matcher wymaga objęcia wszystkich zwracanych zasobów regułami catalog. Brak możliwości bezpiecznego ograniczenia katalogu oznacza odmowę, nie odczyt z pominięciem kontraktu.

prepare_project obejmuje AGENTS i potencjalne skany wielu źródeł. A2b2 v1 kwalifikuje je jako manual_required, również przy create_agents=false, dopóki nie ma osobnego adaptera rozpisującego kompletny plan odczytu. To konserwatywne ograniczenie obsługiwanej paczki, nie nadanie nowego uprawnienia. knowledge_audit i knowledge_convert oraz knowledge_save pozostają poza automatycznym wykonaniem v1.

## 5. Selektory argumentów MCP

Selektor ma jedną z poniższych zamkniętych postaci; brak dowolnego JSON Schema, $ref, regex, eval, dodatkowych properties lub callbacku:

| type | Pola poza type | Dopasowanie |
|---|---|---|
| literal | value | Dokładny typ i wartość JSON, także obiekt/lista; kolejność kluczy obiektu nieistotna, kolejność list istotna |
| enum | values (1–32 różne wartości JSON) | Jedna dokładna wartość z zachowaniem typów |
| string | max_length (1–4096) | Napis ograniczony długością, bez automatycznej konwersji |
| integer | minimum, maximum (int, zakres 64-bit) | Int w domkniętym zakresie, bool odrzucony |
| number | minimum, maximum (skończone liczby) | Int/float w zakresie, bool odrzucony |
| boolean | brak | Wyłącznie bool |
| object | properties (mapa nazwa → selektor), required (lista nazw z properties) | Zamknięty obiekt; brak pola różni się od null i pustego napisu |
| url | schemes, hosts, ports, path | Jawna lista http/https, ASCII hostów i portów oraz zakres path |

Null obsługuje literal z value=null. Array w v1 tylko jako literal/enum, bez reguły „dowolne elementy”. Głębokość zagnieżdżenia selektorów maks. 8, suma danych nadal podlega limitom dokumentu. Równość literal/enum rozróżnia bool/int/float, więc true nie jest 1, a 1 nie jest 1.0. number jawnie dopuszcza int i float w zakresie.

URL jest sprawdzany przez parser: bez userinfo, query, fragmentu (także pustego separatora ?/#), backslash, procentowych sekwencji w ścieżce i segmentów `.` lub `..`. Dwa kolejne ukośniki wewnątrz ścieżki są odrzucane zamiast normalizowane. Puste path URL odpowiada /. schemes/hosts/ports są niepuste, bez wildcardów; host DNS jest ASCII lowercase, adres IP zapisany kanonicznie, bez rozwiązywania DNS. Port domyślny rozwijany zgodnie ze schematem. Host jest porównywany w całości, a path po segmentach; example.test.evil nie jest example.test. Zakres path ma kind=exact lub tree i absolutną ścieżkę URL. Drzewo /docs obejmuje /docs i /docs/a, nie /docs2.

V1 celowo odmawia złożonych URL z query/encodingiem wymagających osobnej polityki. Nie deklaruje na tej podstawie ochrony SSRF; istniejące guardy/TLS/przypinanie IP pozostają wymagane. Pole URL przekazane jako zwykły string nie nabywa ograniczeń hosta przez samą nazwę parametru — użytkownik wybiera selektor url w panelu.

Polityka i argumenty operacji pozostają poufnymi danymi RAM. Parser nie może dowieść, że literal nie jest sekretem. Przyszły panel stosuje obecną politykę wykrywania sekretów i odmowy; brak gwarancji heurystyki nie uprawnia do logowania reguł.

## 6. Deskryptor operacji i kontekst serwera

Matcher przyjmuje typowany OperationDescriptor i AccessSnapshot, tworzone wyłącznie przez przyszłe adaptery. Nie przyjmuje surowego żądania MCP jako gotowego dowodu dostępu. Deskryptory są niemutowalne i nie zawierają treści plików, diffów ani wyników; argumenty wykonawców pozostają prywatne z repr=false.

OperationDescriptor obejmuje: tool, dokładny action, wszystkie cele i powiązania, informację pełny odczyt/rekurencja, overwrite tam gdzie dotyczy, parametry wykonawcy/timeout oraz przypięcie ContractBinding. Operacja bez celów i bez jawnej klasyfikacji nie oznacza dozwolonego no-op; jest odrzucana. No-op potwierdza osobno adapter przed rezerwacją budżetu.

AccessSnapshot obejmuje aktualne przypięcia zasobów, dostępność, efektywny tryb ownera ro/rw, wyniki istniejących kontroli sensitive i trust oraz zgodność hasha/schema/interpretera/konfiguracji. Wszystkie wartości są odczytywane przez serwer, nie pochodzą z pól modelu. Brak wymaganej pozycji lub pozytywnego rozstrzygnięcia to odmowa. Wrażliwość nie jest jednym wspólnym booleanem dla każdego rodzaju operacji.

Reguła files wymaga pasującego RootRef i kompletnego zestawu canonical target paths. Zagnieżdżony ro owner uniemożliwia zapis mimo rw wybranego roota. Wszystkie aliasy/PathBinding tego samego pliku są sprawdzone, a dodatkowe, nieobjęte kontraktem powiązanie powoduje odmowę zamiast wybrania wygodnego aliasu.

Dla create_tree ocena obejmuje pliki oraz wszystkie katalogi, które executor zamierza utworzyć. Potrzebny brakujący rodzic poza zakresem blokuje operację. Operacja zbiorcza może użyć kilku reguł tej samej kategorii, ale każdy cel musi w całości spełniać przynajmniej jedną regułę wraz z jej warunkami; nie łączy się fragmentów dwóch reguł w nowe uprawnienie.

Wiedza wymaga aktualnego BundleRef i dostępu do źródła, a knowledge_get dokumentu/sekcji musi mieścić się w zakresie dokumentu. Search obejmujące scope=all wymaga kompletnego planu wszystkich skanowanych źródeł; nie rozstrzyga się po samym bundle_id ostatniego wyniku.

Skill_run wymaga aktualnego źródła central, trusted=true, approval_required=false, zgodnych SkillRef/skryptu/argumentów i timeoutu. MCP call wymaga enabled=true, trusted=true, zgodnego profilu/nazwy/schema/argumentów i limitów. MCP discovery nie wymaga trust narzędzia, ale wymaga enabled i jawnej reguły discovery. Te stany są kontrolowane ponownie przez wykonawcę przed skutkiem; A2b2 nie zmienia zaufania.

Kontrola pełnego inputSchema i limiter 30/min pozostają w adapterze MCP. Matcher nie wykonuje discovery, nie importuje skilla i nie rozwiązuje DNS ani środowiska. Wynik pozytywny to migawka; A2b3 musi powtórzyć ocenę przy aktualnym dostępie i synchronizować dispatch.

## 7. Wyłączenia i decyzja

Zawsze brak delegacji: secret_reveal, request_root, zmiany profili/trust/konfiguracji, rename/usuwanie, kod i zabezpieczenia działającego sklr, pliki instrukcji/kontraktu, .git/sekrety, produkcja/publikacja/płatności oraz nieznane narzędzia/akcje. Serwerowy kontekst sensitive ma pierwszeństwo przed dopasowaniem reguły files. Klasyfikator musi rozpoznawać także nowy pakiet autopilot/, klucze/cache/bazy sklr i chronione instrukcje, nawet jeśli starszy SensitivePolicy ich jeszcze nie wymienia.

Nie wolno uznać external_mcp za niedopuszczalne wyłącznie dlatego, że jego wynik używa sensitive=true; tam decyzja opiera się na profilu/trust/narzędziu/argumentach i jawnej klasyfikacji skutku. Nieznana semantyka obcego narzędzia nie jest potwierdzeniem read-only; serwer egzekwuje tylko zatwierdzone techniczne ograniczenia, a świadomy wybór wykonawcy pozostaje po stronie użytkownika.

Decyzja ma tylko allowed (bool), code z zamkniętej listy oraz posortowane matched_rule_ids. Bez argumentów, ścieżek, surowego wyjątku i tekstu reguł. Przy odmowie matched_rule_ids jest puste, także gdy część batch pasowała. Kody obejmują: allowed, invalid_operation, binding_mismatch, policy_mismatch, resource_changed, access_denied, sensitive_operation, manual_required, tool_not_allowed, scope_mismatch, arguments_mismatch, timeout_exceeded, trust_required.

Decyzja nie rezerwuje/zużywa budżetu, nie anuluje zadania, nie pauzuje sesji i nie rozpoczyna efektu. A2b3 zamieni odmowę na sterowanie sesją zgodne z kontraktem. Funkcje dopasowania są deterministyczne, bez globalnych rejestrów i mutacji wejścia.

Narzędzia sterujące istniejącą sesją/checkpointem i odczyt wyniku już rozpoczętego zadania pozostają w obecnej ścieżce sterującej. Nie są dopuszczane przez ogólny wildcard matchera i nie stanowią nowej delegacji. Ich rozdział od narzędzi roboczych pozostaje jawny w integracji.

## 8. Kryteria sprawdzenia paczki

Wymagane testy logiki: zamknięte schematy/pułapy/cykle/typy, zamrożenie wejścia i brak treści w repr/błędach; HMAC i odmienna polityka; prefiksy ścieżek/dyski/case/UNC/ADS oraz platformy; tree/file i pełny scan; wszystkie cele batch i rodzice create_tree; dodatkowy owner ro; sensitive i nieznane rodzaje; każda składowa przypięcia/epoch; trust/hash/schema/interpreter; timeout wykonawcy; typy literal/enum, required/null/extra fields, granice liczb/długości i URL z hostem podszywającym się pod dozwolony.

Testy używają wyłącznie modeli i kontrolowanych serwerowych snapshotów. Nie dowodzą rzeczywistego junctiona, kompletności adaptera, auth panelu ani atomowości przed skutkiem. Dotychczasowe guardy i snapshoty muszą przejść osobne testy integracji A2b3/odbiór Windows. Pytest nie uruchamia się bez wyraźnego polecenia.
