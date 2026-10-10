#!/usr/bin/env python3
"""Wygaszanie zadań czekających zbyt długo na decyzję (awaiting_approval -> rejected).

Korzysta z TaskManager.resolve_approval(id, "timeout"). Bez zależności od UI.
"""
import logging
import threading
from datetime import datetime, timezone
from typing import List, Optional

from tasks.manager import TaskManager, TaskNotFoundError, TaskStateError
from tasks.models import TaskStatus

logger = logging.getLogger(__name__)

LIST_LIMIT = 1000


def _age_seconds(created_at: str, now: datetime) -> Optional[float]:
    try:
        created = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created).total_seconds()


def expire_overdue(manager: TaskManager, max_age_s: float,
                   now: Optional[datetime] = None) -> List[int]:
    """Odrzuca (decyzja 'timeout') zadania awaiting_approval starsze niż max_age_s.

    max_age_s <= 0 wyłącza wygaszanie. Zwraca id zadań, które faktycznie wygasły.
    """
    if max_age_s <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    expired: List[int] = []
    for task in manager.list(TaskStatus.AWAITING_APPROVAL, LIST_LIMIT):
        age = _age_seconds(task.created_at, now)
        if age is None or age < max_age_s:
            continue
        try:
            manager.resolve_approval(task.id, "timeout")
            expired.append(task.id)
        except (TaskStateError, TaskNotFoundError):
            continue  # użytkownik zdążył zdecydować lub anulować
    return expired


class ExpiryWatcher:
    """Wątek-demon cyklicznie wywołujący expire_overdue."""

    def __init__(self, manager: TaskManager, max_age_s: float, interval_s: float = 30.0):
        self._manager = manager
        self._max_age_s = float(max_age_s)
        self._interval_s = max(0.05, float(interval_s))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running or self._max_age_s <= 0:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="approval-expiry")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                expired = expire_overdue(self._manager, self._max_age_s)
                if expired:
                    logger.info("Wygasły zadania oczekujące na decyzję: %s", expired)
            except Exception:
                logger.exception("Błąd strażnika wygasania zadań")
            self._stop.wait(self._interval_s)
