---
type: Specification
title: "OKF: rewizje i sekcje"
tags: [sklr, okf, revisions, sections]
---
# OKF: rewizje i sekcje

Kontrakt lokalny sklr dla implementacji w `e6cee29`; odbiór i ograniczenia testów: [CURRENT](../../plan/CURRENT.md).
Mapa i pozostałe kontrakty: [obsługa OKF](../okf-support.md). Standard formatu: [OKF 0.2](../OKF/SPEC.md).

## 5. Rewizje i sekcje

### 5.1 Opaque revision

Publiczna revision: r1.<base64url-HMAC-SHA256>. Klucz co najmniej 32 losowe bajty, generowany przy starcie i przechowywany tylko w pamięci. HMAC obejmuje domenę/version, bundle id, przypięte kanoniczne root/bundle/file paths oraz SHA-256 prywatnych bajtów pliku, z jednoznacznym kodowaniem pól. Publicznie nie zwracamy surowego digestu, identity, sekretnego klucza ani nie zapisujemy rewizji w payload kolejki/telemetrii. Porównanie constant-time.

Ten sam plik i wiązanie w tym procesie dają ten sam token. Zmiana dowolnych bajtów (także YAML/EOL) lub ścieżki daje inną rewizję. Restart zmienia klucz, więc stara rewizja jest nieaktualna; agent musi wykonać get. Token nie nadaje dostępu i nie podpisuje prawdziwości wiedzy. Równość tokenów może ujawniać zmianę pliku; ochrona dotyczy offline zgadywania treści na podstawie jawnego hasha, nie wszystkich kanałów bocznych.

Po przygotowaniu zapisu transakcja dodatkowo kontroluje prywatny snapshot (hash/identity/mode) jak obecnie. HMAC nie zastępuje kontroli konfliktów ani uprawnień.

### 5.2 Outline i section_id

Podział po ATX heading (# do ######) i Setext heading, z pominięciem fenced i indented code blocks. C1-C2 wymagają testów nagłówków w kodzie, powtarzających się nazw i CRLF. v1 nie obiecuje pełnego CommonMark; nierozpoznany wariant pozostaje treścią, nie jest zgadywanym nagłówkiem.

Sekcje mają ids s1, s2, ... w kolejności rozpoznanych nagłówków. Treść przed pierwszym nagłówkiem to s0. Są to lokalne identyfikatory związane z revision, nie trwałe URL. Outline zawiera id, heading, level, parent_id, start_line, end_line, subtree_end_line.

section_mode=direct zwraca od heading do następnego heading (bez dzieci); section_mode=subtree do następnego heading o równym/wyższym poziomie (z dziećmi). Zakresy liczone od 1 względem oryginalnego pliku. Pobranie section_id wymaga expected_revision, aby zmiana kolejności nagłówków nie pobrała innej sekcji po cichu.
