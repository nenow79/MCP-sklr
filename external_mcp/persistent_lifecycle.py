"""Internal lifecycle worker; no import-time thread or transport startup.

Callbacks enqueue bounded RAM notifications only. No control/store lock is
acquired by the worker; supervisor invalidation is generation-pinned. Overflow
or worker failure closes the local admission latch instead of dropping a grant.
This module alone does not provide synchronized spawn admission.
"""
import queue
import threading
from contextlib import contextmanager

from external_mcp.profiles import connection_digest


class PersistentLifecycle:
    def __init__(self, supervisor, *, name, limit=64):
        self.supervisor = supervisor
        self._lock = threading.RLock()
        self._pins = {}
        self._pending = set()
        self._queue = queue.Queue(maxsize=limit)
        self._stop = threading.Event()
        self._closed = False
        self._failed = False
        self._bound = False
        self.thread = None
        self._name = name

    @property
    def blocked(self):
        with self._lock:
            return self._closed or self._failed

    def set_supervisor(self, supervisor):
        """Injection is allowed only before the first persistent operation."""
        with self._lock:
            if self._bound or self.thread is not None or self._closed or self._failed:
                raise RuntimeError('Lifecycle already bound.')
            self.supervisor = supervisor

    def bind(self):
        """Seal injection before the first operation, without starting a thread."""
        with self._lock:
            if self._closed or self._failed or self.supervisor is None:
                raise RuntimeError('Lifecycle unavailable.')
            self._bound = True

    @contextmanager
    def discovery_admission(self):
        """Short RAM gate before the supervisor's spawn/catalog admission."""
        with self._lock:
            if self._closed or self._failed:
                raise RuntimeError('Lifecycle unavailable.')
            yield

    @contextmanager
    def admission(self, owner, profile_id, generation):
        """RAM gate retained through local commit, never through transport I/O."""
        with self._lock:
            key = (owner, profile_id, generation)
            if (self._closed or self._failed or key not in self._pins
                    or key in self._pending):
                raise RuntimeError('Lifecycle unavailable.')
            yield

    def track(self, owner, profile, generation):
        if not isinstance(generation, str) or not generation:
            raise ValueError('Invalid lifecycle generation.')
        with self._lock:
            if self._closed or self._failed:
                raise RuntimeError('Lifecycle unavailable.')
            key = (owner, profile['id'], generation)
            if key in self._pending:
                raise RuntimeError('Lifecycle generation retired.')
            # Only one runtime can exist per owner/profile. Retain queued old
            # generations until consumed, but do not accumulate idle history.
            for old in tuple(self._pins):
                if old[:2] == key[:2] and old != key and old not in self._pending:
                    del self._pins[old]
            self._pins[key] = connection_digest(profile)
            if self.thread is None:
                self.thread = threading.Thread(target=self._run,
                    name=self._name, daemon=True)
                self.thread.start()

    def _enqueue(self, key, reason):
        # Caller owns only the private RAM lock, never supervisor/control locks.
        if key in self._pending or self._closed:
            return
        try:
            self._queue.put_nowait((key, reason))
            self._pending.add(key)
        except queue.Full:
            self._failed = True
            raise RuntimeError('Lifecycle queue unavailable.') from None

    def session_changed(self, owner, reason):
        with self._lock:
            for key in tuple(self._pins):
                if key[0] == owner:
                    self._enqueue(key, reason)

    def config_changed(self, profiles):
        """Detached connection fingerprints only; no configuration retained."""
        with self._lock:
            for key, digest in tuple(self._pins.items()):
                if profiles.get(key[1]) != digest:
                    self._enqueue(key, 'profile_changed')

    def _run(self):
        while True:
            queued = True
            try:
                key, reason = self._queue.get(timeout=0.05)
            except queue.Empty:
                with self._lock:
                    unqueued = next((pin for pin in self._pins
                                     if pin not in self._pending), None)
                    if (self._closed or self._failed) and unqueued is not None:
                        # Overflow must not lose the pin that did not fit the
                        # queue. Failure closes admission and drains all pins.
                        key = unqueued
                        reason = 'shutdown'
                        self._pending.add(key)
                        queued = False
                    elif self._stop.is_set() and not self._pins and self._queue.empty():
                        return
                    else:
                        continue
            try:
                self.supervisor.invalidate_generation(owner=key[0],
                    profile_id=key[1], expected_runtime_generation=key[2],
                    reason=reason)
            except Exception:
                with self._lock:
                    self._failed = True
            finally:
                with self._lock:
                    self._pending.discard(key)
                    self._pins.pop(key, None)
                if queued:
                    self._queue.task_done()

    def close(self, timeout=5):
        with self._lock:
            self._closed = True
            self._stop.set()
            thread = self.thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout=timeout)
        return not thread.is_alive()
