"""Internal lifecycle worker; no import-time thread or transport startup.

Callbacks enqueue bounded RAM notifications only. No control/store lock is
acquired by the worker; supervisor invalidation is generation-pinned. Overflow
or worker failure closes the local admission latch instead of dropping a grant.
This module alone does not provide synchronized spawn admission.
"""
import queue
import threading

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
        self.thread = None
        self._name = name

    @property
    def blocked(self):
        with self._lock:
            return self._closed or self._failed

    def track(self, owner, profile, generation):
        if not isinstance(generation, str) or not generation:
            raise ValueError('Invalid lifecycle generation.')
        with self._lock:
            if self._closed or self._failed:
                raise RuntimeError('Lifecycle unavailable.')
            key = (owner, profile['id'], generation)
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
        while not self._stop.is_set() or not self._queue.empty():
            try:
                key, reason = self._queue.get(timeout=0.05)
            except queue.Empty:
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
