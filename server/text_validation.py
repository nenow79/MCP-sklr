"""Bounded text validation shared by autopilot input checks. No I/O."""
import unicodedata

from server.secrets_mask import mask_text


class TextRejected(ValueError):
    """Fixed code only; rejected content is never returned."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _has_secret(text):
    # .env: NAME=value lines and URL credentials; .pem: private key blocks.
    return mask_text('.env', text).count > 0 or mask_text('x.pem', text).count > 0


def _clean(value, limit, allow_empty=False):
    if type(value) is not str:
        raise TextRejected('invalid_shape')
    text = value.strip()
    if len(text) > limit:
        raise TextRejected('too_long')
    for ch in text:
        category = unicodedata.category(ch)
        if ch == '\n':
            continue
        if category in ('Cc', 'Cf', 'Zl', 'Zp', 'Cs'):
            raise TextRejected('control_chars')
    if not text and not allow_empty:
        raise TextRejected('empty')
    if text and _has_secret(text):
        raise TextRejected('secret_detected')
    return text
