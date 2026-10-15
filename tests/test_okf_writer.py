"""C3a: bajty wejściowe/wyjściowe bez plików, serwera i produkcyjnej wiedzy."""
from datetime import datetime, timezone

import pytest

from okf_engine.models import DocumentIdentity, ParseLimits
from okf_engine.parser import parse_document
from okf_engine.writer import WriteError, prepare_write

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
IDENTITY = DocumentIdentity("general", "tables/orders")


def document(metadata="type: Reference\n", body="# Title\ntext\n", eol="\n", bom=False):
    text = "---\n" + metadata + "---\n" + body
    return (b"\xef\xbb\xbf" if bom else b"") + text.replace("\n", eol).encode("utf-8")


def prepare(**kwargs):
    defaults = dict(identity=IDENTITY, mode="create", body="# Title\ntext\n",
                    metadata_yaml="type: Reference\n", now=NOW)
    defaults.update(kwargs)
    return prepare_write(**defaults)


def update(before, **kwargs):
    defaults = dict(mode="update", before=before, metadata_yaml=None)
    defaults.update(kwargs)
    return prepare(**defaults)


def test_minimal_create_does_not_invent_metadata():
    plan = prepare()
    assert plan.after == document() and plan.before is None and plan.changed
    parsed = parse_document(plan.after, IDENTITY)
    assert parsed.metadata == {"type": "Reference"}
    assert plan.after_view.status == "stable"  # sygnał OKF, nie dopisane pole
    for key in ("status", "generated", "verified", "sources"):
        assert key not in parsed.metadata


def test_unknown_types_and_extensions_remain_valid():
    plan = prepare(metadata_yaml="type: External Custom Type\nx-vendor: {nested: [one, 2]}\n")
    parsed = parse_document(plan.after, IDENTITY)
    assert parsed.metadata == {"type": "External Custom Type", "x-vendor": {"nested": ["one", 2]}}


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("bom", [False, True])
def test_update_preserves_bom_frontmatter_comments_and_final_newline(eol, bom):
    prefix = "---  \n# source comment\ntype: 'Reference'\nx-ext: {foo: 7} # keep\n--- \n"
    raw_prefix = (b"\xef\xbb\xbf" if bom else b"") + prefix.replace("\n", eol).encode()
    before = raw_prefix + ("old" + eol).encode()
    plan = update(before, body="new\nline without newline")
    assert plan.after == raw_prefix + ("new" + eol + "line without newline").encode()
    assert plan.has_bom == bom
    assert plan.metadata_replaced is False
    assert plan.eol_adjusted == (eol != "\n")


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
def test_explicit_metadata_update_preserves_extensions_bom_eol(eol):
    before = document("type: Reference\nx-ext: {foo: 7}\n", eol=eol, bom=True)
    plan = update(before, metadata_yaml="type: Procedure\nx-ext: {foo: 8}\ntags: [one]\n")
    assert plan.after == document("type: Procedure\nx-ext: {foo: 8}\ntags: [one]\n", eol=eol, bom=True)
    assert plan.metadata_replaced and plan.after_view.type == "Procedure"


def test_delimiter_without_newline_gets_separator_only_when_body_added():
    before = b"---\ntype: Reference\n---"
    noop = update(before, body="")
    assert not noop.changed and noop.after == before
    plan = update(before, body="new")
    assert plan.after == before + b"\nnew" and plan.eol_adjusted


def test_empty_body_can_replace_existing_body():
    plan = update(document(), body="")
    assert plan.after == b"---\ntype: Reference\n---\n" and plan.changed


def test_exact_noop_also_allowed_for_secret_document():
    before = document("type: Reference\ntoken: META_SENTINEL\n", body="password: BODY_SENTINEL\n")
    plan = update(before, body="password: BODY_SENTINEL\n")
    assert plan.after == before and not plan.changed
    assert plan.before_view.masked and plan.after_view.masked
    full = update(before, body="password: BODY_SENTINEL\n", metadata_yaml="type: Reference\ntoken: META_SENTINEL\n")
    assert not full.changed


@pytest.mark.parametrize("metadata", [
    "type: Reference\n",  # top-level extension removed
    "type: Reference\nx-ext: {}\n",  # nested field removed
    "type: Reference\nx-ext: null\n",  # mapping replaced with scalar
    "type: Reference\nx-ext: {nested: {}}\n",  # deeper mapping field removed
])
def test_metadata_key_loss_rejected_recursively(metadata):
    before = document("type: Reference\nx-ext: {nested: {name: value}}\n")
    with pytest.raises(WriteError, match="METADATA_KEY_LOSS"):
        update(before, metadata_yaml=metadata)


def test_mapping_values_may_change_and_keys_may_be_added():
    before = document("type: Reference\nx-ext: {nested: {name: old}}\n")
    plan = update(before, metadata_yaml="type: Reference\nx-ext: {nested: {name: new, count: 2}}\n")
    assert parse_document(plan.after, IDENTITY).metadata["x-ext"]["nested"] == {"name": "new", "count": 2}


def test_list_edits_and_reordering_are_explicitly_marked_for_diff_review():
    before = document("type: Reference\nsources: [{url: https://one.test}, {note: two}]\n")
    plan = update(before, metadata_yaml="type: Reference\nsources: [{note: two}, {url: https://one.test}]\n")
    assert "metadata_lists_changed" in plan.warnings
    assert parse_document(plan.after, IDENTITY).metadata["sources"][0] == {"note": "two"}


@pytest.mark.parametrize("body", ["password: ***\n", "new clean text\n"])
def test_masked_body_cannot_be_replaced_even_with_clean_text(body):
    before = document(body="password: BODY_SENTINEL\n")
    with pytest.raises(WriteError, match="MASKED_UPDATE_UNSUPPORTED"):
        update(before, body=body)


def test_masked_metadata_may_be_preserved_during_clean_body_update():
    before = document("type: Reference\ntoken: META_SENTINEL\n")
    plan = update(before, body="new clean body\n")
    assert b"token: META_SENTINEL" in plan.after
    assert plan.after_view.metadata["token"] == "***"


def test_masked_metadata_full_replacement_is_blocked():
    before = document("type: Reference\nx-ext: {token: META_SENTINEL}\n")
    with pytest.raises(WriteError, match="MASKED_UPDATE_UNSUPPORTED"):
        update(before, metadata_yaml="type: Reference\nx-ext: {token: '***'}\n")


def test_unchanged_masked_body_can_accompany_metadata_edit():
    before = document(body="password: BODY_SENTINEL\n")
    plan = update(before, body="password: BODY_SENTINEL\n", metadata_yaml="type: Procedure\n")
    assert plan.after.endswith(b"password: BODY_SENTINEL\n")
    assert plan.after_view.type == "Procedure"


def test_semantic_warnings_do_not_invent_or_block_verification():
    plan = prepare(metadata_yaml="type: Reference\nverified: [{by: nobody, at: invalid}]\nstatus: strange\n")
    parsed = parse_document(plan.after, IDENTITY)
    assert parsed.metadata["verified"] == [{"by": "nobody", "at": "invalid"}]
    assert plan.after_view.trust_tier == "unverified"
    assert "trust_metadata_changed" in plan.warnings and "lifecycle_metadata_changed" in plan.warnings


def test_trust_and_lifecycle_changes_are_explicit():
    before = document("type: Reference\nstatus: draft\n")
    plan = update(before, metadata_yaml="type: Reference\nstatus: stable\nverified: {by: 'human:Anna', at: 2026-10-15T00:00:00Z}\n")
    assert "trust_metadata_changed" in plan.warnings
    assert "lifecycle_metadata_changed" in plan.warnings


def test_create_normalizes_homogeneous_proposed_eol_to_lf():
    plan = prepare(body="# Title\r\ntext\r\n", metadata_yaml="type: Reference\r\n")
    assert plan.after == document() and plan.eol_adjusted and "eol_adjusted" in plan.warnings


@pytest.mark.parametrize("kwargs", [
    {"body": "first\r\nsecond\n"},
    {"metadata_yaml": "type: Reference\r\ntitle: Test\n"},
    {"mode": "update", "before": b"---\r\ntype: Reference\n---\nbody"},
])
def test_mixed_eol_refused(kwargs):
    with pytest.raises(WriteError, match="MIXED_EOL_UNSUPPORTED"):
        prepare(**kwargs)


@pytest.mark.parametrize("yaml", [
    "---\ntype: Reference", "type: Reference\n---\nevil", "type: Reference\n...\n",
    "type: Reference\n--- # comment\ntitle: second",
])
def test_metadata_delimiters_cannot_escape_into_body(yaml):
    with pytest.raises(WriteError, match="METADATA_DELIMITERS_UNSUPPORTED"):
        prepare(metadata_yaml=yaml)


def test_indented_marker_in_yaml_scalar_is_allowed():
    plan = prepare(metadata_yaml="type: Reference\ndescription: |\n  ---\n  text\n")
    assert plan.after_view.description == "---\ntext\n"


def test_body_frontmatter_refused_but_markdown_rules_allowed():
    with pytest.raises(WriteError, match="BODY_FRONTMATTER_UNSUPPORTED"):
        prepare(body="---\ntype: Reference\n---\n# Body\n")
    plan = prepare(body="---\nprose\n---\nbody\n")
    assert plan.after_view.body == "---\nprose\n---\nbody\n"


@pytest.mark.parametrize("yaml", [
    "title: missing type\n", "type: 123\n", "type: Reference\ntype: Procedure\n",
    "type: Reference\nx: [unterminated", "- Reference\n",
])
def test_invalid_proposals_rejected_without_raw_errors(yaml):
    with pytest.raises(WriteError, match="^INVALID_OKF$"):
        prepare(metadata_yaml=yaml)


@pytest.mark.parametrize("yaml", [
    "type: Reference\nx: !!python/object/apply:os.system ['SECRET_SENTINEL']\n",
    "type: Reference\nx: &cycle [*cycle]\n",
])
def test_unsafe_yaml_rejected_without_constructor_execution(yaml):
    with pytest.raises(WriteError) as error:
        prepare(metadata_yaml=yaml)
    assert error.value.code == "UNSUPPORTED_PROFILE"
    assert "SECRET_SENTINEL" not in str(error.value)


def test_limits_apply_to_combined_result_and_utf8_byte_size():
    limits = ParseLimits(max_document_bytes=1024, max_frontmatter_bytes=256)
    with pytest.raises(WriteError, match="SAFETY_LIMIT"):
        prepare(body="ą" * 600, limits=limits)
    with pytest.raises(WriteError, match="SAFETY_LIMIT"):
        prepare(body="x" * 1000, metadata_yaml="type: Reference\ntitle: " + "y" * 100, limits=limits)
    with pytest.raises(WriteError, match="SAFETY_LIMIT"):
        prepare(metadata_yaml="type: Reference\nx: " + "x" * 300, limits=limits)
    with pytest.raises(WriteError, match="SAFETY_LIMIT"):
        prepare(metadata_yaml="type: Reference\nx: {a: {b: c}}", limits=ParseLimits(max_yaml_depth=2))


@pytest.mark.parametrize("kwargs,code", [
    ({"mode": "upsert"}, "INVALID_ARGUMENT"),
    ({"body": None}, "INVALID_ARGUMENT"),
    ({"metadata_yaml": {}}, "INVALID_ARGUMENT"),
    ({"identity": "general/note"}, "INVALID_ARGUMENT"),
    ({"before": bytearray(b"x")}, "INVALID_ARGUMENT"),
    ({"now": datetime(2026, 1, 1)}, "INVALID_ARGUMENT"),
    ({"before": b""}, "CREATE_CONFLICT"),
    ({"metadata_yaml": None}, "METADATA_REQUIRED"),
    ({"mode": "update"}, "DOCUMENT_UNAVAILABLE"),
    ({"body": "\ud800"}, "INVALID_UTF8"),
    ({"metadata_yaml": "type: \ud800"}, "INVALID_UTF8"),
    ({"mode": "update", "before": b"---\ntype: Reference\n---\nz\xb3"}, "INVALID_UTF8"),
    ({"mode": "update", "before": b"ordinary markdown"}, "INVALID_OKF"),
])
def test_argument_and_existing_document_errors_are_safe(kwargs, code):
    with pytest.raises(WriteError) as error:
        prepare(**kwargs)
    assert error.value.code == code and str(error.value) == code


def test_private_plan_repr_does_not_contain_raw_or_public_content():
    plan = prepare(body="password: BODY_SENTINEL\n", metadata_yaml="type: Reference\ntoken: META_SENTINEL\n")
    assert "BODY_SENTINEL" not in repr(plan) and "META_SENTINEL" not in repr(plan)
    assert "Reference" not in repr(plan)
