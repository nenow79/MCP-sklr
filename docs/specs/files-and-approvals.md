---
type: Specification
title: Pliki, roots, zatwierdzenia i sekrety
description: Kontrakt narzędzi plikowych, kontroli dostępu, kolejki zgód i ochrony sekretów.
tags: [sklr, files, approvals, security]
---
# Pliki, roots, zatwierdzenia i sekrety

Kontrakt istniejących narzędzi bazowych. Historia planowania i szczegółowe wyniki pozostają w [fazie 5](../archive/pre-okf-2026-10-15/plan/phase-5.md); odczyt historii nie jest wymagany do zwykłej pracy. Ochronę specyficzną dla wiedzy opisują [kontrakty OKF](okf-support.md).

## Roots i ścieżki

Roots wyznaczają dostęp narzędzi plikowych. Brak root oznacza domyślny alias; zapis wymaga rw i respektuje bardziej restrykcyjny root ro. ConfigStore synchronizuje zmiany konfiguracji. Panel Foldery zarządza rootami; request_root przygotowuje sensitive task do decyzji, bez Ufaj. Konfiguracja bundle nie przyznaje praw poza rootami.

Guard musi kontrolować wyjście przez `..`, inne dyski, UNC i prefiksy urządzeń, symlinki/junctiony i przodków, case, nazwy 8.3, końcowe kropki/spacje, strumienie NTFS i nazwy zarezerwowane Windows. Sprawdzenie przy przygotowaniu nie zastępuje ponownej walidacji po zgodzie. Wewnętrzne ścieżki względem przypiętego roota pozwalają rozwijać projekt na Linuksie bez osłabiania reguł Windows.

Roots nie sandboxują skryptów ani procesów użytkownika. Heurystyki i zgoda w panelu nie usuwają wszystkich zewnętrznych TOCTOU.

## Odczyt i zapis

list_roots/list_directory/read_file/read_files_batch/search_code ograniczają odpowiedź budżetem. Search może być częściowy i pomijać pliki binarne/duże i katalogi techniczne; nie jest dowodem kompletnego przeglądu dysku.

edit_file przygotowuje listę dokładnych zmian i wspólny maskowany diff; niejednoznaczne old_text jest błędem. create_file z overwrite wymaga jawnej propozycji; manage_folder obsługuje create/rename. create_tree tworzy strukturę jedną zgodą, bez nadpisywania istniejących plików; limity i ograniczenia: [README](../../README.md).

Dokumentację obejmuje [polityka zapisu OKF](okf-integration/write-policy.md): domyślna zgodność zachowuje obsługę starego Markdown, ale zabrania uszkodzenia już poprawnego OKF. Jawny require_okf wymaga formatu w wybranych podkatalogach również przy zwykłym create/edit i create_tree; rename katalogów przecinających chroniony zakres jest blokowany. Kontrola jest powtarzana po zgodzie i przed wykonaniem.

Zapis ma natychmiastowy task_id, kontrolę snapshotu po decyzji i staging przez FileTransaction. Zachowuje BOM/EOL tam, gdzie kontrakt to przewiduje. Rollback jest best-effort, nie pełną atomowością wieloplikową przy awarii procesu/systemu. committed, rollback_complete i cleanup_complete trzeba rozróżniać; zgoda Allow nie dowodzi sukcesu operacji.

## Panel i kolejka

Wszystkie zatwierdzenia przechodzą przez panel na localhost; tkinter jest wycofany. Bez uruchomionego panelu serwer nie startuje. Token jest jednorazowy, tylko w pamięci procesu; sesja używa ciasteczka HttpOnly/SameSite, kontroli Host/Origin i braku CORS. Treść modelu i plików musi być renderowana jako tekst, nie HTML. Opis modelu jest drugorzędny wobec faktycznej operacji/diffu. Tylko panel rozstrzyga zgodę; nie ma MCP do zatwierdzania.

Zadania awaiting_approval podlegają konfigurowalnemu wygaśnięciu (domyślnie 60 min). Zapisy, root_change i secret_reveal nie oferują Ufaj. Polityka ręczna agenta: przy awaiting_approval najwyżej jedno wait_task(seconds=6) na osobny task_id. Każde kolejne zadanie w sekwencji ma własne oczekiwanie do 6 s. Zadanie obejmujące wiele plików ma tylko jedno oczekiwanie, nie osobne na każdy plik. Nie ponawiać oczekiwania na tę samą zgodę ani tworzyć nowego zadania dla resetu limitu. Jeśli po oczekiwaniu nadal brak decyzji: podsumowanie i STOP do wiadomości użytkownika, bez kolejnych wait/anulowania/resubmit. queued/running po decyzji to oczekiwanie na wykonanie; śledzić ten sam task_id, zależny krok dopiero po potwierdzonym sukcesie i w aktualnym zakresie zgody. Pauza/utrata zgody blokuje nowe kroki. Koniec oczekiwania nie anuluje zadania; limit 8 s pojedynczego wait_task, timeouty zadań, wygaśnięcie zgód i TTL nie zmieniają się. Jedno oczekiwanie na zgodę dla task_id jest instrukcją modelu, nie serwerowym licznikiem wywołań. Skończony budżet autopilota wymaga osobnego uzgodnienia. Narzędzia zgłoszenia nadal zwracają task_id bez dodatkowego oczekiwania na człowieka; wait_task nie pobiera wyników poufnych, które są dostępne tylko przez get_task. Po błędzie `:ping` najpierw list_tasks, aby nie utworzyć duplikatu. Restart oznacza interrupted dla nieterminalnych zadań i nie odtwarza prywatnych planów w pamięci.

Zapisy do kodu serwera, panelu, konfiguracji, bazy i .git oraz plików sekretów są sensitive, z ręczną zgodą. Zakres i ryzyka komend są odrębną [niewdrożoną propozycją](command-execution-proposal.md), nie ochroną istniejącego execute_command.

## Sekrety

Odczyty plików sekretów (m.in. .env poza .env.example, pip.ini/pip.conf, .npmrc i klucze) maskują rozpoznane wartości i poświadczenia URL, także w search. Pełne ujawnienie wymaga osobnego secret_reveal, sensitive i bez Ufaj. Wynik ujawnienia jest ulotny, tylko w pamięci, poza SQLite/logami/telemetrią. Nie gwarantuje to zeroizacji pamięci ani retencji po stronie klienta/czatu. Maskowanie jest heurystyczne, nie pełnym DLP.

Zwykły .md potrzebuje odrębnej [transformacji publicznego widoku OKF](okf-integration/format-and-security.md); samo mask_text dla nazw plików sekretów nie wystarcza do indeksowania wiedzy. Zaufanie do skills ma własny [kontrakt i ograniczenia](skills-support.md).
