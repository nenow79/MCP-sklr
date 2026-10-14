"""Testy parsera OKF (C1a): samodzielne, bez dysku, sieci i serwera."""
import pytest

from okf_engine.models import (
    ERR_INVALID_OKF, ERR_SAFETY_LIMIT, ERR_UNSUPPORTED_PROFILE, DocumentIdentity, IdentityError,
    ParseLimits)
from okf_engine.parser import parse_document

IDENT = DocumentIdentity("general", "tables/orders")


def parse(text, limits=None):
    data = text if isinstance(text, bytes) else text.encode("utf-8")
    return parse_document(data, IDENT, limits) if limits else parse_document(data, IDENT)


def fm(yaml_text, body=""):
    return f"---\n{yaml_text}---\n{body}"


def reasons(doc):
    return [issue.reason for issue in doc.errors]


# --- struktura i minimalna zgodność ---

def test_type_only_is_conformant():
    doc = parse(fm("type: Reference\n"))
    assert doc.ok and doc.metadata == {"type": "Reference"} and doc.body == ""
    assert doc.body_start_line == 4


def test_unknown_type_and_extra_fields_preserved():
    doc = parse(fm("type: Something New\nx-ext: {a: [1, 2], b: text}\n"))
    assert doc.ok
    assert doc.metadata["x-ext"] == {"a": [1, 2], "b": "text"}


def test_spec_example_parses():
    text = (
        "---\ntype: BigQuery Table\ntitle: Customer Orders\n"
        "description: One row per completed customer order across all channels.\n"
        "resource: https://console.cloud.google.com/bigquery?p=acme&d=sales&t=orders\n"
        "tags: [sales, orders, revenue]\n"
        "generated: { by: reference_agent/gemini-2.5-pro, at: 2026-05-28T14:30:00Z }\n---\n\n"
        "# Schema\n\n|---|---|\n| a | b |\n")
    doc = parse(text)
    assert doc.ok
    assert doc.metadata["generated"]["at"] == "2026-05-28T14:30:00Z"
    assert doc.body.startswith("\n# Schema")
    assert doc.body_start_line == 9


def test_missing_frontmatter_keeps_text_as_body():
    doc = parse("# Just markdown\n")
    assert not doc.ok and reasons(doc) == ["missing_frontmatter"]
    assert doc.errors[0].kind == ERR_INVALID_OKF
    assert doc.metadata is None and doc.body == "# Just markdown\n" and doc.body_start_line == 1


def test_empty_and_unterminated():
    assert reasons(parse(b"")) == ["missing_frontmatter"]
    assert reasons(parse("---\n")) == ["unterminated_frontmatter"]
    assert reasons(parse("---\ntype: X\n")) == ["unterminated_frontmatter"]


def test_empty_block_is_not_mapping():
    assert reasons(parse("---\n---\n")) == ["yaml_not_mapping"]
    assert reasons(parse(fm("- a\n- b\n"))) == ["yaml_not_mapping"]


def test_type_required_and_must_be_string():
    assert reasons(parse(fm("title: x\n"))) == ["missing_type"]
    assert reasons(parse(fm("type: 123\n"))) == ["type_not_string"]
    assert reasons(parse(fm('type: "  "\n'))) == ["type_not_string"]
    assert reasons(parse(fm("type:\n"))) == ["type_not_string"]
    doc = parse(fm("title: x\n"))
    assert doc.metadata == {"title": "x"}  # reszta pozostaje dostępna prywatnie


def test_delimiter_tolerates_trailing_spaces_and_body_may_contain_dashes():
    doc = parse("---  \ntype: X\n---\nhello\n---\nworld\n")
    assert doc.ok and doc.body == "hello\n---\nworld\n"


def test_body_start_line_and_crlf():
    doc = parse(b"---\r\ntype: X\r\n---\r\nline1\r\nline2\r\n")
    assert doc.ok and doc.eol == "crlf" and doc.warnings == ()
    assert doc.body == "line1\r\nline2\r\n" and doc.body_start_line == 4


def test_mixed_eol_warning():
    doc = parse(b"---\ntype: X\n---\na\r\nb\n")
    assert doc.ok and doc.eol == "mixed" and "mixed_eol" in doc.warnings


def test_bom_tolerated_with_warning():
    doc = parse(b"\xef\xbb\xbf---\ntype: X\n---\nbody\n")
    assert doc.ok and doc.has_bom and "bom" in doc.warnings and doc.body == "body\n"


def test_not_utf8_rejected_without_transcoding():
    doc = parse(b"---\ntype: X\n---\ncena z\xb3otych\n")
    assert reasons(doc) == ["not_utf8"] and doc.metadata is None


# --- profil YAML ---

def test_scalar_profile_v1():
    doc = parse(fm(
        "type: X\na: yes\nb: 2026-05-28\nc: true\nd: 5\ne: 1.5\nf: null\ng: .nan\n"
        "h: \"5\"\ni: ~\nj: on\nk: 0x1F\nl: -3\nm: 007\nn: 12345678901234567890\no: !!str 123\n"))
    meta = doc.metadata
    assert meta["a"] == "yes" and meta["j"] == "on"
    assert meta["b"] == "2026-05-28"
    assert meta["c"] is True and meta["d"] == 5 and meta["e"] == 1.5 and meta["l"] == -3
    assert meta["f"] is None and meta["i"] is None
    assert meta["g"] == ".nan" and meta["h"] == "5" and meta["k"] == "0x1F" and meta["m"] == "007"
    assert meta["n"] == "12345678901234567890" and meta["o"] == "123"


def test_duplicate_keys_are_an_error_with_line():
    doc = parse(fm("type: X\na: 1\na: 2\n"))
    assert reasons(doc) == ["duplicate_key"] and doc.errors[0].line == 4
    assert reasons(parse(fm("type: X\nn: {a: 1, a: 2}\n"))) == ["duplicate_key"]


@pytest.mark.parametrize("yaml_text,reason", [
    ("type: X\nx: !!python/object/apply:os.system [echo]\n", "unsupported_tag"),
    ("type: X\nx: !custom value\n", "unsupported_tag"),
    ("type: X\nx: !!binary aGVsbG8=\n", "unsupported_tag"),
    ("type: X\nx: !!set {a, b}\n", "unsupported_tag"),
    ("type: X\nbase: &b {a: 1}\nx:\n  <<: *b\n", "merge_key"),
    ("type: X\n1: a\n", "non_text_key"),
    ("type: X\ntrue: a\n", "non_text_key"),
    ("type: X\n? [a, b]\n: c\n", "non_text_key"),
])
def test_unsupported_profile(yaml_text, reason):
    doc = parse(fm(yaml_text))
    assert reasons(doc) == [reason]
    assert doc.errors[0].kind == ERR_UNSUPPORTED_PROFILE


def test_cycle_rejected():
    doc = parse(fm("type: X\na: &x [*x]\n"))
    assert reasons(doc) == ["yaml_cycle"] and doc.errors[0].kind == ERR_UNSUPPORTED_PROFILE


def test_syntax_error_and_multiple_documents_have_safe_reasons():
    doc = parse(fm("type: [unclosed\n"))
    assert reasons(doc) == ["yaml_syntax"] and doc.errors[0].kind == ERR_INVALID_OKF
    assert reasons(parse(fm("type: X\n--- b\n"))) == ["multiple_documents"]
    assert reasons(parse(fm("type: X\nx: *missing\n"))) == ["yaml_syntax"]


# --- limity ---

def test_alias_sharing_is_allowed_but_limited():
    text = fm("type: X\na: &a v\nb: *a\n")
    assert parse(text).metadata["b"] == "v"
    doc = parse(text, ParseLimits(max_alias_references=0))
    assert reasons(doc) == ["too_many_aliases"] and doc.errors[0].kind == ERR_SAFETY_LIMIT


def test_alias_expansion_bomb_rejected():
    nine = "[" + ", ".join(["x"] * 9) + "]"
    text = fm(
        f"type: X\na: &a {nine}\n"
        "b: &b [" + ", ".join(["*a"] * 9) + "]\n"
        "c: &c [" + ", ".join(["*b"] * 9) + "]\n"
        "d: &d [" + ", ".join(["*c"] * 9) + "]\n")
    doc = parse(text)
    assert not doc.ok and doc.errors[0].kind == ERR_SAFETY_LIMIT


def test_node_limit():
    body = "type: X\n" + "".join(f"k{i}: v\n" for i in range(10))
    doc = parse(fm(body), ParseLimits(max_yaml_nodes=5))
    assert reasons(doc) == ["yaml_too_many_nodes"] and doc.errors[0].kind == ERR_SAFETY_LIMIT


def test_depth_limit_and_recursion_guard():
    doc = parse(fm("type: X\na: " + "[" * 40 + "]" * 40 + "\n"))
    assert reasons(doc) == ["yaml_too_deep"] and doc.errors[0].kind == ERR_SAFETY_LIMIT
    deep = parse(fm("type: X\na: " + "[" * 20000 + "]" * 20000 + "\n"))
    assert not deep.ok and deep.errors[0].kind == ERR_SAFETY_LIMIT


def test_size_limits():
    limits = ParseLimits(max_document_bytes=1024, max_frontmatter_bytes=256)
    big = parse("---\ntype: X\n---\n" + "a" * 2000, limits)
    assert reasons(big) == ["document_too_large"] and big.errors[0].kind == ERR_SAFETY_LIMIT
    wide = parse(fm("type: X\nnote: " + "x" * 400 + "\n"), limits)
    assert reasons(wide) == ["frontmatter_too_large"] and wide.errors[0].kind == ERR_SAFETY_LIMIT


@pytest.mark.parametrize("kwargs", [
    {"max_alias_references": True}, {"max_document_bytes": 10}, {"max_yaml_depth": 33},
    {"max_yaml_nodes": 0}, {"max_document_bytes": 1024, "max_frontmatter_bytes": 2048},
    {"max_alias_references": "1"}, {"max_document_bytes": 2 * 1048576},
])
def test_invalid_limits_rejected(kwargs):
    with pytest.raises(ValueError):
        ParseLimits(**kwargs)


# --- prywatność wyniku i tożsamość ---

def test_repr_does_not_leak_content():
    doc = parse(fm("type: X\nnote: very-private-note\n", "private-body\n"))
    text = repr(doc)
    assert "very-private-note" not in text and "private-body" not in text


def test_parse_requires_bytes():
    with pytest.raises(TypeError):
        parse_document("text", IDENT)


@pytest.mark.parametrize("concept_id", [
    "", "/abs", "a\\b", "a//b", "./a", "a/../b", "a/.", "a.md", "A.MD", "tables/index", "log",
    "c:/x", "a:b", "a/", "a /b", "x" * 513, "a\x00b", "dir./x", "a*b",
])
def test_invalid_concept_ids(concept_id):
    with pytest.raises(IdentityError):
        DocumentIdentity("general", concept_id)


@pytest.mark.parametrize("concept_id", ["a", "tables/orders", "zażółć/gęślą", "a-b_c/d.e", "indexes/log-2026"])
def test_valid_concept_ids(concept_id):
    assert DocumentIdentity("general", concept_id).concept_id == concept_id


@pytest.mark.parametrize("bundle_id", ["", "A", "-a", "a b", "x" * 33, "a/b", 5])
def test_invalid_bundle_ids(bundle_id):
    with pytest.raises(IdentityError):
        DocumentIdentity(bundle_id, "a")
