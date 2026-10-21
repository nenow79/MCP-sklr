"""Bounded resume text validation. No I/O, authorization or gate changes.

Secret detection is heuristic, not a guarantee. Text remains RAM-only.
"""
from dataclasses import dataclass, field
import unicodedata

from server.secrets_mask import mask_text

MAX_SUPPLEMENT = 3000
MAX_PLAN = 6000


class ResumeRejected(ValueError):
    """Fixed code only; never user/model text."""


@dataclass(frozen=True)
class ResumeText:
    text: str = field(repr=False)
    size: int


def _validate(value, limit, empty_allowed):
    if type(value) is not str:
        raise ResumeRejected('invalid_shape')
    # Bound raw input before trimming and scan controls before normalization.
    if len(value) > limit:
        raise ResumeRejected('too_long')
    if any(ch != '\n' and unicodedata.category(ch) in ('Cc', 'Cf', 'Cs', 'Zl', 'Zp')
           for ch in value):
        raise ResumeRejected('control_chars')
    text = value.strip()
    if not text and not empty_allowed:
        raise ResumeRejected('empty')
    if mask_text('.env', text).count or mask_text('resume.pem', text).count:
        raise ResumeRejected('secret_detected')
    return ResumeText(text, len(text))


def validate_supplement(value):
    """An empty supplement is allowed; missing context must be reported separately."""
    return _validate(value, MAX_SUPPLEMENT, True)


def validate_resume_plan(value):
    """Checks bounded syntax only, NOT semantic correctness of the proposed plan."""
    return _validate(value, MAX_PLAN, False)
