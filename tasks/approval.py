#!/usr/bin/env python3
"""ApprovalService: niezależny od UI interfejs zatwierdzania zadań.

UI (tkinter w 4a, panel web w 4b) dostaje zadanie przez notifier i odpowiada
przez resolve(). Notifier nie może blokować (UI działa we własnym wątku).
"""
import logging
from typing import Callable, Optional

from tasks.models import Task

logger = logging.getLogger(__name__)

VALID_DECISIONS = ("allow", "trust", "deny", "timeout")

Notifier = Callable[[Task], None]
Resolver = Callable[[int, str], Task]


class ApprovalService:
    def __init__(self, notifier: Optional[Notifier] = None):
        self._notifier = notifier
        self._resolver: Optional[Resolver] = None

    def bind(self, resolver: Resolver) -> None:
        """Wołane przez TaskManager: podpina obsługę decyzji."""
        self._resolver = resolver

    def request(self, task: Task) -> None:
        """Informuje UI o zadaniu czekającym na decyzję. Błąd UI nie psuje zadania."""
        if self._notifier is None:
            return
        try:
            self._notifier(task)
        except Exception:
            logger.exception("Notifier zatwierdzeń zgłosił błąd (zadanie %s czeka dalej)", task.id)

    def resolve(self, task_id: int, decision: str) -> Task:
        """Decyzja: allow | trust | deny | timeout."""
        if decision not in VALID_DECISIONS:
            raise ValueError(f"Nieznana decyzja '{decision}'. Dozwolone: {list(VALID_DECISIONS)}.")
        if self._resolver is None:
            raise RuntimeError("ApprovalService nie jest podłączony do TaskManager.")
        return self._resolver(task_id, decision)
