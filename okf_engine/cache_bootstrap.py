"""Jawna inicjalizacja po starcie panelu; import nie tworzy plików.

Istniejąca baza bez klucza wymaga decyzji o odbudowie, nie cichej regeneracji.
Nie szyfrujemy SQLite. Windows chroni klucz DPAPI, POSIX trybem 0600.
"""
import os
import threading

from .cache_backend import CacheRefreshBackend
from .cache_key import create_cache_key, load_cache_key
from .cache_store import CacheStore, CacheError, default_cache_paths


class ReadyCacheBackend(CacheRefreshBackend):
    """Otwarcie bazy musi zakończyć się przed podłączeniem do serwisu."""
    def __init__(self, path, key, *, timeout=5):
        ready = threading.Event()
        failure = []
        # Bazowy backend uruchamia factory leniwie w wątku właściciela.
        # Podstawiamy wyłącznie konstruktor store, bez współdzielonego monkeypatch.
        from .cache_worker import CacheWorker
        self._key, self._batch_size = key, 10
        self._lock = threading.Lock()
        self._jobs = {}
        self._sequence = 0

        def factory():
            try:
                return CacheStore(path)
            except Exception:
                failure.append(True)
                raise CacheError('CACHE_OPEN_FAILED') from None
            finally:
                ready.set()

        self._worker = CacheWorker(factory, self._step, lambda store: store.close())
        self._worker.start()
        if not ready.wait(timeout):
            self._worker.close(timeout=0)
            raise CacheError('CACHE_START_TIMEOUT')
        if failure:
            self._worker.close(timeout)
            raise CacheError('CACHE_OPEN_FAILED')


def open_application_cache(application_dir):
    """Pierwsze wywołanie może utworzyć klucz i bazę; tylko jawny bootstrap."""
    db, key_path = default_cache_paths(application_dir)
    if os.path.lexists(key_path):
        key = load_cache_key(key_path)
    else:
        artifacts = (db, db.with_name(db.name + '-wal'), db.with_name(db.name + '-shm'),
                     db.with_name(db.name + '-journal'))
        if any(os.path.lexists(path) for path in artifacts):
            raise CacheError('CACHE_KEY_MISSING')
        try:
            key = create_cache_key(key_path)
        except CacheError as exc:
            if str(exc) != 'CACHE_KEY_EXISTS':
                raise
            key = load_cache_key(key_path)
    return ReadyCacheBackend(db, key)
