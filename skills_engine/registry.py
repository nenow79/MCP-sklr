#!/usr/bin/env python3
"""Rejestr skills: wynik skanu + filtr wyłączonych, bezpieczny wątkowo (GUI i wątek MCP)."""
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from skills_engine.models import Diagnostic, ScanResult, SkillRecord
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
    ) -> None:
        self._lock = threading.RLock()
        self._central_dir = central_dir
        self._project_root = project_root
        self._disabled = set(disabled)
        self._result = ScanResult()

    # --- konfiguracja ---
    def set_central_dir(self, central_dir: Optional[Path]) -> None:
        with self._lock:
            self._central_dir = central_dir

    def set_project_root(self, project_root: Optional[Path]) -> None:
        with self._lock:
            self._project_root = project_root

    def set_disabled(self, disabled: Iterable[str]) -> None:
        with self._lock:
            self._disabled = set(disabled)

    # --- skan ---
    def rescan(self) -> ScanResult:
        with self._lock:
            central_dir, project_root = self._central_dir, self._project_root
        result = scan_skills(central_dir, project_root)  # I/O poza blokadą
        with self._lock:
            self._result = result
        return result

    # --- odczyt ---
    def is_disabled(self, name: str) -> bool:
        with self._lock:
            return name in self._disabled

    def get(self, name: str) -> Optional[SkillRecord]:
        """Zwraca aktywny skill; None gdy nie istnieje albo jest wyłączony."""
        with self._lock:
            if name in self._disabled:
                return None
            return self._result.skills.get(name)

    def list_active(self) -> List[SkillRecord]:
        with self._lock:
            return sorted(
                (s for n, s in self._result.skills.items() if n not in self._disabled),
                key=lambda s: s.name,
            )

    def names(self) -> List[str]:
        return [skill.name for skill in self.list_active()]

    def snapshot(self) -> RegistrySnapshot:
        with self._lock:
            return RegistrySnapshot(
                active=sorted(
                    (s for n, s in self._result.skills.items() if n not in self._disabled),
                    key=lambda s: s.name,
                ),
                disabled=sorted(n for n in self._result.skills if n in self._disabled),
                shadowed_count=len(self._result.shadowed),
                diagnostics=list(self._result.diagnostics),
            )
