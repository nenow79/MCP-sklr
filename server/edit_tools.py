#!/usr/bin/env python3
"""Narzędzia MCP zapisów 5b/B1: edit_file i create_file (bez zależności od FastMCP).

Przepływ jak request_root: przygotowanie planu PRZED submit (błąd = brak zadania),
zadanie z needs_approval=True i natychmiastowy task_id, bez wait. Do zadania, SQLite
i odpowiedzi trafia wyłącznie maskowany podgląd z PreparedChanges.preview(): bez
surowych bajtów, treści żądania i hashy plików. Plan (prywatne bajty) żyje tylko
w domknięciach verify/executor. Komunikaty błędów są stałe, bez str(wyjątku).
"""
import time
import unicodedata
from typing import Any, Dict, Optional

from roots_registry import RootsRegistry
from server.file_changes import ChangeError, PreparedChanges, SensitivePolicy, prepare_edit, prepare_write
from server.file_transaction import FileTransaction
from server.path_guard import PathError
from tasks.manager import QueueFullError, TaskManager, TaskStateError
from tasks.models import TaskStatus

KIND_FILE_EDIT = "file_edit"
KIND_FILE_WRITE = "file_write"
DESCRIPTION_MAX = 500
TITLE_MAX = 200
TASK_TIMEOUT = 10  # jak request_root: pole zadania, nie limit czasu zatwierdzania

WAIT_HINT = ("Zadanie czeka na decyzję użytkownika w panelu zatwierdzeń (zatwierdza użytkownik, nie ty). "
             "Zatrzymaj się i poczekaj na jego wiadomość; nie odpytuj wait_task w pętli. "
             "Po decyzji sprawdź wynik przez get_task. Po błędzie \": ping\" przed ponowieniem "
             "sprawdź list_tasks (zadanie mogło powstać).")
APPROVAL_REASON = ("Model prosi o zapis w plikach. Sprawdź diff; zmiany zostaną zapisane dopiero "
                   "po zatwierdzeniu, a przed zapisem stan plików jest sprawdzany ponownie.")
INTERNAL_ERROR = "Nie udało się przygotować zmiany (błąd wewnętrzny)."
EXEC_INTERNAL_ERROR = "Błąd wewnętrzny transakcji; sprawdź pliki przed ponowieniem."
NO_CHANGES_ERROR = "Brak zmian: wynik jest identyczny z obecną zawartością."
PANEL_DOWN_ERROR = ("Zapisy są możliwe tylko po zatwierdzeniu w panelu; panel jest niedostępny, "
                    "więc zadanie odrzucono.")
ROLLBACK_ERROR = "Niepełny rollback: część zmian mogła pozostać; wymagane sprawdzenie plików."
CLEANUP_ERROR = "Niepełne sprzątanie plików tymczasowych."
COMMITTED_NOTE = "Zmiany zapisano mimo błędu sprzątania; nie ponawiaj zapisu w ciemno."


def _error(message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"success": False, "error": message}
    if data is not None:
        result["data"] = data
    return result


def check_description(value: Any) -> str:
    """Opis od modelu: tekst jednoliniowy do 500 znaków, bez znaków sterujących i bidi."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ChangeError("description musi być tekstem.")
    text = value.strip()
    if len(text) > DESCRIPTION_MAX:
        raise ChangeError(f"description przekracza {DESCRIPTION_MAX} znaków.")
    for ch in text:
        # Cc: sterujące (też nowa linia/tab), Cf: bidi i zero-width, Zl/Zp: separatory linii, Cs: surogaty
        if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Cs"):
            raise ChangeError("description nie może zawierać znaków sterujących ani kierunkowych.")
    return text


def _label(entry: Dict[str, Any]) -> str:
    return f"{entry['root']}:{entry['filepath']}"


def _diff_text(preview: Dict[str, Any]) -> str:
    """Jeden tekst dla panelu: nagłówek per plik i maskowany diff z preview()."""
    parts = []
    for entry in preview["files"]:
        if entry["created"]:
            flags = ["nowy"]
        else:
            flags = ["zmiana"] if entry["changed"] else ["bez zmian"]
        if entry["hidden_change"]:
            flags.append("ukryta zmiana")
        if entry["sensitive"]:
            flags.append("wrażliwy")
        if entry["masked"]:
            flags.append("zamaskowany")
        if entry["truncated"]:
            flags.append("skrócony")
        diff = entry["diff"]
        if diff and not diff.endswith("\n"):
            diff += "\n"
        parts.append(f"=== {_label(entry)} [{', '.join(flags)}] ===\n{diff}")
    return "\n".join(parts)


def _file_meta(entry: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("root", "filepath", "created", "changed", "masked", "hidden_change", "truncated",
            "sensitive_reasons", "encoding", "eol_before", "eol_after", "eol_adjusted")
    return {key: entry[key] for key in keys}


def _title(verb_many: str, verb_one: str, preview: Dict[str, Any]) -> str:
    entries = preview["files"]
    if len(entries) == 1:
        return f"{verb_one} {_label(entries[0])}"[:TITLE_MAX]
    return f"{verb_many} ({len(entries)})"


def _reason(preview: Dict[str, Any]) -> str:
    reasons = sorted({r for e in preview["files"] for r in e["sensitive_reasons"]})
    if not reasons:
        return APPROVAL_REASON
    return APPROVAL_REASON + " Zmiana wrażliwa: " + ", ".join(reasons) + "."


def _make_executor(transaction: FileTransaction, plan: PreparedChanges, count: int, after_write=None):
    """Adaptuje wynik transakcji do kontraktu menedżera; nigdy nie rzuca."""
    def executor(cancel_event) -> Dict[str, Any]:
        started = time.monotonic()
        try:
            result = transaction.execute(plan, cancel_event)
            data = result.get("data") or {}
            committed = bool(data.get("committed"))
            rollback_ok = bool(data.get("rollback_complete", False))
            cleanup_ok = bool(data.get("cleanup_complete", False))
            published = int(data.get("published_count", 0))
            cancelled = bool(data.get("cancelled"))
            success = bool(result.get("success"))
        except Exception:  # surowy wyjątek nie trafia do error/logów menedżera
            from autopilot.context import mark_uncertain
            mark_uncertain()
            if after_write is not None:
                try:
                    after_write(tuple(item.path for item in plan.files if item.changed), uncertain=True)
                except Exception:
                    pass
            return {"started": False, "error": EXEC_INTERNAL_ERROR}
        index_note = ""
        if after_write is not None and (committed or not rollback_ok):
            try:
                after_write(tuple(item.path for item in plan.files if item.changed),
                            uncertain=not committed or not rollback_ok)
            except Exception:
                # Zapis pliku pozostaje udany nawet przy awarii powiadomienia indeksu.
                index_note = "\nAktualizacja indeksu nie została potwierdzona; sprawdź stan wiedzy."
        duration = round(time.monotonic() - started, 3)
        summary = (f"committed={str(committed).lower()}, published={published}, "
                   f"rollback_complete={str(rollback_ok).lower()}, cleanup_complete={str(cleanup_ok).lower()}")
        if not rollback_ok or not cleanup_ok:
            if not rollback_ok:
                from autopilot.context import mark_uncertain
                mark_uncertain()
            error = ROLLBACK_ERROR if not rollback_ok else CLEANUP_ERROR
            if not rollback_ok and not cleanup_ok:
                error += " " + CLEANUP_ERROR
            if committed:
                error += " " + COMMITTED_NOTE
            return {"started": False, "error": error, "stdout": summary, "duration_s": duration}
        if success:
            return {"exit_code": 0, "stdout": f"Zapisano plików: {published} z {count}." + index_note,
                    "stderr": "", "duration_s": duration}
        if cancelled:
            return {"cancelled": True, "stdout": summary, "duration_s": duration}
        error = result.get("error")
        message = error if isinstance(error, str) and error else EXEC_INTERNAL_ERROR
        return {"started": False, "error": message, "stdout": summary, "duration_s": duration}
    return executor


def _submit(manager: TaskManager, transaction: FileTransaction, plan: PreparedChanges,
            kind: str, title: str, description: str, preview: Dict[str, Any], after_write=None) -> Dict[str, Any]:
    roots = {entry["root"] for entry in preview["files"]}
    payload = {
        "root": next(iter(roots)) if len(roots) == 1 else None,
        "paths": [_label(entry) for entry in preview["files"]],
        "diff": _diff_text(preview),
        "files": [_file_meta(entry) for entry in preview["files"]],
        "truncated": bool(preview["truncated"]),
        "description": description,
    }
    fields = dict(
        kind=kind, sensitive=bool(plan.sensitive), title=title, payload=payload,
        approval_reason=_reason(preview), timeout=TASK_TIMEOUT,
    )
    try:
        submitted = manager.submit(
            fields, _make_executor(transaction, plan, len(preview["files"]), after_write),
            needs_approval=True, verify=lambda: transaction.verify(plan))
    except (QueueFullError, TaskStateError) as exc:
        return _error(str(exc))
    task = manager.get(submitted.id) or submitted
    if task.status == TaskStatus.REJECTED:
        return _error(PANEL_DOWN_ERROR, {"task_id": task.id, "status": task.status.value})
    return {"success": True, "data": {
        "task_id": task.id, "status": task.status.value, "kind": kind, "paths": payload["paths"],
        "sensitive": task.sensitive, "truncated": payload["truncated"], "hint": WAIT_HINT}}


def _prepared(prepare, *args, **kwargs):
    """Zwraca (plan, preview) albo (None, odpowiedź błędu)."""
    try:
        plan = prepare(*args, **kwargs)
        if not any(item.changed for item in plan.files):
            return None, _error(NO_CHANGES_ERROR)
        return plan, plan.preview()
    except (ChangeError, PathError) as exc:
        return None, _error(str(exc))


def edit_file_impl(
    registry: RootsRegistry,
    manager: TaskManager,
    transaction: FileTransaction,
    changes: Any,
    root: Any = None,
    description: Any = "",
    policy: Optional[SensitivePolicy] = None,
    after_write=None,
) -> Dict[str, Any]:
    try:
        try:
            text = check_description(description)
        except ChangeError as exc:
            return _error(str(exc))
        plan, preview = _prepared(prepare_edit, registry, changes, root, policy=policy)
        if plan is None:
            return preview
        title = _title("Edytuj pliki", "Edytuj", preview)
        return _submit(manager, transaction, plan, KIND_FILE_EDIT, title, text, preview, after_write)
    except Exception:
        return _error(INTERNAL_ERROR)


def create_file_impl(
    registry: RootsRegistry,
    manager: TaskManager,
    transaction: FileTransaction,
    filepath: Any,
    content: Any,
    overwrite: Any = False,
    root: Any = None,
    description: Any = "",
    policy: Optional[SensitivePolicy] = None,
    after_write=None,
) -> Dict[str, Any]:
    try:
        try:
            text = check_description(description)
        except ChangeError as exc:
            return _error(str(exc))
        plan, preview = _prepared(prepare_write, registry, filepath, content, overwrite, root, policy=policy)
        if plan is None:
            return preview
        verb = "Utwórz" if preview["files"][0]["created"] else "Nadpisz"
        title = _title(verb, verb, preview)
        return _submit(manager, transaction, plan, KIND_FILE_WRITE, title, text, preview, after_write)
    except Exception:
        return _error(INTERNAL_ERROR)
