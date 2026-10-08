---
name: hello-skill
description: Demonstracyjny skill do testów MCP. Użyj, gdy użytkownik prosi o test skills, "hello skill", weryfikację aktywacji skilla albo test uruchamiania skryptów i limitów czasu.
metadata:
  author: vibecoder
  version: "1.0"
---

# Hello skill

Skill demonstracyjny. Służy do sprawdzenia, czy aktywacja, odczyt zasobów i uruchamianie skryptów działają.

## Instrukcje
1. Przeczytaj `references/guide.md` przez `read_skill_resource`.
2. Powiedz użytkownikowi, co zawiera ten przewodnik.
3. Na prośbę o test skryptów uruchom `scripts/hello.py` przez `run_skill_script`
   (`script="scripts/hello.py"`, `args=["--name", "Jan"]`) i pokaż wynik.
4. Na prośbę o test limitów czasu uruchom `scripts/sleep.py` z argumentem `--seconds N`.
   Ustaw `timeout` większy niż N, żeby skrypt zdążył się zakończyć.

## Dostępne skrypty
- **`scripts/hello.py`** - wypisuje JSON z pozdrowieniem (`--name`).
- **`scripts/sleep.py`** - czeka N sekund (`--seconds`), służy do pomiaru limitów czasu.
