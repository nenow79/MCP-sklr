"""P3b1 pure text validation; no controller or main wiring."""
import pytest

from server.session_resume import (
    MAX_PLAN, MAX_SUPPLEMENT, ResumeRejected, validate_resume_plan, validate_supplement)


def test_normalization_and_empty_supplement():
    assert validate_supplement(' ').text == ''
    assert validate_resume_plan('  review\nthen test  ').text == 'review\nthen test'
    assert validate_resume_plan('review').size == 6
    with pytest.raises(ResumeRejected, match='^empty$'):
        validate_resume_plan(' ')


@pytest.mark.parametrize('validator,limit', [
    (validate_supplement, MAX_SUPPLEMENT), (validate_resume_plan, MAX_PLAN)])
def test_limits_before_trimming(validator, limit):
    assert validator('a' * limit).size == limit
    with pytest.raises(ResumeRejected, match='^too_long$'):
        validator(' ' * (limit + 1))


@pytest.mark.parametrize('value', [None, [], {}, 1, True])
def test_types(value):
    for validator in (validate_supplement, validate_resume_plan):
        with pytest.raises(ResumeRejected, match='^invalid_shape$'):
            validator(value)


@pytest.mark.parametrize('value', ['\tplan', 'plan\r', '\x00plan', 'a\u202eb', 'a\u2028b'])
def test_controls_before_trimming(value):
    for validator in (validate_supplement, validate_resume_plan):
        with pytest.raises(ResumeRejected, match='^control_chars$'):
            validator(value)


@pytest.mark.parametrize('value', ['API_TOKEN=private', 'https://user:private@host',
    '-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----'])
def test_whole_text_rejected_for_detected_secret(value):
    for validator in (validate_supplement, validate_resume_plan):
        with pytest.raises(ResumeRejected) as info:
            validator(value)
        assert str(info.value) == 'secret_detected' and 'private' not in str(info.value)


def test_repr_hides_text():
    assert 'private-marker' not in repr(validate_supplement('private-marker'))
    assert 'private-marker' not in repr(validate_resume_plan('private-marker'))
