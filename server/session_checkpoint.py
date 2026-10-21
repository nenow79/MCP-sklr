"""Detached P3a validation of a model-supplied session checkpoint. No I/O.

The checkpoint is structured, bounded data, never an authorization source.
Any violation rejects the WHOLE payload with a fixed code; rejected content
is never echoed, masked-and-kept, logged or stored. Secret detection reuses
the file-based heuristic of secrets_mask and is a safety net, not a guarantee:
the real protection is that content lives only in RAM and is never persisted.
"""
import unicodedata
from dataclasses import dataclass, field
from typing import Tuple

from server.secrets_mask import mask_text

MAX_GOAL = 500
MAX_STAGE = 200
MAX_NEXT_STEP = 500
MAX_FINDINGS = 20
MAX_FINDING_TEXT = 400
MAX_FINDING_SOURCE = 200
MAX_TASK_IDS = 100
MAX_QUESTIONS = 10
MAX_QUESTION = 300
MAX_UNCERTAIN = 10
MAX_UNCERTAIN_ITEM = 300
MAX_TOTAL_CHARS = 6000

ALLOWED_FIELDS = frozenset({
    'goal', 'stage', 'next_step', 'findings', 'done_task_ids', 'open_questions', 'uncertain'})


class CheckpointRejected(ValueError):
    """Fixed machine code only (str(exc) == code); never foreign content."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Finding:
    text: str = field(repr=False)
    source: str = field(repr=False)


@dataclass(frozen=True)
class Checkpoint:
    """Immutable; repr hides all content so accidental logging cannot leak it."""
    goal: str = field(repr=False)
    stage: str = field(repr=False)
    next_step: str = field(repr=False)
    findings: Tuple[Finding, ...] = field(repr=False)
    done_task_ids: Tuple[int, ...] = field(repr=False)
    open_questions: Tuple[str, ...] = field(repr=False)
    uncertain: Tuple[str, ...] = field(repr=False)
    size: int = 0

    def to_public(self):
        """Fresh plain structure for the user panel; mutating it changes nothing."""
        return {
            'goal': self.goal, 'stage': self.stage, 'next_step': self.next_step,
            'findings': [{'text': f.text, 'source': f.source} for f in self.findings],
            'done_task_ids': list(self.done_task_ids),
            'open_questions': list(self.open_questions),
            'uncertain': list(self.uncertain),
            'size': self.size,
        }


def _has_secret(text):
    # .env: NAME=value lines and URL credentials; .pem: private key blocks.
    return mask_text('.env', text).count > 0 or mask_text('x.pem', text).count > 0


def _clean(value, limit, allow_empty=False):
    if type(value) is not str:
        raise CheckpointRejected('invalid_shape')
    text = value.strip()
    if len(text) > limit:
        raise CheckpointRejected('too_long')
    for ch in text:
        category = unicodedata.category(ch)
        if ch == '\n':
            continue
        if category in ('Cc', 'Cf', 'Zl', 'Zp', 'Cs'):
            raise CheckpointRejected('control_chars')
    if not text and not allow_empty:
        raise CheckpointRejected('empty')
    if text and _has_secret(text):
        raise CheckpointRejected('secret_detected')
    return text


def _list(value, limit):
    if type(value) is not list:
        raise CheckpointRejected('invalid_shape')
    if len(value) > limit:
        raise CheckpointRejected('too_many')
    return value


def validate_checkpoint(payload, owns_task):
    """Return an immutable Checkpoint or raise CheckpointRejected(code).

    owns_task(task_id) -> bool must confirm the task is linked to THIS session;
    a model cannot cite invented or foreign task ids. A failing lookup rejects
    the payload instead of guessing.
    """
    if type(payload) is not dict or any(type(key) is not str for key in payload):
        raise CheckpointRejected('invalid_shape')
    if set(payload) - ALLOWED_FIELDS:
        raise CheckpointRejected('unknown_field')
    if 'goal' not in payload or 'next_step' not in payload:
        raise CheckpointRejected('missing_field')

    goal = _clean(payload['goal'], MAX_GOAL)
    next_step = _clean(payload['next_step'], MAX_NEXT_STEP)
    stage = _clean(payload.get('stage', ''), MAX_STAGE, allow_empty=True)

    findings = []
    for item in _list(payload.get('findings', []), MAX_FINDINGS):
        if type(item) is not dict or set(item) != {'text', 'source'}:
            raise CheckpointRejected('invalid_shape')
        findings.append(Finding(_clean(item['text'], MAX_FINDING_TEXT),
                                _clean(item['source'], MAX_FINDING_SOURCE)))

    questions = tuple(_clean(q, MAX_QUESTION)
                      for q in _list(payload.get('open_questions', []), MAX_QUESTIONS))
    uncertain = tuple(_clean(u, MAX_UNCERTAIN_ITEM)
                      for u in _list(payload.get('uncertain', []), MAX_UNCERTAIN))

    ids = _list(payload.get('done_task_ids', []), MAX_TASK_IDS)
    if any(type(i) is not int or i < 1 for i in ids) or len(set(ids)) != len(ids):
        raise CheckpointRejected('invalid_shape')
    for task_id in ids:
        try:
            owned = owns_task(task_id)
        except Exception:
            raise CheckpointRejected('link_check_failed') from None
        if owned is not True:
            raise CheckpointRejected('foreign_task')

    total = (len(goal) + len(stage) + len(next_step)
             + sum(len(f.text) + len(f.source) for f in findings)
             + sum(len(q) for q in questions) + sum(len(u) for u in uncertain))
    if total > MAX_TOTAL_CHARS:
        raise CheckpointRejected('too_long')
    return Checkpoint(goal, stage, next_step, tuple(findings), tuple(ids),
                      questions, uncertain, total)
