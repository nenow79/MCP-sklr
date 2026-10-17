---
type: Specification
title: Model zagrożeń i środowisko firmowe — propozycja
description: Model zagrożeń i wymagania ochrony dla niewdrożonej propozycji komend.
tags: [sklr, commands, security, proposal]
---
# Model zagrożeń i środowisko firmowe — propozycja

Niewdrożone w `e6cee29`. [Status, korekty i mapa propozycji](command-execution-proposal.md) są częścią kontraktu. Wzmianki o ADR 0003 oznaczają planowany dokument. Testy opisują wymagania, nie wyniki.

### 4.3 Ryzyko żądań HTTP do localhosta (blokada portu panelu 8102)
Założenie z ADR 0002: model ma powłokę, więc może wysyłać żądania HTTP do localhosta; ochrona dotyczy pomyłek i prompt injection, nie celowo złośliwego modelu.

| # | Wektor | Środek | Typ ochrony |
|---|---|---|---|
| T1 | Bezpośrednie żądania do `http://127.0.0.1:8102/api/...` (`curl`, `Invoke-WebRequest`, `python`, skrypt zapisany `create_file`) | Panel wymaga ciasteczka sesji, `Origin` i `Host`; model nie zna tokenu (brak w env, argv, configu, bazie, plikach, odpowiedziach). Test: token nie występuje w `clean_env()`, `config.json`, `tasks.db`, odpowiedziach narzędzi | prawdziwa (kryptograficzna) |
| T2 | Wyciek tokenu z historii przeglądarki (`#token=...` trafia do profilu, link w konsoli) | Token jednorazowy (P1); profile przeglądarek poza dozwolonymi rootami; policy blokuje argumenty wskazujące te ścieżki | prawdziwa (jednorazowość), heurystyka (ścieżki) |
| T3 | Zajęcie portu 8102 przez inny proces (podszycie pod panel) | Link z tokenem tylko po udanym `PanelServer.start()` (jest); przy zajętym porcie serwer kończy start bez linku (tkinter wycofany); policy odrzuca port 8102 jako argument serwerów i narzędzi sieciowych; głośna diagnostyka po porażce bind | częściowo prawdziwa + heurystyka |
| T4 | Zbudowanie adresu lub portu w kodzie (`81*100+2`, base64, osobny skrypt) | Nie do zablokowania ogólnie. Środek: użytkownik widzi pełną komendę, a dla `python skrypt.py` w panelu podgląd treści skryptu z roota; guard nie jest granicą bezpieczeństwa (ADR 0003) | rezydualne, jawnie zaakceptowane |
| T5 | DNS rebinding i inna nazwa hosta | `Host` i `Origin` tylko `127.0.0.1:8102`/`localhost:8102` (jest) | prawdziwa |
| T6 | Żądania do portu MCP 8101 (szeroki CORS, bind `0.0.0.0`) z pominięciem harnessa | Na 8101 brak endpointów zatwierdzania; wszystkie narzędzia zmieniające stan tworzą zadanie `awaiting_approval`. Test regresyjny: żadne narzędzie nie ma parametru typu "approve/auto" | prawdziwa (architektura) |
| T7 | Zapis modyfikujący sam serwer (root = to repo): `panel/`, `command_policy.py`, `config.json`, `main.py` | Zadania `sensitive` (2.4): ręczne zatwierdzenie, bez "Ufaj" | prawdziwa |
| T8 | Podszycie opisu zadania ("bezpieczne, kliknij zezwól") | Panel pokazuje treść wykonywaną (argv, diff) jako główną, opis modelu jako drugorzędny z etykietą; brak HTML | prawdziwa |
| T9 | Zmiana roots przez model (nadanie sobie dostępu do dowolnego folderu) | Brak narzędzia modyfikującego roots; `request_root` tworzy zadanie do zatwierdzenia; endpointy `/api/roots` tylko z sesją panelu | prawdziwa |
| T10 | Subprocess dziedziczy sekrety z serwera | `clean_env()` (jest), `close_fds` (domyślnie) | prawdziwa |
| T11 | Zapis do roota `ro` przez `execute_command` (ścieżka absolutna) | Tylko zatwierdzenie komendy; `cwd` ograniczony do roots `rw`; opisane w ADR 0003 | rezydualne |

Heurystyczna detekcja `localhost`, `127.0.0.1`, `::1`, `0.0.0.0` w komendach nie blokuje (np. testy API na porcie 8000), tylko blokuje port panelu/MCP i dodaje plakietkę "łączy się z localhostem" w panelu. Wykrywane narzędzia: `curl`, `wget`, `iwr`, `irm`, `Invoke-WebRequest`, `Invoke-RestMethod`, `nc`, `ncat`, `telnet`, `ssh -L`.

### 4.4 Komendy firmowe: proxy i zmienne środowiskowe
Ustalone: LLM bywa proszony o komendy cmd dotyczące firmowego proxy i zmiennych środowiskowych. To zmiany o wysokiej wadze: `setx`, `npm config set proxy`, `git config --global http.proxy`, `pip config`, `netsh winhttp` zmieniają zachowanie sieci dla całego użytkownika, a podmiana proxy pozwala przechwytywać ruch (w tym tokeny).
- W `execute_command` są zablokowane (4.2). Model zwraca użytkownikowi gotowe komendy do własnego terminala (zasada jak dla venv).
- Odczyt: skill `env-info` (Python), wzorowany na projekcie referencyjnym CSM: zmienne `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, prefiks `DEV_*`, `PIP_CONFIG_FILE`, `NPM_CONFIG_FILE`, `DOCKER_REGISTRY`; `npm config get registry|proxy|userconfig`, `git config --get http.proxy`, `pip config list` (z maskowaniem `index-url` z tokenem). Zawartość `pip.ini` i `.npmrc` nie jest czytana: tylko ścieżka, istnienie pliku i nazwy kluczy. Nazwy pasujące do `TOKEN|SECRET|PASSWORD|KEY|PROXY_PASS` i adresy z poświadczeniami są maskowane.
- Zmiany proxy i zmiennych środowiskowych nie wchodzą w zakres skilla: model zwraca użytkownikowi komendy do własnego terminala. Wrócimy do tematu w fazie 8, jeśli pojawi się realna potrzeba (wtedy: jawna lista dozwolonych kluczy, osobne zadanie z ostrzeżeniem "zmiana trwała, dotyczy całego użytkownika", zawsze ręczne zatwierdzenie).
- Wzorzec z CSM: proxy żyje w zmiennych `DEV_*` w `.env` (bez systemowego `HTTP_PROXY`), a compose mapuje je na build args. Skill `env-info` rozpoznaje oba warianty.
