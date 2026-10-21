"""Detached P3a RAM-only checkpoint store. Never writes to disk or logs content.

One entry per session, last write wins within a pause. The store is opaque to
the checkpoint type and knows nothing about pause rules: the controller owns
state, revision and persistence ordering. A restart empties it by design.
"""
import threading
from dataclasses import dataclass, field

from tasks.session_models import positive_int, session_id
from tasks.store import now_iso

MAX_ENTRIES = 8


@dataclass(frozen=True)
class StoredCheckpoint:
    session_id: str
    revision: int
    saved_at: str
    checkpoint: object = field(repr=False)


class CheckpointStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._items = {}

    def put(self, sid, revision, checkpoint):
        session_id(sid)
        positive_int(revision)
        if checkpoint is None:
            raise ValueError('Checkpoint is required.')
        with self._lock:
            if sid not in self._items and len(self._items) >= MAX_ENTRIES:
                raise ValueError('Too many checkpoints.')
            item = StoredCheckpoint(sid, revision, now_iso(), checkpoint)
            self._items[sid] = item
            return item

    def get(self, sid):
        session_id(sid)
        with self._lock:
            return self._items.get(sid)

    def discard(self, sid):
        session_id(sid)
        with self._lock:
            return self._items.pop(sid, None) is not None

    def clear(self):
        with self._lock:
            self._items.clear()

    def __len__(self):
        with self._lock:
            return len(self._items)
