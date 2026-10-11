#!/usr/bin/env python3
"""Adapter okna dialogowego (tymczasowy, do fazy 4b) -> ApprovalService.

Bez importu tkintera: funkcja `ask` (np. gui_simple.ask_skill_confirmation) jest wstrzykiwana.
Notifier nie blokuje: okno działa w wątku-demonie, a decyzja trafia przez approval.resolve().
"""
import logging
import threading
from typing import Callable

from tasks.approval import ApprovalService, Notifier
from tasks.manager import TaskNotFoundError, TaskStateError
from tasks.models import Task

logger = logging.getLogger(__name__)

# ask(title, message, allow_trust, timeout_s) -> "allow" | "trust" | "deny" | "timeout"
AskFunc = Callable[[str, str, bool, int], str]

_VALID = ("allow", "trust", "deny", "timeout")


def build_title(task: Task) -> str:
    return "Ufaj temu skillowi?" if task.offer_trust else "Uruchomienie skryptu skilla"


MAX_DIALOG_CHARS = 10000  # dłuższa treść nie mieści się w oknie zapasowym: odrzucamy zamiast zatwierdzać na ślepo
AUTO_DENY_KINDS = ("root_change", "secret_reveal")  # tylko panel (zarządzanie dostępem i sekrety)


def _escape_controls(text: str) -> str:
    """Znaki sterujące i dwukierunkowe Unicode pokazujemy jawnie (nie mogą ukrywać treści)."""
    out = []
    for ch in text:
        code = ord(ch)
        if ch in "\n\t" or (code >= 0x20 and code != 0x7F
                            and not 0x200B <= code <= 0x200F
                            and not 0x202A <= code <= 0x202E
                            and not 0x2066 <= code <= 0x2069):
            out.append(ch)
        else:
            out.append(f"<U+{code:04X}>")
    return "".join(out)


def _generic_body(task: Task) -> str:
    payload = task.payload or {}
    lines = [
        f"Zadanie:    #{task.id}",
        f"Rodzaj:     {task.kind}" + ("  [WRAŻLIWE]" if task.sensitive else ""),
        f"Tytuł:      {task.title or '(brak)'}",
    ]
    if task.command:
        lines.append(f"Komenda:    {task.command}")
    for key in ("root", "cwd", "path", "paths"):
        if payload.get(key):
            lines.append(f"{key}: {payload[key]}")
    if task.approval_reason:
        lines.append(f"\nPowód pytania: {task.approval_reason}")
    if payload.get("description"):
        lines.append(f"\nOpis od modelu (niezweryfikowany): {payload['description']}")
    if payload.get("diff"):
        lines.append(f"\n--- Diff ---\n{payload['diff']}")
    return "\n".join(lines)


def should_auto_deny(task: Task) -> str:
    """Zwraca powód automatycznego odrzucenia w oknie zapasowym albo pusty napis."""
    if task.kind == "skill_script":
        return ""
    if task.sensitive:
        return "zadanie wrażliwe wymaga panelu"
    if task.kind in AUTO_DENY_KINDS:
        return f"zadanie '{task.kind}' wymaga panelu"
    if len(build_message(task)) > MAX_DIALOG_CHARS:
        return f"treść dłuższa niż {MAX_DIALOG_CHARS} znaków nie mieści się w oknie"
    return ""


def build_message(task: Task) -> str:
    if task.kind != "skill_script":
        return _escape_controls(_generic_body(task))
    interpreter = task.command[0] if task.command else "(brak)"
    return (
        f"Zadanie:    #{task.id}\n"
        f"Skill:      {task.skill} ({task.source})\n"
        f"Skrypt:     {task.script}\n"
        f"Argumenty:  {task.args if task.args else '(brak)'}\n"
        f"Interpreter: {interpreter}\n"
        f"Katalog:    {task.skill_dir}\n"
        f"Hash:       {task.skill_hash[:16]}...\n\n"
        f"Powód pytania: {task.approval_reason}\n\n"
        f"--- Podgląd skryptu ---\n{task.script_preview}"
    )


def make_dialog_notifier(approval: ApprovalService, ask: AskFunc, timeout_s: int) -> Notifier:
    """Zwraca notifier dla ApprovalService: pokazuje okno w wątku tła i odsyła decyzję."""

    def worker(task: Task) -> None:
        reason = should_auto_deny(task)
        if reason:
            logger.warning("Okno zapasowe: zadanie %s odrzucone automatycznie (%s). "
                           "Użyj panelu zatwierdzeń.", task.id, reason)
            try:
                approval.resolve(task.id, "deny")
            except (TaskStateError, TaskNotFoundError):
                pass
            except Exception:
                logger.exception("Nie udało się odrzucić zadania %s", task.id)
            return
        try:
            answer = ask(build_title(task), build_message(task), task.offer_trust, timeout_s)
        except Exception:
            logger.exception("Okno zatwierdzenia zadania %s zgłosiło błąd; odrzucam", task.id)
            answer = "deny"
        if answer not in _VALID:
            answer = "deny"
        try:
            approval.resolve(task.id, answer)
        except (TaskStateError, TaskNotFoundError):
            logger.info("Zadanie %s nie czeka już na decyzję (np. anulowane); pomijam '%s'",
                        task.id, answer)
        except Exception:
            logger.exception("Nie udało się zapisać decyzji dla zadania %s", task.id)

    def notifier(task: Task) -> None:
        threading.Thread(target=worker, args=(task,), daemon=True,
                         name=f"approval-dialog-{task.id}").start()

    return notifier
