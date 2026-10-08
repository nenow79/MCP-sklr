import pytest

from skills_engine.models import SOURCE_CENTRAL, SOURCE_PROJECT
from skills_engine.policy import declares_no_confirm, decide_confirmation


@pytest.mark.parametrize("value", ["false", "False", " FALSE "])
def test_declares_no_confirm_true(value):
    assert declares_no_confirm({"vibecoder-confirm": value}) is True


@pytest.mark.parametrize("meta", [{}, {"vibecoder-confirm": "true"}, {"vibecoder-confirm": "no"}, {"vibecoder-confirm": ""}])
def test_declares_no_confirm_false(meta):
    assert declares_no_confirm(meta) is False


def test_project_skill_always_confirms_even_if_flag_and_trusted():
    d = decide_confirmation(SOURCE_PROJECT, {"vibecoder-confirm": "false"}, trusted=True)
    assert d.required is True
    assert d.offer_trust is False


def test_central_default_confirms():
    d = decide_confirmation(SOURCE_CENTRAL, {}, trusted=False)
    assert d.required is True
    assert d.offer_trust is False


def test_central_default_confirms_even_if_trusted():
    d = decide_confirmation(SOURCE_CENTRAL, {}, trusted=True)
    assert d.required is True


def test_central_flag_untrusted_asks_and_offers_trust():
    d = decide_confirmation(SOURCE_CENTRAL, {"vibecoder-confirm": "false"}, trusted=False)
    assert d.required is True
    assert d.offer_trust is True


def test_central_flag_trusted_skips_confirmation():
    d = decide_confirmation(SOURCE_CENTRAL, {"vibecoder-confirm": "false"}, trusted=True)
    assert d.required is False
