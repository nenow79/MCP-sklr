#!/usr/bin/env python3
"""Transakcje plikowe 5b: staging/os.replace per plik i obsługiwany rollback.

Nie jest to pełna atomowość wielu plików ani ochrona przed awarią procesu,
systemu lub zewnętrznymi zapisami w oknie TOCTOU. Temp zawiera rzeczywiste
bajty (także sekretów): tylko na czas wykonania, nigdy w wyniku/logach.
Nie sprzątamy cudzych plików ani pozostałości po restarcie na podstawie nazwy.
Lock: wspólny zapisów -> wstrzyknięty reentrantny ConfigStore.lock.
Wywoływać tylko po zatwierdzeniu; moduł nie podejmuje decyzji użytkownika.
"""
import hashlib
import os
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from roots_registry import RootsRegistry
from server.file_changes import PreparedChanges, PreparedFile, read_snapshot
from server.path_guard import resolve_path

WRITE_LOCK = threading.RLock()  # wspólny dla wszystkich instancji w procesie


class TransactionError(ValueError):
    """Wyłącznie stałe, bezpieczne komunikaty; bez treści plików/wyjątków I/O."""


class _Cancelled(Exception):
    pass


def _identity(info: os.stat_result) -> Tuple[int, int]:
    return info.st_dev, info.st_ino


@dataclass(repr=False)
class _Temp:
    path: Path
    identity: Tuple[int, int]
    parent_identity: Tuple[int, int]
    sha256: str


@dataclass(repr=False)
class _Staged:
    item: PreparedFile
    replacement: _Temp
    backup: Optional[_Temp]


class FileTransaction:
    def __init__(self, registry: RootsRegistry, config_lock: Any):
        """config_lock musi być tym samym RLock co w ConfigStore/RootsRegistry."""
        self.registry = registry
        self.config_lock = config_lock

    @staticmethod
    def _cancel(event: threading.Event) -> None:
        if event.is_set():
            raise _Cancelled()

    @staticmethod
    def _acquire(lock: Any, event: threading.Event) -> None:
        while True:
            FileTransaction._cancel(event)
            if lock.acquire(timeout=0.05):
                return

    def _paths(self, item: PreparedFile) -> None:
        for binding in item.bindings:
            try:
                resolved = resolve_path(self.registry, binding.filepath, binding.root.alias, write=True)
                same = (resolved.path == binding.path
                        and resolved.root.alias == binding.root.alias
                        and resolved.root.path == binding.root.path
                        and resolved.root.mode == binding.root.mode
                        and resolved.owner.alias == binding.owner.alias
                        and resolved.owner.path == binding.owner.path
                        and resolved.owner.mode == binding.owner.mode)
                if not same or not item.path.parent.is_dir():
                    raise TransactionError("Konflikt: zmieniono ścieżkę, root lub jego uprawnienia.")
            except TransactionError:
                raise
            except Exception:
                raise TransactionError("Konflikt: ścieżka lub root nie pozwala na zapis.") from None

    def _validate(self, item: PreparedFile, published: Optional[_Temp] = None) -> None:
        self._paths(item)
        try:
            info = item.path.lstat()
        except FileNotFoundError:
            if item.before is None and published is None:
                return
            raise TransactionError("Konflikt: plik zniknął.") from None
        if not stat.S_ISREG(info.st_mode):
            raise TransactionError("Konflikt: cel nie jest zwykłym plikiem.")
        if item.before is None and published is None:
            raise TransactionError("Konflikt: cel tworzenia już istnieje.")
        snapshot = read_snapshot(item.path)
        expected_hash = published.sha256 if published else item.before.sha256
        expected_identity = published.identity if published else item.before.identity
        if (snapshot.sha256 != expected_hash or snapshot.identity != expected_identity
                or _identity(info) != snapshot.identity):
            raise TransactionError("Konflikt: plik zmienił się; nie nadpisano zmiany zewnętrznej.")
        if published is None and snapshot.mode != item.before.mode:
            raise TransactionError("Konflikt: zmieniły się uprawnienia pliku.")

    def verify(self, plan: PreparedChanges) -> Optional[str]:
        """Callback kolejki po decyzji; execute i tak powtarza walidację pod lockiem."""
        try:
            with WRITE_LOCK:
                with self.config_lock:
                    for item in plan.files:
                        self._validate(item)
            return None
        except Exception:
            return "Konflikt: plik, ścieżka lub dostęp zmieniły się od przygotowania."

    def _temp_path(self, temp: _Temp) -> None:
        if (temp.path.parent.resolve() != temp.path.parent
                or _identity(temp.path.parent.stat()) != temp.parent_identity):
            raise TransactionError("Konflikt: zmieniono katalog pliku tymczasowego.")
        info = temp.path.lstat()
        if not stat.S_ISREG(info.st_mode) or _identity(info) != temp.identity:
            raise TransactionError("Konflikt: zmieniono plik tymczasowy.")

    def _check_temp(self, temp: _Temp, item: PreparedFile) -> None:
        self._temp_path(temp)
        binding = item.bindings[0]
        resolved = resolve_path(self.registry, str(temp.path), binding.root.alias, write=True)
        if resolved.path != temp.path:
            raise TransactionError("Konflikt: zmieniono ścieżkę stagingu.")
        snapshot = read_snapshot(temp.path)
        if snapshot.sha256 != temp.sha256 or snapshot.identity != temp.identity:
            raise TransactionError("Konflikt: zmieniono bajty stagingu.")

    def _stage(self, item: PreparedFile, raw: bytes, temps: List[_Temp]) -> _Temp:
        self._paths(item)
        parent = item.path.parent
        parent_identity = _identity(parent.stat())
        fd, name = tempfile.mkstemp(prefix=".sklr-", suffix=".tmp", dir=parent)
        path = Path(name)
        # Rejestracja przed zapisem: cleanup obejmuje także błąd write/fsync/chmod.
        temp = _Temp(path, _identity(os.fstat(fd)), parent_identity, hashlib.sha256(raw).hexdigest())
        temps.append(temp)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if item.before is not None:
            os.chmod(path, item.before.mode)
        self._check_temp(temp, item)
        return temp

    def _publish(self, staged: _Staged) -> None:
        self._check_temp(staged.replacement, staged.item)
        # Ostatnia walidacja tuż przed replace, nie tylko przy tworzeniu zadania.
        self._validate(staged.item)
        os.replace(staged.replacement.path, staged.item.path)

    def _rollback(self, published: List[_Staged]) -> bool:
        complete = True
        for staged in reversed(published):
            try:
                if staged.backup is not None:
                    self._check_temp(staged.backup, staged.item)
                # Nie przywracamy starej treści kosztem cudzej późniejszej zmiany.
                self._validate(staged.item, staged.replacement)
                if staged.backup is None:
                    staged.item.path.unlink()
                else:
                    os.replace(staged.backup.path, staged.item.path)
            except Exception:
                complete = False
        return complete

    def _cleanup(self, temps: List[_Temp]) -> bool:
        complete = True
        for temp in reversed(temps):
            try:
                # Temp może już być przeniesiony. Sprawdzamy rodzica nawet gdy nie istnieje.
                if (temp.path.parent.resolve() != temp.path.parent
                        or _identity(temp.path.parent.stat()) != temp.parent_identity):
                    complete = False
                    continue
                if not os.path.lexists(temp.path):
                    continue
                self._temp_path(temp)
                # Windows: kopia pliku RO mogła odziedziczyć bit read-only.
                os.chmod(temp.path, stat.S_IRUSR | stat.S_IWUSR)
                temp.path.unlink()
            except Exception:
                complete = False
        return complete

    def execute(self, plan: PreparedChanges, cancel_event: Optional[threading.Event] = None) -> Dict[str, Any]:
        """Bezpieczny wynik operacji, do adaptacji przez executor z paczki B.

        Bez stdout, hashy, bajtów, nazw temp i surowych wyjątków. Przy cleanup
        failure committed może pozostać True: nie ponawiać operacji w ciemno.
        """
        event = cancel_event if cancel_event is not None else threading.Event()
        temps: List[_Temp] = []
        published: List[_Staged] = []
        write_locked = config_locked = committed = cancelled = False
        rollback_complete = cleanup_complete = True
        error = ""
        try:
            self._acquire(WRITE_LOCK, event)
            write_locked = True
            self._acquire(self.config_lock, event)
            config_locked = True
            self._cancel(event)
            for item in plan.files:
                self._validate(item)
            staging = []
            for item in plan.files:
                self._cancel(event)
                if not item.changed:
                    continue
                replacement = self._stage(item, item.after, temps)
                self._cancel(event)
                backup = self._stage(item, item.before.raw, temps) if item.before else None
                staging.append(_Staged(item, replacement, backup))
            # Wszystkie cele zweryfikowane jeszcze raz przed pierwszą publikacją.
            for item in plan.files:
                self._cancel(event)
                self._validate(item)
            for staged in staging:
                self._cancel(event)
                self._publish(staged)
                published.append(staged)
                self._cancel(event)
            committed = True
        except _Cancelled:
            cancelled = True
            error = "Anulowano transakcję."
            rollback_complete = self._rollback(published)
        except Exception:
            # Nigdy nie propagujemy surowego wyjątku do logger.exception w TaskManager.
            error = "Transakcja przerwana: konflikt lub błąd operacji plikowej."
            rollback_complete = self._rollback(published)
        finally:
            cleanup_complete = self._cleanup(temps)
            if config_locked:
                self.config_lock.release()
            if write_locked:
                WRITE_LOCK.release()
        if not rollback_complete:
            error = "Niepełny rollback: część zmian mogła pozostać; wymagane sprawdzenie plików."
        if not cleanup_complete:
            error += (" " if error else "") + "Niepełne sprzątanie plików tymczasowych."
        return {
            "success": committed and cleanup_complete,
            "data": {"committed": committed, "cancelled": cancelled,
                     "rollback_complete": rollback_complete, "cleanup_complete": cleanup_complete,
                     "published_count": len(published)},
            **({"error": error} if error else {}),
        }
