#!/usr/bin/env python3
"""Poufne wyniki tylko w pamięci procesu, nigdy jako pola Task.

Usunięcie referencji nie gwarantuje wyzerowania RAM ani ochrony przed swapem.
Dostawca callbacku musi sprawdzać dostęp bez ujawniania surowych wyjątków.
Magazyn nie loguje zawartości, callbacków ani wyjątków.
"""
import copy
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

TTL_SECONDS = 300.0
MAX_RESULTS = 50
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_CONTENT_CHARS = 80000
MAX_CONTENT_BYTES = 320000
MAX_METADATA_BYTES = 16 * 1024
SWEEP_SECONDS = 15.0

AccessCheck = Callable[[], Optional[str]]


class VolatileResultError(ValueError):
    """Wyłącznie stały komunikat; bez treści wyniku i surowych wyjątków."""


@dataclass(frozen=True, repr=False)
class VolatileResult:
    """Oddzielny kontrakt executora; nie przekazywać do TaskStore/asdict/logów.

    data musi zawierać tekstowe content. Pozostałe pola to ograniczone
    metadane JSON, nie dodatkowe kanały dla treści sekretów.
    access_check: None oznacza dostęp; każdy inny wynik lub wyjątek odmawia.
    """
    data: Dict[str, Any]
    access_check: Optional[AccessCheck] = field(default=None, compare=False)


@dataclass(repr=False)
class _Entry:
    data: Dict[str, Any]
    size: int
    access_check: Optional[AccessCheck]
    token: object = field(default_factory=object)
    expires_at: Optional[float] = None


def _metadata_size(value: Any, depth: int = 0) -> int:
    """Ograniczony koszt metadanych, bez serializacji treści do JSON.

    Budżet obejmuje również koszt struktury i pustych elementów.
    """
    if depth > 4:
        raise VolatileResultError("Metadane wyniku przekraczają limit.")
    if value is None or isinstance(value, bool):
        return 16
    if isinstance(value, str):
        return 64 + len(value.encode("utf-8"))
    if isinstance(value, int) and not isinstance(value, bool):
        if value.bit_length() > 64:
            raise VolatileResultError("Nieprawidłowe metadane wyniku.")
        return 32
    if isinstance(value, (list, dict)):
        if len(value) > 256:
            raise VolatileResultError("Metadane wyniku przekraczają limit.")
        size = 64
        if isinstance(value, list):
            for item in value:
                size += _metadata_size(item, depth + 1)
                if size > MAX_METADATA_BYTES:
                    raise VolatileResultError("Metadane wyniku przekraczają limit.")
        else:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise VolatileResultError("Nieprawidłowe metadane wyniku.")
                size += _metadata_size(key, depth + 1) + _metadata_size(item, depth + 1)
                if size > MAX_METADATA_BYTES:
                    raise VolatileResultError("Metadane wyniku przekraczają limit.")
        return size
    raise VolatileResultError("Nieprawidłowe metadane wyniku.")


def _prepare(result: VolatileResult) -> _Entry:
    try:
        if not isinstance(result, VolatileResult) or type(result.data) is not dict:
            raise VolatileResultError("Nieprawidłowy wynik pamięciowy.")
        content = result.data.get("content")
        if not isinstance(content, str):
            raise VolatileResultError("Wynik pamięciowy wymaga tekstowego content.")
        if len(content) > MAX_CONTENT_CHARS:
            raise VolatileResultError("Treść wyniku przekracza limit; niczego nie obcięto.")
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > MAX_CONTENT_BYTES:
            raise VolatileResultError("Treść wyniku przekracza limit; niczego nie obcięto.")
        metadata = {key: value for key, value in result.data.items() if key != "content"}
        metadata_bytes = _metadata_size(metadata)
        if metadata_bytes > MAX_METADATA_BYTES:
            raise VolatileResultError("Metadane wyniku przekraczają limit.")
        if result.access_check is not None and not callable(result.access_check):
            raise VolatileResultError("Nieprawidłowa kontrola dostępu do wyniku.")
        return _Entry(copy.deepcopy(result.data), content_bytes + metadata_bytes,
                      result.access_check)
    except VolatileResultError:
        raise
    except Exception:
        raise VolatileResultError("Nie można przygotować wyniku pamięciowego.") from None


class VolatileResultStore:
    """Lock magazynu nigdy nie obejmuje callbacku kontroli dostępu.

    reserve tworzy niedostępny wpis; activate rozpoczyna TTL i udostępnia go.
    Właściciel musi discard rezerwację po każdym błędzie finalizacji zadania.
    Limit liczby i rozmiaru obejmuje również niedostępne rezerwacje.
    """
    def __init__(self, *, ttl_seconds: float = TTL_SECONDS,
                 max_results: int = MAX_RESULTS,
                 max_total_bytes: int = MAX_TOTAL_BYTES,
                 clock: Callable[[], float] = time.monotonic,
                 sweep_seconds: float = SWEEP_SECONDS):
        if (isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float))
                or not 0 < ttl_seconds <= TTL_SECONDS
                or isinstance(max_results, bool) or not isinstance(max_results, int)
                or not 1 <= max_results <= MAX_RESULTS
                or isinstance(max_total_bytes, bool) or not isinstance(max_total_bytes, int)
                or not 1 <= max_total_bytes <= MAX_TOTAL_BYTES
                or isinstance(sweep_seconds, bool) or not isinstance(sweep_seconds, (int, float))
                or not 0 < sweep_seconds <= SWEEP_SECONDS or not callable(clock)):
            raise VolatileResultError("Nieprawidłowe limity magazynu pamięciowego.")
        self._ttl = float(ttl_seconds)
        self._max_results = max_results
        self._max_bytes = max_total_bytes
        self._clock = clock
        self._interval = float(sweep_seconds)
        self._lock = threading.RLock()
        self._entries: Dict[int, _Entry] = {}
        self._bytes = 0
        self._closed = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _remove_locked(self, task_id: int) -> None:
        entry = self._entries.pop(task_id, None)
        if entry is not None:
            self._bytes -= entry.size

    def _sweep_locked(self) -> None:
        now = self._clock()
        expired = [task_id for task_id, entry in self._entries.items()
                   if entry.expires_at is not None and now >= entry.expires_at]
        for task_id in expired:
            self._remove_locked(task_id)

    def reserve(self, task_id: int, result: VolatileResult) -> object:
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1:
            raise VolatileResultError("Nieprawidłowy identyfikator wyniku pamięciowego.")
        entry = _prepare(result)
        with self._lock:
            if self._closed:
                raise VolatileResultError("Magazyn wyników pamięciowych jest zamknięty.")
            self._sweep_locked()
            if task_id in self._entries:
                raise VolatileResultError("Wynik pamięciowy tego zadania już istnieje.")
            if (len(self._entries) >= self._max_results
                    or self._bytes + entry.size > self._max_bytes):
                raise VolatileResultError("Magazyn wyników pamięciowych jest pełny.")
            self._entries[task_id] = entry
            self._bytes += entry.size
            return entry.token

    def activate(self, task_id: int, token: object) -> None:
        with self._lock:
            entry = self._entries.get(task_id)
            if self._closed or entry is None or entry.token is not token:
                raise VolatileResultError("Nie można opublikować wyniku pamięciowego.")
            if entry.expires_at is not None:
                raise VolatileResultError("Wynik pamięciowy został już opublikowany.")
            # Wątek startuje przed udostępnieniem: błąd startu nie publikuje wyniku.
            if self._thread is None:
                thread = threading.Thread(target=self._loop, daemon=True,
                                          name="volatile-result-expiry")
                thread.start()
                self._thread = thread
            entry.expires_at = self._clock() + self._ttl

    def discard(self, task_id: int, token: Optional[object] = None) -> None:
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is not None and (token is None or entry.token is token):
                self._remove_locked(task_id)

    def get(self, task_id: int) -> Dict[str, Any]:
        """Nie konsumuje wyniku. Dostęp/TTL sprawdzane także po callbacku.

        Zwraca stan available/unavailable/denied. Niedostępność obejmuje restart,
        wygaśnięcie i nieopublikowany wpis; nie utrzymujemy historii sekretów.
        """
        with self._lock:
            self._sweep_locked()
            entry = self._entries.get(task_id)
            if self._closed or entry is None or entry.expires_at is None:
                return {"state": "unavailable"}
            token, check = entry.token, entry.access_check
        denied = False
        if check is not None:
            try:
                denied = check() is not None
            except Exception:
                denied = True
        with self._lock:
            self._sweep_locked()
            current = self._entries.get(task_id)
            if self._closed or current is None or current.token is not token:
                return {"state": "unavailable"}
            if denied:
                self._remove_locked(task_id)
                return {"state": "denied"}
            return {"state": "available", "data": copy.deepcopy(current.data)}

    def sweep(self) -> None:
        with self._lock:
            self._sweep_locked()

    def close(self) -> None:
        """Czyści dane pod lockiem; join zawsze poza lockiem magazynu."""
        with self._lock:
            self._closed = True
            self._entries.clear()
            self._bytes = 0
            self._stop.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.sweep()
            except Exception:
                # Fail closed; bez logger.exception i bez treści wyjątku.
                self.close()
                return
