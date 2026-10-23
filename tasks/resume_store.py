"""Detached single-instance resume preparation in RAM. No SQLite or dispatch.

Only the future controller may call prepare/confirm under its lock after
checking panel authorization, revision, pause and old-task cancellation.
Confirmed here means a RAM plan was recorded, NOT that execution is authorized.
"""
from dataclasses import dataclass, field, replace
import secrets
import threading

from server.session_resume import ResumeText
from tasks.session_models import SessionConflict, positive_int, session_id


@dataclass(frozen=True)
class ResumePreparation:
    session_id: str
    revision: int
    token: str = field(repr=False)
    supplement: ResumeText = field(repr=False)
    plan: object = field(default=None, repr=False)
    contract_binding: object = field(default=None, repr=False)

    def to_context(self):
        # Confidential response only. Fresh containers, no mutable aliases.
        return {
            'revision': self.revision, 'preparation_token': self.token,
            'supplement': self.supplement.text,
        }


class ResumeStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._item = None

    def prepare(self, sid, revision, supplement, *, contract_binding=None):
        session_id(sid)
        positive_int(revision)
        if type(supplement) is not ResumeText:
            raise ValueError('Expected validated resume data.')
        with self._lock:
            if self._item is not None:
                raise SessionConflict('Resume preparation already exists.')
            if contract_binding is not None:
                from autopilot.models import ContractBinding
                if type(contract_binding) is not ContractBinding or contract_binding.session_id != sid:
                    raise ValueError('invalid_resume_contract')
            item = ResumePreparation(sid, revision, secrets.token_hex(32), supplement,
                                     contract_binding=contract_binding)
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
