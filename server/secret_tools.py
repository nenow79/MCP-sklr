#!/usr/bin/env python3
"""C2: ujawnienie jednej wartości lub jawnego zakresu po decyzji w panelu.

Nie rejestruje narzędzia MCP. Task/payload nie zawierają wartości ani hashy.
Podczas oczekiwania przechowujemy tylko prywatny plan bez FileSnapshot/raw/text.
Executor zwraca VolatileResult, nigdy sekret w stdout/error. Kontrola pobrania
zachowuje tylko binding: wynik jest snapshotem wykonania, nie bieżącą treścią.
Brak zapisów/temp. Locki nie zamykają TOCTOU wobec zewnętrznych zmian plików.
Usunięcie referencji nie gwarantuje zeroizacji RAM/swap/crashdump; klient/LLM
może zachować zwrócony sekret poza kontrolą serwera.
"""
import codecs
import os
import re
import stat
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from roots import Root
from roots_registry import RootsRegistry
from server.edit_tools import check_description
from server.file_changes import FileSnapshot, read_snapshot
from server.file_transaction import WRITE_LOCK
from server.path_guard import resolve_path
from server.secrets_mask import is_blocked_secret, is_secret_file
from tasks.manager import QueueFullError, TaskManager, TaskStateError
from tasks.models import TaskStatus
from tasks.volatile_results import (
    MAX_CONTENT_BYTES, MAX_CONTENT_CHARS, MAX_METADATA_BYTES, VolatileResult,
)

KIND = "secret_reveal"
TASK_TIMEOUT = 10  # pole zadania, nie limit czasu zatwierdzania
SELECTOR_ERROR = "Podaj konkretny key albo oba jawne końce zakresu; nie mieszaj selektorów."
KEY_ERROR = "Nie można jednoznacznie wybrać klucza; użyj jawnego zakresu linii."
RANGE_ERROR = "Zakres musi obejmować istniejące fizyczne linie, z dodatnimi końcami start <= end."
LIMIT_ERROR = "Nieprawidłowy limit lub wynik przekracza limit; niczego nie obcięto."
FILE_ERROR = "Ujawnienie wymaga obsługiwanego tekstowego pliku sekretów do 5 MiB (UTF-8/BOM/cp1250)."
ACCESS_ERROR = "Brak dostępu do zwykłego pliku w przypiętym folderze."
CONFLICT_ERROR = "Konflikt: plik, ścieżka lub dostęp zmieniły się od przygotowania."
DESCRIPTION_ERROR = "Nieprawidłowy opis: do 500 znaków, bez znaków sterujących i kierunkowych."
METADATA_ERROR = "Metadane ujawnienia przekraczają limit."
INTERNAL_ERROR = "Nie udało się przygotować ujawnienia (błąd wewnętrzny)."
QUEUE_ERROR = "Kolejka zadań jest pełna; nie zgłoszono ujawnienia."
STATE_ERROR = "Nie można zgłosić ujawnienia w obecnym stanie menedżera."
PANEL_ERROR = "Ujawnienie wymaga decyzji w panelu; zadanie odrzucono."
APPROVAL_REASON = (
    "Model prosi o ujawnienie wskazanej wartości lub jawnego zakresu. "
    "Po zgodzie wynik trafi do klienta MCP/modelu, nie do panelu. "
    "Serwer przechowuje go tylko w RAM przez maksymalnie 5 minut. "
    "Retencja po stronie klienta/modelu pozostaje poza kontrolą serwera."
)
WAIT_HINT = (
    "Zadanie czeka na decyzję użytkownika w panelu (zatwierdza użytkownik, nie ty). "
    "Zatrzymaj się i poczekaj na jego wiadomość; nie odpytuj wait_task w pętli. "
    "Po decyzji sprawdź get_task. Po błędzie : ping przed ponowieniem sprawdź "
    "list_tasks: zadanie mogło powstać."
)
_LINE_END = re.compile(r"\r\n|\r|\n")
_QUOTES = (chr(34), chr(39))


class SecretError(ValueError):
    """Wyłącznie stałe komunikaty tego modułu; bez fragmentów i surowych błędów."""


class _Cancelled(Exception):
    pass


@dataclass(frozen=True, repr=False)
class _Binding:
    filepath: str
    root: Root
    owner: Root
    path: Path
    relative: str


@dataclass(frozen=True, repr=False)
class _Selector:
    key: Optional[str]
    start_line: Optional[int]
    end_line: Optional[int]

    def public(self) -> Dict[str, Any]:
        if self.key is not None:
            return {"key": self.key}
        return {"start_line": self.start_line, "end_line": self.end_line}


@dataclass(frozen=True, repr=False)
class _Plan:
    binding: _Binding
    sha256: str
    identity: Tuple[int, int]
    mode: int
    selector: _Selector
    max_chars: int


def _error(message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"success": False, "error": message}
    if data is not None:
        result["data"] = data
    return result


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _selector(key: Any, start_line: Any, end_line: Any) -> _Selector:
    if key is not None:
        if start_line is not None or end_line is not None:
            raise SecretError(SELECTOR_ERROR)
        if (not isinstance(key, str) or not key or key != key.strip() or "=" in key
                or any(unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp") for ch in key)):
            raise SecretError(KEY_ERROR)
        return _Selector(key, None, None)
    if start_line is None or end_line is None:
        raise SecretError(SELECTOR_ERROR)
    if not _positive_int(start_line) or not _positive_int(end_line) or start_line > end_line:
        raise SecretError(RANGE_ERROR)
    return _Selector(None, start_line, end_line)


def _physical_lines(text: str):
    """Wyłącznie CRLF/LF/CR; form-feed i Unicode separatory nie tworzą linii."""
    lines = []
    start = 0
    for match in _LINE_END.finditer(text):
        lines.append(text[start:match.end()])
        start = match.end()
    if start < len(text):
        lines.append(text[start:])
    return lines


def _file_type(binding: _Binding) -> str:
    # Ani alias sekretnej nazwy do zwykłego celu, ani odwrotny alias nie rozszerza listy 5a.
    names = (binding.filepath, binding.relative, binding.path.name)
    if any(is_blocked_secret(name) or not is_secret_file(name) for name in names):
        raise SecretError(FILE_ERROR)
    name = binding.path.name.lower()
    if name.endswith((".pem", ".key")):
        return "range"
    return "env" if name == ".env" or name.startswith(".env.") else "ini"


def _key_value(text: str, key: str, kind: str) -> str:
    """Konserwatywne przypisania, bez parserów wykonujących interpolację/kod.

    Nie zgadujemy multiline/komentarzy. Niejednoznaczna składnia przypisania
    w pliku odrzuca key także poza wybranym kluczem (może ukrywać kontynuację).
    Jawny zakres pozostaje dostępny dla takich plików.
    """
    found = []
    for line in _physical_lines(text):
        physical_body = line.rstrip("\r\n")
        body = physical_body.strip()
        if not body or body.startswith(("#", ";")):
            continue
        if kind == "ini" and body.startswith("[") and body.endswith("]"):
            continue
        # INI może traktować wciętą linię z '=' jako kontynuację poprzedniej
        # wartości. Nie zgadujemy, nawet gdy wygląda jak nowe przypisanie.
        if kind == "ini" and physical_body != physical_body.lstrip():
            raise SecretError(KEY_ERROR)
        if kind == "env" and body.startswith("export") and len(body) > 6 and body[6].isspace():
            body = body[6:].lstrip()
        if "=" not in body:
            # Nieznana linia może być kontynuacją poprzedniej wartości.
            raise SecretError(KEY_ERROR)
        name, value = body.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name or (kind != "env" and name.startswith("export ")):
            raise SecretError(KEY_ERROR)
        if value.startswith(_QUOTES):
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise SecretError(KEY_ERROR)
            inner = value[1:-1]
            # Wewnętrzny delimiter/escape delimitera wymaga interpretacji: nie zgadujemy.
            if quote in inner or inner.endswith(chr(92)):
                raise SecretError(KEY_ERROR)
            value = inner
        elif (value.endswith(chr(92)) or any(q in value for q in _QUOTES)
              or "#" in value or ";" in value):
            raise SecretError(KEY_ERROR)
        if name == key:
            found.append(value)
    if len(found) != 1:
        raise SecretError(KEY_ERROR)
    return found[0]


def _extract(snapshot: FileSnapshot, selector: _Selector, kind: str, limit: int) -> str:
    # NUL wykrywa read_snapshot; BOM odrzucamy jawnie nawet bez NUL w body.
    if snapshot.raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE,
                                codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        raise SecretError(FILE_ERROR)
    if selector.key is not None:
        if kind == "range":
            raise SecretError(KEY_ERROR)
        content = _key_value(snapshot.text, selector.key, kind)
    else:
        lines = _physical_lines(snapshot.text)
        if selector.end_line > len(lines):
            raise SecretError(RANGE_ERROR)
        content = "".join(lines[selector.start_line - 1:selector.end_line])
    if len(content) > limit or len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise SecretError(LIMIT_ERROR)
    return content


def _paths(registry: RootsRegistry, binding: _Binding) -> None:
    try:
        current = resolve_path(registry, binding.filepath, binding.root.alias, write=False)
        if (current.path != binding.path or current.relative != binding.relative
                or (current.root.alias, current.root.path, current.root.mode)
                != (binding.root.alias, binding.root.path, binding.root.mode)
                or (current.owner.alias, current.owner.path, current.owner.mode)
                != (binding.owner.alias, binding.owner.path, binding.owner.mode)):
            raise SecretError(CONFLICT_ERROR)
    except SecretError:
        raise
    except Exception:
        raise SecretError(ACCESS_ERROR) from None


def _read_access(binding: _Binding) -> None:
    """Otwiera bez czytania treści; nie zachowuje uchwytu ani snapshotu."""
    try:
        before = binding.path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise SecretError(ACCESS_ERROR)
        with binding.path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
                raise SecretError(ACCESS_ERROR)
        after = binding.path.lstat()
        if (not stat.S_ISREG(after.st_mode)
                or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)):
            raise SecretError(ACCESS_ERROR)
    except SecretError:
        raise
    except Exception:
        raise SecretError(ACCESS_ERROR) from None


def _snapshot(registry: RootsRegistry, binding: _Binding) -> FileSnapshot:
    _paths(registry, binding)
    _read_access(binding)
    try:
        snapshot = read_snapshot(binding.path)
        info = binding.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != snapshot.identity
                or stat.S_IMODE(info.st_mode) != snapshot.mode):
            raise SecretError(CONFLICT_ERROR)
    except SecretError:
        raise
    except Exception:
        raise SecretError(FILE_ERROR) from None
    _paths(registry, binding)
    return snapshot


def _validated_snapshot(registry: RootsRegistry, plan: _Plan) -> FileSnapshot:
    snapshot = _snapshot(registry, plan.binding)
    if (snapshot.sha256 != plan.sha256 or snapshot.identity != plan.identity
            or snapshot.mode != plan.mode):
        raise SecretError(CONFLICT_ERROR)
    return snapshot


def _prepare(registry: RootsRegistry, config_lock: Any, filepath: Any, root: Any,
             selector: _Selector, limit: int) -> _Plan:
    with WRITE_LOCK:
        with config_lock:
            try:
                resolved = resolve_path(registry, filepath, root, write=False)
            except Exception:
                raise SecretError(ACCESS_ERROR) from None
            binding = _Binding(filepath, resolved.root, resolved.owner, resolved.path, resolved.relative)
            kind = _file_type(binding)
            snapshot = _snapshot(registry, binding)
            _extract(snapshot, selector, kind, limit)  # chwilowy wynik, nie część planu/domknięcia
            return _Plan(binding, snapshot.sha256, snapshot.identity, snapshot.mode, selector, limit)


def _verify(registry: RootsRegistry, config_lock: Any, plan: _Plan) -> Optional[str]:
    try:
        with WRITE_LOCK:
            with config_lock:
                _validated_snapshot(registry, plan)
        return None
    except Exception:
        return CONFLICT_ERROR


def _make_access_check(registry: RootsRegistry, config_lock: Any, binding: _Binding):
    """Domknięcie wyłącznie binding/dependencies, bez planu/hashu/sekretu."""
    def access_check() -> Optional[str]:
        try:
            with WRITE_LOCK:
                with config_lock:
                    _paths(registry, binding)
                    _read_access(binding)
                    _paths(registry, binding)
            return None
        except Exception:
            return ACCESS_ERROR
    return access_check


def _cancel(event: threading.Event) -> None:
    if event.is_set():
        raise _Cancelled()


def _acquire(lock: Any, event: threading.Event) -> None:
    while True:
        _cancel(event)
        if lock.acquire(timeout=0.05):
            return


def _make_executor(registry: RootsRegistry, config_lock: Any, plan: _Plan):
    def executor(event: threading.Event) -> Union[VolatileResult, Dict[str, Any]]:
        write_locked = config_locked = False
        release_failed = False
        outcome: Union[VolatileResult, Dict[str, Any]] = {
            "started": False, "error": CONFLICT_ERROR,
        }
        try:
            _acquire(WRITE_LOCK, event)
            write_locked = True
            _acquire(config_lock, event)
            config_locked = True
            _cancel(event)
            snapshot = _validated_snapshot(registry, plan)
            _cancel(event)
            content = _extract(snapshot, plan.selector, _file_type(plan.binding), plan.max_chars)
            _paths(registry, plan.binding)
            _read_access(plan.binding)
            _paths(registry, plan.binding)
            _cancel(event)
            data = {
                "content": content, "root": plan.binding.root.alias,
                "filepath": plan.binding.relative, "selector": plan.selector.public(),
                "encoding": snapshot.encoding, "bom": bool(snapshot.bom), "eol": snapshot.eol,
                "max_chars": plan.max_chars,
            }
            outcome = VolatileResult(data, _make_access_check(registry, config_lock, plan.binding))
        except _Cancelled:
            outcome = {"cancelled": True}
        except Exception:
            outcome = {"started": False, "error": CONFLICT_ERROR}
        finally:
            # Próbujemy zwolnić oba locki; błąd odrzuca wynik bez surowego wyjątku.
            try:
                if config_locked:
                    config_lock.release()
            except Exception:
                release_failed = True
            finally:
                try:
                    if write_locked:
                        WRITE_LOCK.release()
                except Exception:
                    release_failed = True
        if release_failed:
            return {"started": False, "error": CONFLICT_ERROR}
        return outcome
    return executor


def _metadata_budget(plan: _Plan, description: str) -> None:
    # Konserwatywny zapas na strukturę/klucze payloadu i metadanych VolatileResult.
    strings = (plan.binding.root.alias, plan.binding.relative, description, plan.selector.key or "")
    if 2048 + sum(64 + len(value.encode("utf-8")) for value in strings) > MAX_METADATA_BYTES:
        raise SecretError(METADATA_ERROR)


def secret_reveal_impl(
    registry: RootsRegistry,
    manager: TaskManager,
    config_lock: Any,
    filepath: Any,
    key: Any = None,
    start_line: Any = None,
    end_line: Any = None,
    max_chars: Any = 12000,
    root: Any = None,
    description: Any = "",
) -> Dict[str, Any]:
    """Prepare PRZED submit; zawsze panel/sensitive/no-trust i bez wait.

    C3 dopiero podłączy get_task do oddzielnego kanału pamięciowego.
    Żaden komunikat tego modułu nie używa str(exc) ani fragmentu pliku.
    """
    try:
        if type(max_chars) is not int or not 1 <= max_chars <= MAX_CONTENT_CHARS:
            raise SecretError(LIMIT_ERROR)
        selector = _selector(key, start_line, end_line)
        try:
            text = check_description(description)
        except Exception:
            raise SecretError(DESCRIPTION_ERROR) from None
        plan = _prepare(registry, config_lock, filepath, root, selector, max_chars)
        _metadata_budget(plan, text)
        payload = {
            "root": plan.binding.root.alias, "filepath": plan.binding.relative,
            "selector": selector.public(), "max_chars": max_chars, "description": text,
        }
        fields = dict(
            kind=KIND, sensitive=True, offer_trust=False, title="Ujawnij wskazany sekret",
            payload=payload, approval_reason=APPROVAL_REASON, timeout=TASK_TIMEOUT,
        )
        try:
            submitted = manager.submit(
                fields, _make_executor(registry, config_lock, plan), needs_approval=True,
                verify=lambda: _verify(registry, config_lock, plan),
            )
        except QueueFullError:
            return _error(QUEUE_ERROR)
        except TaskStateError:
            return _error(STATE_ERROR)
        task = manager.get(submitted.id) or submitted
        if task.status == TaskStatus.REJECTED:
            return _error(PANEL_ERROR, {"task_id": task.id, "status": task.status.value})
        return {"success": True, "data": {
            "task_id": task.id, "status": task.status.value, "kind": KIND,
            "sensitive": True, "hint": WAIT_HINT,
        }}
    except SecretError as exc:
        # Tylko nasz typ z własnymi stałymi; obce wyjątki nigdy nie trafiają do odpowiedzi.
        allowed = (SELECTOR_ERROR, KEY_ERROR, RANGE_ERROR, LIMIT_ERROR, FILE_ERROR,
                   ACCESS_ERROR, CONFLICT_ERROR, DESCRIPTION_ERROR, METADATA_ERROR)
        message = exc.args[0] if len(exc.args) == 1 and exc.args[0] in allowed else INTERNAL_ERROR
        return _error(message)
    except Exception:
        return _error(INTERNAL_ERROR)
