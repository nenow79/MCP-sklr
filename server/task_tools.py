#!/usr/bin/env python3
"""Logika narzędzi MCP dla kolejki zadań (bez zależności od FastMCP - łatwe testowanie)."""
from typing import Any, Dict, List, Optional

from server.skills_tools import PreparedRun, prepare_script_run
from skills_engine.registry import SkillRegistry
from skills_engine.runner import DEFAULT_TIMEOUT, clean_env, run_command
from skills_engine.trust import TrustStore, compute_skill_hash
from tasks.manager import QueueFullError, TaskManager, TaskNotFoundError, TaskStateError
from tasks.models import Task, TaskStatus

DEFAULT_WAIT = 8
LIST_MIN = 1
LIST_MAX = 100

_CHANGED_MSG = ("Skill zmienił się podczas oczekiwania na zatwierdzenie; "
                "skrypt nie został uruchomiony. Ponów wywołanie.")


def _error(message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"success": False, "error": message}
    if data is not None:
        result["data"] = data
    return result


def _check_task_id(task_id: Any) -> Optional[str]:
    if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1:
        return "task_id musi być dodatnią liczbą całkowitą."
    return None


def _check_seconds(value: Any, label: str) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return f"{label} musi być liczbą >= 0 (sekundy)."
    return None


def _status_hint(task: Task) -> str:
    if task.status == TaskStatus.AWAITING_APPROVAL:
        return ("Zadanie czeka na zatwierdzenie przez użytkownika (nie zatwierdzasz go sam). "
                "Po jego decyzji użyj wait_task(task_id) lub get_task(task_id).")
    return "Zadanie trwa. Użyj wait_task(task_id) lub get_task(task_id), aby poznać wynik."


def _pending_response(task: Task) -> Dict[str, Any]:
    return {
        "success": True,
        "data": {"task_id": task.id, "status": task.status.value, "hint": _status_hint(task)},
    }


def _result_data(task: Task) -> Dict[str, Any]:
    return {
        "task_id": task.id,
        "status": task.status.value,
        "skill": task.skill,
        "script": task.script,
        "exit_code": task.exit_code,
        "stdout": task.stdout,
        "stderr": task.stderr,
        "stdout_truncated": task.stdout_truncated,
        "stderr_truncated": task.stderr_truncated,
        "duration_s": task.duration_s,
        "timed_out": task.status == TaskStatus.TIMED_OUT,
    }


def task_to_response(task: Task) -> Dict[str, Any]:
    """Zamienia zadanie na odpowiedź w kształcie dotychczasowego run_skill_script."""
    if not task.is_terminal:
        return _pending_response(task)
    data = _result_data(task)
    if task.status == TaskStatus.DONE:
        return {"success": True, "data": data}
    if task.status == TaskStatus.TIMED_OUT:
        return _error(
            f"Skrypt przerwany po {task.timeout} s (timeout); proces został zakończony. "
            "Zwrócono częściowy output.", data)
    if task.status == TaskStatus.REJECTED:
        if task.decision == "timeout":
            return _error("Brak odpowiedzi użytkownika w czasie oczekiwania; "
                          "skrypt nie został uruchomiony.", {"task_id": task.id, "status": task.status.value})
        return _error("Użytkownik zablokował uruchomienie skryptu.",
                      {"task_id": task.id, "status": task.status.value})
    if task.status == TaskStatus.FAILED and task.exit_code is not None:
        return _error(f"Skrypt zakończył się kodem {task.exit_code}.", data)
    message = task.error or f"Zadanie zakończone ze statusem {task.status.value}."
    return _error(message, data if task.status == TaskStatus.CANCELLED else
                  {"task_id": task.id, "status": task.status.value})


def _make_executor(prepared: PreparedRun):
    def executor(cancel_event):
        return run_command(prepared.command, prepared.base_dir, prepared.timeout,
                           clean_env(), cancel_event)
    return executor


def _make_verify(prepared: PreparedRun):
    def verify() -> Optional[str]:
        # Skill mógł się zmienić w trakcie oczekiwania (na zatwierdzenie lub w kolejce).
        if compute_skill_hash(prepared.base_dir) != prepared.skill_hash:
            return _CHANGED_MSG
        return None
    return verify


def run_skill_script_impl(
    registry: SkillRegistry,
    config: Dict[str, Any],
    trust_store: TrustStore,
    manager: TaskManager,
    name: str,
    script: str,
    args: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    wait: float = DEFAULT_WAIT,
    default_python: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        wait_error = _check_seconds(wait, "wait")
        if wait_error:
            return _error(wait_error)

        prepared = prepare_script_run(
            registry, config, trust_store, name, script, args, timeout, default_python)
        if not isinstance(prepared, PreparedRun):
            return prepared  # błąd walidacji: bez zadania

        fields = dict(
            skill=prepared.skill,
            source=prepared.source,
            script=prepared.script_rel,
            skill_dir=str(prepared.base_dir),
            script_preview=prepared.script_preview,
            args=prepared.args,
            timeout=prepared.timeout,
            command=prepared.command,
            skill_hash=prepared.skill_hash,
            approval_reason=prepared.approval_reason,
            offer_trust=prepared.offer_trust,
        )

        def on_trust() -> None:
            trust_store.trust(prepared.skill, prepared.skill_hash)

        try:
            submitted = manager.submit(
                fields, _make_executor(prepared), prepared.approval_required,
                verify=_make_verify(prepared), on_trust=on_trust)
        except QueueFullError as e:
            return _error(str(e))
        except TaskStateError as e:
            return _error(str(e))

        # Notifier mógł już rozstrzygnąć zadanie - pobierz aktualny stan.
        task = manager.get(submitted.id) or submitted
        if task.status == TaskStatus.AWAITING_APPROVAL:
            return _pending_response(task)  # decyzja 1: bez czekania
        if not task.is_terminal:
            task = manager.wait(task.id, wait)
        return task_to_response(task)
    except Exception as e:
        return _error(str(e))


def get_task_impl(manager: TaskManager, task_id: int) -> Dict[str, Any]:
    try:
        id_error = _check_task_id(task_id)
        if id_error:
            return _error(id_error)
        task = manager.get(task_id)
        if task is None:
            return _error(f"Nie znaleziono zadania {task_id}.")
        return {"success": True, "data": task.to_dict()}
    except Exception as e:
        return _error(str(e))


def list_tasks_impl(manager: TaskManager, status: Optional[str] = None,
                    limit: int = 20) -> Dict[str, Any]:
    try:
        if isinstance(limit, bool) or not isinstance(limit, int) or not LIST_MIN <= limit <= LIST_MAX:
            return _error(f"limit musi być liczbą całkowitą od {LIST_MIN} do {LIST_MAX}.")
        status_filter = None
        if status:
            try:
                status_filter = TaskStatus(status)
            except ValueError:
                return _error(
                    f"Nieznany status '{status}'. Dozwolone: {[s.value for s in TaskStatus]}.")
        tasks = manager.list(status=status_filter, limit=limit)
        return {
            "success": True,
            "data": {"count": len(tasks), "tasks": [t.to_summary() for t in tasks]},
        }
    except Exception as e:
        return _error(str(e))


def wait_task_impl(manager: TaskManager, task_id: int,
                   seconds: float = DEFAULT_WAIT) -> Dict[str, Any]:
    try:
        id_error = _check_task_id(task_id)
        if id_error:
            return _error(id_error)
        seconds_error = _check_seconds(seconds, "seconds")
        if seconds_error:
            return _error(seconds_error)
        task = manager.wait(task_id, seconds)
        data = task.to_dict()
        if not task.is_terminal:
            data["hint"] = _status_hint(task)
        return {"success": True, "data": data}
    except TaskNotFoundError:
        return _error(f"Nie znaleziono zadania {task_id}.")
    except Exception as e:
        return _error(str(e))


def cancel_task_impl(manager: TaskManager, task_id: int) -> Dict[str, Any]:
    try:
        id_error = _check_task_id(task_id)
        if id_error:
            return _error(id_error)
        before = manager.get(task_id)
        if before is None:
            return _error(f"Nie znaleziono zadania {task_id}.")
        task = manager.cancel(task_id)
        data = task.to_dict()
        if before.is_terminal:
            data["note"] = "Zadanie było już zakończone; nic nie zmieniono."
        elif not task.is_terminal:
            data["note"] = "Anulowanie zlecone, ale zadanie jeszcze się kończy. Sprawdź get_task."
        return {"success": True, "data": data}
    except TaskNotFoundError:
        return _error(f"Nie znaleziono zadania {task_id}.")
    except Exception as e:
        return _error(str(e))
