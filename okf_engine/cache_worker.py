"""Niezależny worker partiami; nie uruchamia się przy imporcie.

Callbacks są prywatne. Wynik step to (done, cursor); cursor nie jest logowany.
Ticket sprawdzany przez valid() musi być ponownie sprawdzony przez adapter
przed commit/publikacją. Worker sam nie zapewnia atomowości callback + config.
Brak auto-retry błędów i brak twardego przerwania blokującego I/O.
"""
import threading
from collections import OrderedDict
from dataclasses import dataclass, field


class WorkerError(ValueError):
    pass


@dataclass(frozen=True)
class WorkItem:
    key: str
    ticket: int
    cursor: object = field(default=None, repr=False)


class CacheWorker:
    """Jeden wątek właściciela zasobów. Jawne start/close, brak daemon.

    factory() i dispose(resource) działają w tym samym wątku co step.
    step(resource, item, valid) przetwarza jedną ograniczoną partię.
    Zgłoszenie tego samego key zastępuje stary cursor i unieważnia stary ticket.
    Przepełnienie ustawia reconcile_required; adapter musi zaplanować uzgodnienie.
    """
    def __init__(self, factory, step, dispose, *, capacity=256):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 5000:
            raise WorkerError('WORKER_CAPACITY_INVALID')
        self._factory, self._step, self._dispose = factory, step, dispose
        self._capacity = capacity
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._tickets = {}
        self._sequence = 0
        self._active = None
        self._thread = None
        self._stopping = False
        self._failed = False
        self._error = None
        self._dirty = False
        self._completed = 0

    def start(self):
        with self._condition:
            if self._stopping or self._failed:
                raise WorkerError('WORKER_CLOSED')
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='knowledge-worker', daemon=False)
                self._thread.start()

    def submit(self, key):
        if not isinstance(key, str) or not key or len(key) > 512 or any(ord(c) < 32 for c in key):
            raise WorkerError('WORKER_KEY_INVALID')
        with self._condition:
            if self._thread is None or self._stopping or self._failed:
                raise WorkerError('WORKER_UNAVAILABLE')
            if key not in self._tickets and len(self._tickets) >= self._capacity:
                self._dirty = True
                raise WorkerError('WORKER_QUEUE_FULL')
            self._sequence += 1
            ticket = self._sequence
            self._tickets[key] = ticket
            self._pending[key] = WorkItem(key, ticket)
            self._condition.notify_all()
            return ticket

    def cancel(self, key):
        with self._condition:
            found = key in self._tickets
            self._tickets.pop(key, None)
            self._pending.pop(key, None)
            self._condition.notify_all()
            return found

    def _valid(self, item):
        with self._condition:
            return not self._stopping and self._tickets.get(item.key) == item.ticket

    def status(self):
        with self._condition:
            return {'pending': len(self._pending), 'active': self._active is not None,
                    'completed': self._completed, 'reconcile_required': self._dirty,
                    'error': self._error, 'stopping': self._stopping,
                    'alive': self._thread is not None and self._thread.is_alive()}

    def close(self, timeout=5):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 <= timeout <= 30:
            raise WorkerError('WORKER_TIMEOUT_INVALID')
        with self._condition:
            self._stopping = True
            self._pending.clear()
            self._tickets.clear()
            thread = self._thread
            self._condition.notify_all()
        if thread is not None:
            if thread is threading.current_thread():
                raise WorkerError('WORKER_SELF_JOIN')
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def _run(self):
        resource = None
        opened = False
        try:
            resource = self._factory()
            opened = True
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._stopping or bool(self._pending))
                    if self._stopping:
                        break
                    _, item = self._pending.popitem(last=False)
                    self._active = item
                try:
                    result = self._step(resource, item, lambda: self._valid(item))
                    if not isinstance(result, tuple) or len(result) != 2 or not isinstance(result[0], bool):
                        raise WorkerError('WORKER_RESULT_INVALID')
                    done, cursor = result
                    with self._condition:
                        if self._valid(item):
                            if done:
                                self._tickets.pop(item.key, None)
                                self._completed += 1
                            else:
                                self._pending[item.key] = WorkItem(item.key, item.ticket, cursor)
                except Exception:
                    # Niestety wyjątki callbacków mogą zawierać treści; żadnego loggera.
                    with self._condition:
                        if self._tickets.get(item.key) == item.ticket:
                            self._tickets.pop(item.key, None)
                        self._dirty = True
                        self._error = 'WORKER_STEP_FAILED'
                finally:
                    with self._condition:
                        self._active = None
                        self._condition.notify_all()
        except Exception:
            with self._condition:
                self._failed = True
                self._dirty = True
                self._error = 'WORKER_START_FAILED'
                self._pending.clear()
                self._tickets.clear()
        finally:
            if opened:
                try:
                    self._dispose(resource)
                except Exception:
                    with self._condition:
                        self._dirty = True
                        self._error = 'WORKER_CLOSE_FAILED'
