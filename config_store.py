#!/usr/bin/env python3
"""ConfigStore: jedyna ścieżka zapisu config.json (wspólny lock, zapis atomowy, wycofanie przy błędzie).

Słownik konfiguracji jest współdzielony (np. z TrustStore), więc wszystkie zmiany i zapisy muszą
korzystać z tego samego locka. Lock jest reentrantny: TrustStore trzyma go podczas wołania save().
"""
import copy
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from state import save_config


class ConfigStore:
    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None) -> None:
        self._data = data
        self._path = Path(path) if path else None
        self._lock = threading.RLock()
        self._change_listeners = []

    def add_change_listener(self, listener) -> None:
        """Leaf invalidators only; called under config lock, never controllers."""
        with self._lock:
            self._change_listeners.append(listener)

    def _changed(self) -> None:
        for listener in self._change_listeners:
            listener()

    @property
    def lock(self) -> "threading.RLock":
        return self._lock

    @property
    def data(self) -> Dict[str, Any]:
        """Żywy słownik konfiguracji. Zmiany tylko przez update() albo pod self.lock."""
        return self._data

    def snapshot(self) -> Dict[str, Any]:
        """Głęboka kopia konfiguracji (bezpieczna do iteracji poza lockiem)."""
        with self._lock:
            return copy.deepcopy(self._data)

    def save(self) -> None:
        with self._lock:
            # Invalidate even if persistence is uncertain or a legacy writer
            # later restores RAM. Never resurrect authorization after failure.
            self._changed()
            save_config(self._data, self._path)

    def update(self, mutator: Callable[[Dict[str, Any]], Any]) -> Any:
        """Wykonuje mutator pod lockiem i zapisuje plik.

        Gdy mutator albo zapis zgłosi wyjątek, stan w pamięci wraca do poprzedniego
        (w miejscu, bo inne obiekty trzymają referencję do tego samego słownika).
        """
        with self._lock:
            before = copy.deepcopy(self._data)
            try:
                result = mutator(self._data)
                if self._data != before:
                    self._changed()
                save_config(self._data, self._path)
            except BaseException:
                self._data.clear()
                self._data.update(before)
                raise
            return result
