#!/usr/bin/env python3
"""TaskManager: kolejka FIFO, pula wątków, stany zadań, wait/cancel.

Bez zależności od FastMCP, tkintera i skills. Wszystko zależne od skilla
(uruchomienie, weryfikacja hasha, zapis zaufania) przychodzi jako funkcje.
"""
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Union

from tasks.approval import VALID_DECISIONS, ApprovalService
from tasks.models import Task, TaskStatus
from tasks.store import TaskStore, now_iso
from tasks.volatile_results import VolatileResult, VolatileResultStore

logger = logging.getLogger(__name__)

CANCEL_FINALIZE_WAIT_S = 7.0  # kill grace (5 s) + zapas na pętlę runnera

Executor = Callable[[threading.Event], Union[Dict[str, Any], VolatileResult]]
SECRET_KIND = "secret_reveal"
SECRET_ERROR = "Ujawnienie przerwane: konflikt, nieprawidłowy wynik lub błąd wewnętrzny."
SECRET_SUMMARY = "Wynik ujawnienia dostępny wyłącznie w pamięci przez ograniczony czas."


class QueueFullError(Exception):
    pass


class TaskStateError(Exception):
    pass


class TaskNotFoundError(KeyError):
    pass


@dataclass
class _Ctx:
    executor: Executor
    verify: Optional[Callable[[], Optional[str]]] = None
    on_trust: Optional[Callable[[], None]] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    decision: str = ""
    confidential: bool = False


class TaskManager:
    def __init__(self, store: TaskStore, approval: ApprovalService, max_parallel: int = 2,
                 max_queued: int = 50, max_wait_s: float = 8,
                 volatile_store: Optional[VolatileResultStore] = None):
        self._store = store
        self._approval = approval
        self._max_parallel = max(1, int(max_parallel))
        self._max_queued = int(max_queued)
        self._max_wait_s = float(max_wait_s)
        self._cond = threading.Condition(threading.RLock())
        self._queue: "queue.Queue[Optional[int]]" = queue.Queue()
        self._ctx: Dict[int, _Ctx] = {}
        self._workers = []
        self._stopped = False
        self._volatile = volatile_store if volatile_store is not None else VolatileResultStore()
        approval.bind(self.resolve_approval)

    @property
    def max_wait_s(self) -> float:
        return self._max_wait_s

    # --- publiczne API ---
    def submit(self, fields: Dict[str, Any], executor: Executor, needs_approval: bool,
               verify: Optional[Callable[[], Optional[str]]] = None,
               on_trust: Optional[Callable[[], None]] = None) -> Task:
        with self._cond:
            if self._stopped:
                raise TaskStateError("Menedżer zadań jest zatrzymany.")
            confidential = fields.get("kind") == SECRET_KIND
            if confidential and (needs_approval is not True or fields.get("sensitive") is not True
                                 or fields.get("offer_trust", False) is not False
                                 or on_trust is not None):
                raise TaskStateError("Ujawnienie wymaga panelu, flagi sensitive i braku zaufania.")
            pending = (len(self._store.list(TaskStatus.AWAITING_APPROVAL, self._max_queued))
                       + len(self._store.list(TaskStatus.QUEUED, self._max_queued)))
            if pending >= self._max_queued:
                raise QueueFullError(f"Kolejka pełna ({self._max_queued} zadań oczekuje).")
            status = TaskStatus.AWAITING_APPROVAL if needs_approval else TaskStatus.QUEUED
            task = self._store.create(**dict(fields), status=status)
            self._ctx[task.id] = _Ctx(executor=executor, verify=verify, on_trust=on_trust,
                                      confidential=confidential)
            self._ensure_workers()
            if not needs_approval:
                self._queue.put(task.id)
            self._cond.notify_all()
        if needs_approval:
            self._approval.request(task)
        return task

    def get(self, task_id: int) -> Optional[Task]:
        return self._store.get(task_id)

    @staticmethod
    def _secret_allowed(task: Optional[Task]) -> bool:
        return bool(task is not None and task.kind == SECRET_KIND and task.sensitive
                    and not task.offer_trust and task.decision == "allow")

    def get_volatile_result(self, task_id: int) -> Dict[str, Any]:
        """Osobny kanał dla MCP get_task; Task/panel/list/wait/cancel bez sekretu.

        Callback dostępu działa poza _cond i lockiem magazynu. Ponowna kontrola
        menedżera po pobraniu uniemożliwia zwrot po zakończonym shutdown.
        """
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1:
            return {"state": "unavailable"}
        try:
            with self._cond:
                task = self._store.get(task_id)
                if self._stopped or not self._secret_allowed(task) or task.status != TaskStatus.DONE:
                    return {"state": "unavailable"}
            result = self._volatile.get(task_id)
            with self._cond:
                task = self._store.get(task_id)
                if self._stopped or not self._secret_allowed(task) or task.status != TaskStatus.DONE:
                    self._volatile.discard(task_id)
                    return {"state": "unavailable"}
                return result
        except Exception:
            self._volatile.discard(task_id)
            return {"state": "unavailable"}

    def list(self, status: Optional[TaskStatus] = None, limit: int = 20):
        return self._store.list(status=status, limit=limit)

    def wait(self, task_id: int, seconds: float) -> Task:
        """Czeka (maks. max_wait_s) na stan terminalny; zwraca aktualne zadanie."""
        seconds = max(0.0, min(float(seconds), self._max_wait_s))
        deadline = time.monotonic() + seconds
        with self._cond:
            while True:
                task = self._require(task_id)
                if task.is_terminal:
                    return task
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return task
                self._cond.wait(remaining)

    def resolve_approval(self, task_id: int, decision: str) -> Task:
        if decision not in VALID_DECISIONS:
            raise ValueError(f"Nieznana decyzja '{decision}'.")
        with self._cond:
            task = self._require(task_id)
            if task.status != TaskStatus.AWAITING_APPROVAL:
                raise TaskStateError(
                    f"Zadanie {task_id} nie oczekuje na zatwierdzenie (status: {task.status.value}).")
            ctx = self._ctx.get(task_id)
            if decision in ("allow", "trust"):
                if decision == "trust" and (not task.offer_trust or task.sensitive):
                    decision = "allow"
                if ctx is not None:
                    ctx.decision = decision
                task = self._store.update(
                    task_id, status=TaskStatus.QUEUED, decision=decision, decided_at=now_iso())
                self._queue.put(task_id)
            else:
                error = "Brak odpowiedzi użytkownika w czasie oczekiwania." \
                    if decision == "timeout" else "Odrzucone przez użytkownika."
                task = self._store.update(
                    task_id, status=TaskStatus.REJECTED, decision=decision,
                    decided_at=now_iso(), finished_at=now_iso(), error=error)
                self._ctx.pop(task_id, None)
            self._cond.notify_all()
            return task

    def cancel(self, task_id: int) -> Task:
        with self._cond:
            task = self._require(task_id)
            if task.is_terminal:
                return task
            if task.status in (TaskStatus.AWAITING_APPROVAL, TaskStatus.QUEUED):
                task = self._store.update(
                    task_id, status=TaskStatus.CANCELLED, finished_at=now_iso(),
                    error="Anulowano.")
                self._ctx.pop(task_id, None)
                self._cond.notify_all()
                return task
            ctx = self._ctx.get(task_id)
            if ctx is not None:
                ctx.cancel_event.set()
            deadline = time.monotonic() + CANCEL_FINALIZE_WAIT_S
            while True:
                task = self._require(task_id)
                remaining = deadline - time.monotonic()
                if task.is_terminal or remaining <= 0:
                    return task
                self._cond.wait(remaining)

    def shutdown(self) -> None:
        """Zatrzymuje workery (zadania w toku nie są ubijane; po restarcie -> interrupted)."""
        with self._cond:
            self._stopped = True
            for _ in self._workers:
                self._queue.put(None)
            self._cond.notify_all()
        # _stopped blokuje nowe publikacje/pobrania; join bez _cond.
        self._volatile.close()

    # --- wnętrze ---
    def _require(self, task_id: int) -> Task:
        task = self._store.get(task_id)
        if task is None:
            raise TaskNotFoundError(f"Brak zadania {task_id}")
        return task

    def _ensure_workers(self) -> None:
        while len(self._workers) < self._max_parallel:
            th = threading.Thread(target=self._worker_loop, daemon=True,
                                  name=f"task-worker-{len(self._workers) + 1}")
            self._workers.append(th)
            th.start()

    def _worker_loop(self) -> None:
        while True:
            task_id = self._queue.get()
            if task_id is None:
                return
            try:
                self._run(task_id)
            except Exception:
                with self._cond:
                    ctx = self._ctx.get(task_id)
                    confidential = ctx is None or ctx.confidential
                if confidential:
                    self._volatile.discard(task_id)
                    # Bez tracebacku: wyjątek może zawierać poufną treść.
                    logger.error("Błąd finalizacji poufnego zadania %s", task_id)
                else:
                    logger.exception("Błąd wewnętrzny przy zadaniu %s", task_id)
                try:
                    self._finish(task_id, TaskStatus.FAILED, error="Błąd wewnętrzny menedżera zadań.")
                except Exception:
                    if confidential:
                        logger.error("Nie udało się zakończyć poufnego zadania %s", task_id)
                    else:
                        logger.exception("Nie udało się zakończyć zadania %s", task_id)

    def _run(self, task_id: int) -> None:
        with self._cond:
            task = self._store.get(task_id)
            ctx = self._ctx.get(task_id)
            if task is None or ctx is None or task.status != TaskStatus.QUEUED:
                return  # np. anulowane w kolejce
            self._store.update(task_id, status=TaskStatus.RUNNING, started_at=now_iso())
            self._cond.notify_all()

        started = time.monotonic()
        try:
            if ctx.confidential:
                if not self._secret_allowed(task) or ctx.decision != "allow":
                    self._finish(task_id, TaskStatus.FAILED, error=SECRET_ERROR)
                    return
                if self._stopped or ctx.cancel_event.is_set():
                    self._finish_secret(task_id, ctx, None, started)
                    return
            if ctx.verify is not None:
                problem = ctx.verify()
                if problem:
                    self._finish(task_id, TaskStatus.FAILED,
                                 error=SECRET_ERROR if ctx.confidential else problem)
                    return
            if ctx.decision == "trust" and ctx.on_trust is not None:
                ctx.on_trust()
            result = ctx.executor(ctx.cancel_event)
        except Exception as exc:
            if ctx.confidential:
                self._finish_secret(task_id, ctx, None, started)
            else:
                logger.exception("Wyjątek w zadaniu %s", task_id)
                self._finish(task_id, TaskStatus.FAILED, error=f"Wyjątek: {exc}")
            return

        if isinstance(result, VolatileResult):
            ctx.confidential = True  # obejmuje także błąd wyniku z niewłaściwego kind
            self._finish_secret(task_id, ctx, result, started)
            return
        if ctx.confidential:
            # Słownik błędu może zawierać surowe stdout/error; niczego nie utrwalamy.
            cancelled = type(result) is dict and result.get("cancelled") is True
            if cancelled:
                ctx.cancel_event.set()
            self._finish_secret(task_id, ctx, None, started)
            return

        fields: Dict[str, Any] = {
            "exit_code": result.get("exit_code"),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "stdout_truncated": bool(result.get("stdout_truncated", False)),
            "stderr_truncated": bool(result.get("stderr_truncated", False)),
            "duration_s": result.get("duration_s"),
        }
        if result.get("cancelled"):
            status, error = TaskStatus.CANCELLED, "Anulowano."
        elif not result.get("started", True):
            status, error = TaskStatus.FAILED, result.get("error", "Proces nie wystartował.")
        elif result.get("timed_out"):
            status, error = TaskStatus.TIMED_OUT, f"Przekroczono timeout {task.timeout} s."
        elif result.get("exit_code") == 0:
            status, error = TaskStatus.DONE, ""
        else:
            status, error = TaskStatus.FAILED, f"Kod wyjścia {result.get('exit_code')}."
        if status in (TaskStatus.CANCELLED, TaskStatus.TIMED_OUT):
            fields["exit_code"] = None  # kod ubitego przez nas procesu nie ma znaczenia
        self._finish(task_id, status, error=error, **fields)

    def _finish_secret(self, task_id: int, ctx: _Ctx,
                       result: Optional[VolatileResult], started: float) -> None:
        """Finalizacja bez surowych pól executora i bez tekstu wyjątku.

        Kolejność: _cond -> magazyn -> SQLite. Lock magazynu obejmuje zapis
        DONE i aktywację; get nie widzi częściowo opublikowanego wyniku.
        """
        with self._cond:
            with self._volatile._lock:
                token = None
                try:
                    task = self._store.get(task_id)
                    if task is None or task.status != TaskStatus.RUNNING:
                        self._volatile.discard(task_id)
                        return
                    fields = dict(exit_code=None, stdout="", stderr="",
                                  stdout_truncated=False, stderr_truncated=False,
                                  duration_s=round(time.monotonic() - started, 3))
                    if ctx.cancel_event.is_set():
                        status, error = TaskStatus.CANCELLED, "Anulowano."
                    elif self._stopped:
                        status, error = TaskStatus.FAILED, "Serwer zatrzymany; wynik ujawnienia odrzucono."
                    elif not self._secret_allowed(task) or ctx.decision != "allow" or result is None:
                        status, error = TaskStatus.FAILED, SECRET_ERROR
                    else:
                        token = self._volatile.reserve(task_id, result)
                        status, error = TaskStatus.DONE, ""
                        fields.update(exit_code=0, stdout=SECRET_SUMMARY)
                    self._store.update(task_id, status=status, finished_at=now_iso(),
                                       error=error, **fields)
                    if token is not None:
                        self._volatile.activate(task_id, token)
                except Exception:
                    self._volatile.discard(task_id, token)
                    # Nawet błąd magazynu/SQLite/clock nie trafia do tracebacku.
                    try:
                        self._store.update(task_id, status=TaskStatus.FAILED,
                                           finished_at=now_iso(), error=SECRET_ERROR,
                                           exit_code=None, stdout="", stderr="",
                                           stdout_truncated=False, stderr_truncated=False)
                    except Exception:
                        logger.error("Nie udało się utrwalić błędu poufnego zadania %s", task_id)
                finally:
                    self._ctx.pop(task_id, None)
                    self._cond.notify_all()

    def _finish(self, task_id: int, status: TaskStatus, error: str = "", **fields: Any) -> None:
        with self._cond:
            self._store.update(task_id, status=status, finished_at=now_iso(), error=error, **fields)
            self._ctx.pop(task_id, None)
            self._cond.notify_all()
