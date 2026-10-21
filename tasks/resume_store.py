"""Detached single-instance resume preparation in RAM. No SQLite or dispatch.

Only the future controller may call prepare/confirm under its lock after
checking panel authorization, revision, pause and old-task cancellation.
Confirmed here means a RAM plan was recorded, NOT that execution is authorized.
"""
from dataclasses import dataclass, field, replace
import secrets
import threading

from server.session_checkpoint import Checkpoint
from server.session_resume import ResumeText
from tasks.session_models import SessionConflict, positive_int, session_id


@dataclass(frozen=True)
class ResumePreparation:
    session_id: str
    revision: int
    token: str = field(repr=False)
    supplement: ResumeText = field(repr=False)
    checkpoint: object = field(repr=False)
    plan: object = field(default=None, repr=False)

    def to_context(self):
        # Confidential response only. Fresh containers, no mutable aliases.
        return {
            'revision': self.revision, 'preparation_token': self.token,
            'supplement': self.supplement.text,
            'checkpoint_missing': self.checkpoint is None,
            'checkpoint': self.checkpoint.to_public() if self.checkpoint else None,
        }


class ResumeStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._item = None

    def prepare(self, sid, revision, supplement, checkpoint=None):
        session_id(sid)
        positive_int(revision)
        if type(supplement) is not ResumeText or (
                checkpoint is not None and type(checkpoint) is not Checkpoint):
            raise ValueError('Expected validated resume data.')
        with self._lock:
            if self._item is not None:
                raise SessionConflict('Resume preparation already exists.')
            item = ResumePreparation(sid, revision, secrets.token_hex(32), supplement, checkpoint)
            self._item = item
            return item

    def get(self):
        with self._lock:
            return self._item

    def confirm(self, sid, revision, token, plan):
        session_id(sid)
        positive_int(revision)
        if type(token) is not str or type(plan) is not ResumeText:
            raise ValueError('Expected validated resume confirmation.')
        with self._lock:
            item = self._item
            if (item is None or item.session_id != sid or item.revision != revision
                    or item.plan is not None or not secrets.compare_digest(token.encode('utf-8'),
                                                                          item.token.encode('ascii'))):
                raise SessionConflict('Resume preparation is not current.')
            confirmed = replace(item, plan=plan)
            self._item = confirmed
            return confirmed

    def clear(self):
        with self._lock:
            self._item = None
