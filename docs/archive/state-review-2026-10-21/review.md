---
type: Review
title: Przegląd stanu przed dalszą implementacją autonomii
description: Ustalenia przeglądu kodu i dokumentacji oraz zakres pierwszych proponowanych poprawek.
tags: [sklr, review, autonomy, documentation, windows]
---
# Przegląd — 2026-10-21

Punkt odniesienia: HEAD 9176967. Odczyt lokalnego Git, kodu main/session_activation/session_tools/session_registration, kontrolera, adapterów, testów oraz kontraktów. Nie uruchamiano aplikacji, pytest ani testów Node; nie sprawdzano remote przez sieć. Brak wyników runtime z tej sesji.

## Ustalenia i sposób uporządkowania

| Rozbieżność | Rozstrzygnięcie |
|---|---|
| CURRENT zawiera opisy „brak integracji” i kolejne historyczne plany | Bieżący stan zastępuje historię etapów; dawne wyniki zachowane w sąsiednim CURRENT.md |
| Backlog nadal otwiera wdrożoną pauzę/checkpoint/wznowienie | Otwarte pozostają korekta komunikatu i odbiory; zakończone etapy w archiwum |
| A1/A1b mieszają fundament sesji z dalszą autonomią | Fundament wdrożony opt-in, A2–A4 wymagają kontraktu i implementacji |
| Dawny priorytet zewnętrznych MCP | Priorytet zmieniony zgodnie z bieżącą prośbą; otwarte M2f/M2g/M5a/M6 zachowane |
| OKF, indeks, wydajność i starsze skille | Zachowane jako potrzebne odbiory/dług; zewnętrzne czynności wymagają ponownej oceny, bez uznawania ich za zakończone |
| Mapa docs nazywa zewnętrzne MCP niewdrożonymi | Skorygowana na wdrożony kontrakt z częściowym odbiorem |
| Windows opisuje pusty indeks po restarcie i dawną bazę OKF | Opis podpisanego cache, sesji, bieżącej bazy kodu i brakujących prób runtime |
| config.json jest śledzony mimo zasady lokalnej konfiguracji | Dodany warunek przygotowania wydania i migracji z kopią konfiguracji Windows |
| Przypadkowy śledzony plik o nazwie zaczynającej się od kills) | Dodana pozycja przeglądu przed publikacją; nie usuwano pliku |

Nie usuwano otwartych odbiorów tylko dlatego, że nie dotyczą autonomii. Powtarzające się testy POSIX/jobów, pełnego HEAD i diagnostyki czasu mają wspólne pozycje z odwołaniami. Nie przyznano nowych uprawnień wykonawczych.

## Proponowana pierwsza paczka kodu — do akceptacji

1. S1: w server/session_tools.py wyodrębnić instrukcję dla session_not_selected kierującą do panelu. Nie zmieniać bramki, uprawnień checkpointu ani kodów odpowiedzi. Komunikat nie twierdzi, że save_session_checkpoint nie istnieje, i nie poleca zapisu bez wybranej sesji.
2. Uzupełnić istniejące testy session_tools/session_activation: checkpoint_available=true przy braku wyboru nie może sugerować zapisu; narzędzie robocze nadal nie jest wywoływane. Zachować przypadki pauzy z/bez checkpointu i błędu odczytu stanu. Nie zmieniać timeoutów.
3. Kontrola statyczna; polecenie dla użytkownika: python -m pytest tests/test_session_tools.py tests/test_session_activation.py tests/test_session_checkpoint_tools.py -q -rs --durations=20. Wynik wpisać dopiero po wykonaniu. Próby MCP/panelu pozostają na Windows.

Osobna paczka przygotowania wydania W1/W2 wymaga planu migracji istniejącego śledzonego configu (kopie poza checkoutem, przenośny przykład, ignorowanie danych lokalnych i zachowanie ustawień Windows). Nie łączyć jej z nadaniem uprawnień autopilotowi. Następnie kontrakt wykonawczy A2, który nie istnieje jeszcze w repo; obecny kontrakt sesji jawnie go wyłącza.

## Kontrola dokumentacji

Sprawdzono YAML/metadane OKF oraz lokalne odnośniki zmienianych dokumentów i nowych kopii archiwalnych; git diff --check bez błędów. Metadane standardu upstream/przykładów i dawnego archiwum nie były objęte konwersją. Pliki aplikacji, konfiguracja i dane pozostają bez zmian.
