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


def build_message(task: Task) -> str:
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
