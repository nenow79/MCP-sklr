#!/usr/bin/env python3
"""Rejestr skills: wynik skanu + filtr wyłączonych, bezpieczny wątkowo (GUI i wątek MCP)."""
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from skills_engine.models import SOURCE_DEV, Diagnostic, ScanResult, SkillRecord
from skills_engine.scanner import scan_skills


@dataclass
class RegistrySnapshot:
    """Spójny odczyt stanu rejestru wykonany pod jedną blokadą."""
    active: List[SkillRecord] = field(default_factory=list)
    disabled: List[str] = field(default_factory=list)
    shadowed_count: int = 0
    diagnostics: List[Diagnostic] = field(default_factory=list)


class SkillRegistry:
    def __init__(
        self,
        central_dir: Optional[Path] = None,
        project_root: Optional[Path] = None,
        disabled: Iterable[str] = (),
        dev_dirs: Iterable[Path] = (),
        dev_enabled: Iterable[str] = (),
    ) -> None:
        self._lock = threading.RLock()
        self._central_dir = central_dir
        self._project_root = project_root
        self._disabled = set(disabled)
        self._dev_dirs = tuple(Path(p).expanduser() for p in dev_dirs)
        self._dev_enabled = set(dev_enabled)
        self._result = ScanResult()
        self._change_listeners = []

    def add_change_listener(self, listener):
        """Leaf generation invalidators only, no task/session control callbacks."""
        with self._lock:
            self._change_listeners.append(listener)

    def _set(self, name, value):
        with self._lock:
            if getattr(self, name) != value:
                setattr(self, name, value)
                for listener in self._change_listeners:
                    listener()

    # --- konfiguracja ---
    def set_central_dir(self, central_dir: Optional[Path]) -> None:
        self._set('_central_dir', central_dir)

    def set_project_root(self, project_root: Optional[Path]) -> None:
        self._set('_project_root', project_root)

    def set_disabled(self, disabled: Iterable[str]) -> None:
        self._set('_disabled', set(disabled))

    def set_dev_dirs(self, dev_dirs: Iterable[Path]) -> None:
        self._set('_dev_dirs', tuple(Path(p).expanduser() for p in dev_dirs))

    def set_dev_enabled(self, enabled: Iterable[str]) -> None:
        self._set('_dev_enabled', set(enabled))

    def _enabled(self, record: SkillRecord) -> bool:
        # Wywoływane tylko pod self._lock. Flagi dev są niezależne od central/project.
        if record.source == SOURCE_DEV:
            return record.name in self._dev_enabled
        return record.name not in self._disabled

    # --- skan ---
    def rescan(self) -> ScanResult:
        with self._lock:
            central_dir, project_root = self._central_dir, self._project_root
            dev_dirs = self._dev_dirs
        result = scan_skills(central_dir, project_root, dev_dirs)  # I/O poza blokadą
        with self._lock:
            self._result = result
        return result

    def scan_copy(self):
        """Detached scan settings; slow panel scans never publish into this registry."""
        with self._lock:
            return SkillRegistry(self._central_dir, self._project_root, self._disabled,
                                 self._dev_dirs, self._dev_enabled)

    # --- odczyt ---
    def is_disabled(self, name: str) -> bool:
        with self._lock:
            record = self._result.skills.get(name)
            if record is not None:
                return not self._enabled(record)
            return name in self._disabled

    def get(self, name: str) -> Optional[SkillRecord]:
        """Zwraca aktywny skill; None gdy nie istnieje albo jest wyłączony."""
        with self._lock:
            record = self._result.skills.get(name)
            return record if record is not None and self._enabled(record) else None

    def list_active(self) -> List[SkillRecord]:
        with self._lock:
            return sorted(
                (s for s in self._result.skills.values() if self._enabled(s)),
                key=lambda s: s.name,
            )

    def names(self) -> List[str]:
        return [skill.name for skill in self.list_active()]

    def list_all(self) -> List[dict]:
        """Lista administracyjna: rekordy zwycięskie i przesłonięte, także wyłączone."""
        with self._lock:
            entries = [
                {"record": s, "enabled": self._enabled(s), "shadowed": False}
                for s in self._result.skills.values()
            ]
            entries.extend(
                {"record": s, "enabled": False, "shadowed": True}
                for s in self._result.shadowed
            )
            return sorted(entries, key=lambda e: (
                e["record"].name, e["shadowed"], e["record"].source,
                str(e["record"].location)))

    def snapshot(self) -> RegistrySnapshot:
        with self._lock:
            return RegistrySnapshot(
                active=sorted(
                    (s for s in self._result.skills.values() if self._enabled(s)),
                    key=lambda s: s.name,
                ),
                disabled=sorted(s.name for s in self._result.skills.values()
                                if not self._enabled(s)),
                shadowed_count=len(self._result.shadowed),
                diagnostics=list(self._result.diagnostics),
            )
