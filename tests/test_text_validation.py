"""Preserve autopilot text validation after removing the checkpoint module."""
import pytest

from server.text_validation import TextRejected, _clean, _has_secret


@pytest.mark.parametrize('value,limit,code', [
    (None, 500, 'invalid_shape'), ('  ', 500, 'empty'),
    ('x' * 501, 500, 'too_long'), ('x' * 6001, 6000, 'too_long'),
    ('bad\u202etext', 500, 'control_chars'), ('bad\x00text', 500, 'control_chars'),
    ('API_TOKEN=private', 500, 'secret_detected'),
])
def test_rejected_input_keeps_fixed_code(value, limit, code):
    with pytest.raises(TextRejected) as error:
        _clean(value, limit)
    assert error.value.code == code and str(error.value) == code
    assert 'private' not in repr(error.value)


def test_trimming_limits_multiline_and_empty_option():
    assert _clean(' x\ny ', 500) == 'x\ny'
    assert _clean(' x ', 1) == 'x'
    assert _clean('x' * 6000, 6000) == 'x' * 6000
    assert _clean(' ', 1, allow_empty=True) == ''
    assert _has_secret('API_TOKEN=private')
    assert not _has_secret('Read current requirements')
