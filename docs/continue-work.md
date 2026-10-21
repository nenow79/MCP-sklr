---
type: Procedure
title: Kontynuacja pracy nad autonomią sklr na innym komputerze
description: Prompt startowy po przeniesieniu stanu prac S1 i odłączonych fundamentów A2b1/A2b2.
tags: [sklr, autonomy, handoff, windows]
---
# Prompt startowy

Po aktualizacji checkoutu i sprawdzeniu gałęzi/commita wklej tekst poniżej do nowej rozmowy. Ten dokument jest instrukcją wejścia; bieżące wyniki i zadania pozostają w [CURRENT](plan/CURRENT.md) i [backlogu](plan/backlog.md).

```text
Kontynuujemy pracę nad MCP-sklr. Celem jest beta autonomicznej pracy według
zaakceptowanego kontraktu A2, a potem odbiór na Windows z działającym MCP/panelem.

Zacznij od AGENTS.md, docs/plan/CURRENT.md i docs/plan/backlog.md.
Następnie przeczytaj docs/specs/autopilot-contract.md,
docs/specs/autopilot-rules.md i odpowiednie fragmenty docs/windows-okf.md.
Dokumentacja docs/ ma format OKF z metadanymi YAML.

S1 oraz odłączone fundamenty A2b1/A2b2 są zapisane i mają potwierdzone paczki
testów opisane w CURRENT. Nie implementuj ich ponownie. Autopilot nie jest
jeszcze podłączony do wykonywania zadań ani panelu; brak pełnej regresji HEAD
i pełnego odbioru runtime/Windows. Kontrakt A2 i format A2b2 są zaakceptowane.

Najbliższy krok: przejrzyj kod i przygotuj konkretny plan A2b3 — dziennik
metadanych/retencja, generacje zasobów i ich unieważnianie po zmianach,
koordynacja z sesją/handshake, budżetem, kolejką i bezpośrednimi narzędziami,
kontrola przed skutkiem oraz pauza/cofnięcie/wygaśnięcie bez obejścia przez trust.
Plan tej paczki nie został jeszcze zaakceptowany. Zgodnie z AGENTS przed zmianą
kodu przedstaw plan i uzyskaj akceptację. Nie pytaj ponownie o przyjęcie A2.

Później A2b4: panel i aktywacja opt-in; A2b5: regresja i odbiór Windows.
A3/A3b (w tym długie testy) i A4 (cykle poprawek) pozostają osobnymi kontraktami.
Nie myl 8 s wait_task, timeoutu zadania i terminu ważności delegacji.
Obecne wykonawce skills/MCP mają domyślne 60 s i maksimum 300 s.

Sprawdź git status, gałąź i ostatni commit przed pracą; zachowaj lokalne zmiany.
config.json nadal jest śledzony: nie nadpisuj go ustawieniami z innej maszyny.
Migracja konfiguracji W1, porządki W2 i przygotowanie właściwej bety są otwarte.
Nie przenoś venv/conda, baz, kluczy, danych wiedzy ani sekretów między systemami.
Pytest ani serwera API/uvicorn nie uruchamiaj bez wyraźnego polecenia; podawaj
komendy do wykonania w istniejącym środowisku. Nie instaluj i nie twórz środowisk
automatycznie. Prace i kolejne plany zapisuj tylko w backlogu, krótki stan
i potwierdzone wyniki w CURRENT, zakończone szczegóły w archiwum.
Nie przywracaj dawnych .vibecoder_context.md/.vibecoder_checkpoints.md do korzenia.
```

Przed aktualizacją na drugim komputerze sprawdź lokalny git status i zachowaj kopię config.json poza checkoutem. Przy lokalnych zmianach nie używaj reset --hard ani nadpisania configu. Po bezpiecznej aktualizacji commit punktu przekazania identyfikuj przez git log -1 --oneline i porównaj z hashem podanym przy publikacji. Szczegóły docelowego odbioru: [Windows](windows-okf.md).
