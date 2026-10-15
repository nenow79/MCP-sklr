"""Podgląd C3a: poufność, pełne sygnały zmiany i ograniczony poprawny JSON."""
import json
from datetime import datetime, timezone

import pytest

from okf_engine.models import DocumentIdentity
from okf_engine.preview import build_preview
from okf_engine.writer import WriteError, prepare_write

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
IDENTITY = DocumentIdentity("general", "note")


def create(body="# Title\ntext\n", metadata="type: Reference\n", **kwargs):
    return prepare_write(IDENTITY, "create", body, metadata, now=NOW, **kwargs)


def update(before, body="# Title\ntext\n", metadata=None):
    return prepare_write(IDENTITY, "update", body, metadata, before=before, now=NOW)


def test_create_diff_shows_public_metadata_and_body():
    preview = build_preview(create())
    assert preview["bundle_id"] == "general" and preview["concept_id"] == "note"
    assert preview["mode"] == "create" and preview["changed"]
    assert "+type: Reference" in preview["metadata_diff"]
    assert "+# Title" in preview["body_diff"]
    assert preview["metadata_synthetic"] and "komentarze" in preview["metadata_notice"]
    assert not preview["truncated"]
    assert not {"before", "after", "body", "metadata_yaml", "revision", "hash"} & preview.keys()


def test_comments_nested_secrets_urls_and_pem_are_not_exposed():
    body = ("# Title\npassword: BODY_SENTINEL\nhttps://user:URL_SENTINEL@example.test/path\n"
            "-----BEGIN PRIVATE KEY-----\nPEM_SENTINEL\n-----END PRIVATE KEY-----\n")
    metadata = ("# COMMENT_SENTINEL\ntype: Reference\nx: {token: META_SENTINEL}\n"
                "resource: https://user:RESOURCE_SENTINEL@example.test/path\n")
    plan = create(body, metadata)
    preview = build_preview(plan)
    encoded = json.dumps(preview, ensure_ascii=False)
    for text in ("BODY_SENTINEL", "URL_SENTINEL", "PEM_SENTINEL", "COMMENT_SENTINEL",
                 "META_SENTINEL", "RESOURCE_SENTINEL"):
        assert text not in encoded
    assert preview["masked"] and ("***" in encoded or "ZAMASKOWANO" in encoded)
    assert b"META_SENTINEL" in plan.after and b"BODY_SENTINEL" in plan.after


def test_preserved_masked_metadata_safe_in_update_diff():
    before = create(metadata="type: Reference\ntoken: META_SENTINEL\n").after
    preview = build_preview(update(before, body="# Updated\nnew\n"))
    assert preview["metadata_diff"] == ""
    assert "META_SENTINEL" not in json.dumps(preview)
    assert "+# Updated" in preview["body_diff"]


def test_comments_only_change_is_visible_without_exposing_comment_text():
    before = create(metadata="# OLD_COMMENT\ntype: 'Reference'\n").after
    plan = update(before, metadata="# NEW_COMMENT\ntype: Reference\n")
    preview = build_preview(plan)
    assert preview["changed"] and preview["metadata_replaced"] and preview["source_format_changed"]
    assert preview["metadata_diff"] == preview["body_diff"] == ""
    assert "OLD_COMMENT" not in json.dumps(preview) and "NEW_COMMENT" not in json.dumps(preview)


def test_trust_lifecycle_and_list_changes_are_visible():
    before = create(metadata="type: Reference\nstatus: draft\nsources: [one]\n").after
    preview = build_preview(update(before, metadata="type: Reference\nstatus: stable\nsources: [two]\n"
                                   "verified: {by: 'human:Anna', at: 2026-10-15T00:00:00Z}\n"))
    for warning in ("trust_metadata_changed", "lifecycle_metadata_changed", "metadata_lists_changed"):
        assert warning in preview["warnings"]
    assert "verified" in preview["metadata_diff"] and "stable" in preview["metadata_diff"]


def test_noop_has_no_diff_or_format_change():
    before = create().after
    preview = build_preview(update(before))
    assert not preview["changed"] and not preview["source_format_changed"]
    assert preview["metadata_diff"] == preview["body_diff"] == ""


@pytest.mark.parametrize("budget", [1000, 12000, 80000])
def test_entire_preview_json_fits_budget_with_escaped_unicode(budget):
    plan = create(body=("quote \" backslash \\ żółć\n" * 2000),
                  metadata="type: Reference\ndescription: '" + "ą" * 1000 + "'\n")
    preview = build_preview(plan, max_chars=budget)
    encoded = json.dumps(preview, ensure_ascii=False)
    assert len(encoded) <= budget and json.loads(encoded) == preview
    assert preview["bundle_id"] == IDENTITY.bundle_id and preview["concept_id"] == IDENTITY.concept_id
    if budget <= 12000:
        assert preview["truncated"] and preview["reasons"]


def test_diff_computation_budget_does_not_pretend_full_preview():
    before = create(body="old\n" * 2500).after
    preview = build_preview(update(before, body="new\n" * 2500))
    assert preview["truncated"] and "diff_budget" in preview["reasons"]
    assert "częściowy" in preview["body_diff"]


def test_large_single_line_is_bounded_before_diff():
    preview = build_preview(create(body="x" * 210000))
    assert preview["truncated"] and "diff_budget" in preview["reasons"]


def test_missing_final_newline_is_visible_in_diff():
    before = create(body="same\n").after
    preview = build_preview(update(before, body="same"))
    assert "brak końca linii" in preview["body_diff"]


def test_unicode_line_separator_does_not_create_artificial_diff_lines():
    preview = build_preview(create(body="one\u2028two\n"))
    assert "+one\u2028two\n" in preview["body_diff"]


def test_metadata_reordering_is_marked_as_source_format_change():
    before = create(metadata="type: Reference\ntitle: Title\n").after
    preview = build_preview(update(before, metadata="title: Title\ntype: Reference\n"))
    assert preview["source_format_changed"] and preview["metadata_replaced"]


@pytest.mark.parametrize("budget", [True, 999, 80001, "12000", None])
def test_invalid_preview_budget(budget):
    with pytest.raises(WriteError, match="INVALID_ARGUMENT"):
        build_preview(create(), max_chars=budget)


def test_long_identity_is_preserved_when_diff_can_be_shortened():
    identity = DocumentIdentity("g" * 32, "/".join(["a" * 100] * 5))
    plan = prepare_write(identity, "create", "body", "type: Reference", now=NOW)
    preview = build_preview(plan, max_chars=1000)
    assert len(json.dumps(preview, ensure_ascii=False)) <= 1000
    assert preview["bundle_id"] == identity.bundle_id
    assert preview["concept_id"] == identity.concept_id
    assert preview["truncated"] and "response_budget" in preview["reasons"]


def test_unfit_required_envelope_reports_error_without_cutting_identity():
    identity = DocumentIdentity("g" * 32, "/".join(["a" * 100] * 5))
    # Poprawny strukturalnie OKF z rzeczywistymi ostrzeżeniami semantycznymi.
    # Długość samych identyfikatorów nie gwarantuje przekroczenia budżetu.
    metadata = ("type: Attested Computation\ngenerated: {}\nverified: []\n"
                "status: unknown\nstale_after: invalid\ntags: invalid\n"
                "title: ''\ndescription: false\nsources: false\nruntime: ''\n")
    plan = prepare_write(identity, "create", "body", metadata, now=NOW)
    required = build_preview(plan, max_chars=80000)
    required.update(metadata_diff="", body_diff="", truncated=True, reasons=["response_budget"])
    assert len(json.dumps(required, ensure_ascii=False)) > 1000
    with pytest.raises(WriteError, match="RESULT_TOO_LARGE"):
        build_preview(plan, max_chars=1000)
