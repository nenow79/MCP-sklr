# OKF: kontrolowany zapis

Kontrakt lokalny sklr dla implementacji w `e6cee29`; odbiór i ograniczenia testów: [CURRENT](../../plan/CURRENT.md).
Mapa i pozostałe kontrakty: [obsługa OKF](../okf-support.md). Standard formatu: [OKF 0.2](../OKF/SPEC.md).

### 6.4 knowledge_save

API precyzuje ogólne metadata z planu jako tekst YAML, aby zachować rozszerzenia i uniknąć udawania bezstratnej serializacji ograniczonego modelu:

```text
knowledge_save(
  bundle_id: str,
  concept_id: str,
  mode: str,
  body: str,
  metadata_yaml: str | null = null,
  expected_revision: str | null = null,
  description: str = ""
)
```

- mode=create/update, bez upsert. body jest Markdown bez frontmattera, do złożenia z YAML. metadata_yaml to sam YAML mapping bez linii delimiterów. create wymaga metadata_yaml i braku expected_revision; update wymaga expected_revision.
- update z metadata_yaml=null zachowuje istniejący frontmatter verbatim. Podany metadata_yaml jest jawną pełną propozycją bloku, NIE patch; diff pokazuje wynik. C0 nie obiecuje zachowania komentarzy w bloku jawnie zastąpionym przez użytkownika/model. Konwersja P2 ma silniejszy kontrakt wąskiej zmiany.
- Przy pełnej propozycji update sprawdzamy rekurencyjnie obecność istniejących kluczy mapping, również nieznanych. Ciche usunięcie klucza jest błędem METADATA_KEY_LOSS. Elementy sources/verified oraz listy o różnym kształcie muszą być ocenione w diffie; nie ma uniwersalnego stabilnego klucza list OKF. W C1-C3 nie dostarczamy API usuwania metadanych. Jawne usunięcie wymaga osobnej zatwierdzonej edycji pliku, nie obejścia walidacji przez save.
- Aktualizacja masked dokumentu: nie dopuszczamy ślepego zastąpienia body/metadanych placeholderami z get. Jeśli prywatny snapshot wykrywa maskowane wartości w zastępowanym zakresie, save zwraca MASKED_UPDATE_UNSUPPORTED. Update body w dokumencie z zamaskowanym body jest zablokowany; jeśli maskowanie dotyczy tylko zachowanego frontmattera (metadata_yaml=null), aktualizacja niemaskowanego body może działać. Jawna naprawa takich dokumentów to osobna operacja, nie automatyczne ujawnienie.
- Create oraz cały wynik update muszą być poprawnym profilem OKF i mieścić się w max_document_bytes. Istniejący CP1250 wymaga osobnej jawnej decyzji o transkodowaniu. BOM/EOL UTF-8 istniejącego pliku zachowujemy tam, gdzie bezpieczne; mixed EOL są odrzucane w v1. Body nie jest skracane do limitu odpowiedzi.
- Katalog rodzica musi istnieć. Brak auto-mkdir/rename, zapisu index/log i auto-generated indeksów/historii.
- Semantic warnings nie są automatyczną odmową, ale wrażliwe zmiany trust/lifecycle muszą być widoczne w podglądzie. Nie dopisujemy generated, verified, sources ani status poza propozycją użytkownika/modelu. Autorstwo i weryfikacja mają być prawdziwe; kliknięcie Allow nie oznacza human-reviewed.
- No-op: success=true, data.changed=false, brak task. Create collision i brak pliku update to błędy przed submit.

Przepływ:
1. Sprawdzenie enabled/root/subpath/rw, zwykłych plików i brak links/junctions.
2. Snapshot i expected_revision; pełna walidacja/limity i prywatny plan.
3. Dedykowany maskowany preview OKF. Dotychczasowy PreparedChanges.preview bez transformacji OKF nie jest wystarczający dla .md. Prywatne PreparedChanges nie są serializowane.
4. Task kind=knowledge_write, needs_approval=true, offer_trust=false; natychmiastowy task_id i status, brak blokującego wait. Payload ma bezpieczne identyfikatory, description, maskowany diff, warnings/truncated, bez body/metadata_yaml/tokenów/hashy.
5. Po decyzji oraz przed wykonaniem sprawdzenie wiązania bundle, enabled, uprawnień i snapshotu pod WRITE_LOCK oraz wspólnym ConfigStore.lock. Użycie FileTransaction nie zwalnia adaptera z kontroli enabled.
6. Sukces zapisu -> usunięcie starego publicznego wpisu i próba indeksowania nowej wersji. Błąd indeksu nie oznacza cofnięcia zapisu i nie może skłaniać do ponowienia create/update bez get.

Wynik początkowy: task_id, status, kind, bundle_id, concept_id, sensitive, truncated, hint o decyzji użytkownika. Get_task po sukcesie ma bezpieczne stdout z committed=true i index_state. Nie dodajemy surowych metadanych/revision do trwałego stdout.

FileTransaction zwraca committed/rollback_complete/cleanup_complete; adapter zachowuje ich rozróżnienie w bezpiecznym podsumowaniu. Cleanup failure może oznaczać committed=true i task failed: instrukcja sprawdzenia pliku, nie automatyczne powtórzenie. Restart unieważnia plan w pamięci; kolejka oznacza interrupted zgodnie z istniejącą logiką. Po :ping najpierw list_tasks, nie ponowienie zapisu w ciemno.


Writer zachowuje niezmienione body z sekretami przy edycji niemaskowanych metadanych; dokładny no-op jest dopuszczony także dla dokumentu z sekretami. Listy nie mają zgadywanej tożsamości: zmiany wymagają oceny podglądu i ostrzeżenia metadata_lists_changed. Nieoczekiwany wyjątek samej transakcji ma committed=null; nie wolno interpretować go jako potwierdzonego braku zapisu. Guard sprawdza dostęp przy verify, na początku execute i przed publikacją.
