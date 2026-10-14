"""Testy limitów wiedzy OKF (C1b): granice profilu v1, diagnostyka, defaults w state.py."""
import pytest

from okf_engine.limits import LIMIT_BOUNDS, MAX_SECTIONS_PER_DOCUMENT, KnowledgeLimits, parse_limits
from okf_engine.models import ParseLimits
from state import DEFAULT_CONFIG


def test_defaults_in_config_match_profile():
    limits, errors = parse_limits(DEFAULT_CONFIG["knowledge"]["limits"])
    assert errors == () and limits == KnowledgeLimits()
    assert set(DEFAULT_CONFIG["knowledge"]["limits"]) == set(LIMIT_BOUNDS)


def test_profile_equals_upper_bounds_except_timings():
    limits = KnowledgeLimits()
    for name in ("max_bundles", "max_documents_per_bundle", "max_depth", "max_document_bytes",
                 "max_frontmatter_bytes", "max_yaml_nodes", "max_yaml_depth", "max_alias_references",
                 "max_index_text_bytes", "scan_budget_s", "search_budget_s"):
        assert getattr(limits, name) == LIMIT_BOUNDS[name][1]
    assert MAX_SECTIONS_PER_DOCUMENT == 2000


def test_missing_fields_use_profile_and_unknown_keys_ignored():
    limits, errors = parse_limits({"max_bundles": 5, "future_option": 1})
    assert errors == () and limits.max_bundles == 5 and limits.max_depth == 20
    assert parse_limits({}) == (KnowledgeLimits(), ())


@pytest.mark.parametrize("section", [None, [], "x", 5, True])
def test_non_mapping_section_is_error(section):
    assert parse_limits(section) == (None, ("limits",))


@pytest.mark.parametrize("value", [True, False, "5", 5.0, None, [5], 0, -1, 10 ** 12])
def test_invalid_values_are_reported_not_replaced(value):
    limits, errors = parse_limits({"max_bundles": value})
    assert limits is None and errors == ("max_bundles",)


def test_bounds_edges():
    for name, (low, high) in LIMIT_BOUNDS.items():
        for value in (low, high):
            section = {name: value}
            if name == "max_document_bytes" and value < 65536:
                section["max_frontmatter_bytes"] = 256
            limits, errors = parse_limits(section)
            assert errors == () and getattr(limits, name) == value, (name, value)
        for value in (low - 1, high + 1):
            assert parse_limits({name: value})[1] == (name,), (name, value)


def test_frontmatter_cannot_exceed_document():
    limits, errors = parse_limits({"max_document_bytes": 2048, "max_frontmatter_bytes": 4096})
    assert limits is None and errors == ("max_frontmatter_bytes",)
    limits, errors = parse_limits({"max_document_bytes": 1024})  # domyślne 65536 > 1024
    assert limits is None and errors == ("max_frontmatter_bytes",)


def test_all_invalid_fields_collected():
    _, errors = parse_limits({"max_bundles": 0, "scan_budget_s": 7, "max_depth": 5})
    assert errors == ("max_bundles", "scan_budget_s")


def test_dataclass_validates_directly():
    with pytest.raises(ValueError):
        KnowledgeLimits(max_bundles=21)
    with pytest.raises(ValueError):
        KnowledgeLimits(refresh_after_s=True)


def test_to_parse_limits():
    parse = KnowledgeLimits(max_document_bytes=2048, max_frontmatter_bytes=512, max_yaml_nodes=7,
                            max_yaml_depth=3, max_alias_references=0).to_parse_limits()
    assert parse == ParseLimits(2048, 512, 7, 3, 0)
