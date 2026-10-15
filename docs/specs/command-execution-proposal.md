# Wykonywanie komend — propozycja, niewdrożone

W `e6cee29` nie ma narzędzia execute_command ani modułu command_policy. Poniżej zachowano wymagania projektowe fazy 5c, w tym model zagrożeń; nie potwierdzają implementacji ani testów. Rejestracja kodu, kontrakt i ADR 0003 wymagają osobnego planu i akceptacji. Priorytety: [backlog](../plan/backlog.md).

Ważne korekty do historycznej propozycji: zatwierdzenia działają wyłącznie w panelu webowym; brak panelu kończy start serwera. Nie ma fallbacku tkinter. Zapis wieloplikowy ma best-effort rollback, bez gwarancji pełnej atomowości przy awarii. `.ps1` działa już przez runner skills; zakaz poniżej dotyczy proponowanego execute_command. Sekcje „testy” opisują wymagane scenariusze, nie wyniki. Wzmianki o ADR 0003 odnoszą się do planowanego dokumentu, który jeszcze nie powstał.

Źródło i pełna historia: [faza 5](../archive/pre-okf-2026-10-15/plan/phase-5.md). Aktualna ochrona plików i panelu: [kontrakt](files-and-approvals.md).

## Tematy

- [Model i twarde reguły komend](command-model-proposal.md)
- [Model zagrożeń i środowisko firmowe](command-threats-proposal.md)
- [Podział komend i skills, Docker](command-skills-proposal.md)
- [Wymagane testy komend](command-validation-proposal.md)

Numeracja historycznych sekcji 4.1–4.7 jest zachowana. Odwołania między sekcjami można rozwiązać przez tę mapę. Żaden z tych dokumentów nie oznacza wdrożenia execute_command, command_policy, skilla docker ani env-info.
