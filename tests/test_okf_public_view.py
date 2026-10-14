"""Testy publicznego widoku OKF (C1a): maskowanie, sygnały jakości, mapa linii."""
import json
from datetime import datetime, timezone

import pytest
import yaml

from okf_engine.models import DocumentIdentity
from okf_engine.parser import parse_document
from okf_engine.public_view import (
    PublicViewError, build_public_view, is_secret_name, mask_metadata, mask_text, render_public_yaml)

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
IDENT = DocumentIdentity("general", "tables/orders")


def make_doc(yaml_text, body="", ident=IDENT):
    return parse_document(f"---\n{yaml_text}---\n{body}".encode("utf-8"), ident)


def view(yaml_text, body="", now=NOW):
    return build_public_view(make_doc(yaml_text, body), now)


# --- sygnały: status, stale, zaufanie ---

def test_type_only_defaults():
    v = view("type: Reference\n")
    assert v.status == "stable" and v.stale is None and v.trust_tier == "unverified"
    assert v.title == "orders" and v.description is None and v.tags == () and v.warnings == ()
    assert not v.masked


def test_title_description_tags():
    v = view("type: X\ntitle: Orders\ndescription: One row per order.\ntags: [a, b]\n")
    assert (v.title, v.description, v.tags) == ("Orders", "One row per order.", ("a", "b"))


@pytest.mark.parametrize("status,expected,warn", [
    (None, "stable", False), ("draft", "draft", False), ("Deprecated", "deprecated", False),
    ("bogus", None, True), ("5", None, True),
])
def test_status(status, expected, warn):
    text = "type: X\n" + (f"status: {status}\n" if status else "")
    v = view(text)
    assert v.status == expected and ("status_invalid" in v.warnings) == warn


def test_stale_after():
    assert view("type: X\nstale_after: 2026-05-01T00:00:00Z\n").stale is True
    assert view("type: X\nstale_after: 2026-07-01T00:00:00Z\n").stale is False
    assert view("type: X\nstale_after: 2026-06-01T00:00:00Z\n").stale is True  # now >= stale_after
    assert view("type: X\nstale_after: 2026-06-01T02:00:00+02:00\n").stale is True
    bad = view("type: X\nstale_after: 2026-07-01\n")
    assert bad.stale is None and "stale_after_invalid" in bad.warnings
    naive = view("type: X\nstale_after: 2026-07-01T00:00:00\n")
    assert naive.stale is None and "stale_after_invalid" in naive.warnings
    assert view("type: X\n").stale is None


def test_naive_now_rejected():
    with pytest.raises(ValueError):
        build_public_view(make_doc("type: X\n"), datetime(2026, 6, 1))


def test_trust_tiers():
    human = view("type: X\nverified: { by: human:ahormati, at: 2026-05-30T09:00:00Z }\n")
    assert human.trust_tier == "human-reviewed"
    assert human.metadata["verified"] == [{"by": "human:ahormati", "at": "2026-05-30T09:00:00Z"}]
    machine = view("type: X\nverified:\n  - { by: process:nightly, at: 2026-05-30T09:00:00Z }\n")
    assert machine.trust_tier == "machine-confirmed"
    both = view("type: X\nverified:\n  - { by: process:n, at: 2026-05-30T09:00:00Z }\n"
                "  - { by: human:a, at: 2026-05-30T10:00:00Z }\n")
    assert both.trust_tier == "human-reviewed"
    assert view("type: X\n").trust_tier == "unverified"


@pytest.mark.parametrize("verified", [
    "{ by: human:x, at: 2026-05-30T09:00:00 }",  # brak offsetu
    "{ by: human:x }",
    "{ at: 2026-05-30T09:00:00Z }",
    "{ by: \"\", at: 2026-05-30T09:00:00Z }",
    "{ by: \"***\", at: 2026-05-30T09:00:00Z }",  # zamaskowany actor nie podnosi zaufania
    "yes", "[]", "[1, 2]",
])
def test_invalid_verified_does_not_raise_trust(verified):
    v = view(f"type: X\nverified: {verified}\n")
    assert v.trust_tier == "unverified"
    assert "verified_invalid" in v.warnings or "verified_event_invalid" in v.warnings


def test_invalid_event_next_to_valid_one():
    v = view("type: X\nverified:\n  - { by: human:a, at: nope }\n"
             "  - { by: process:p, at: 2026-05-30T09:00:00Z }\n")
    assert v.trust_tier == "machine-confirmed" and "verified_event_invalid" in v.warnings


def test_verification_predates_content():
    old = view("type: X\ngenerated: { by: agent/1, at: 2026-06-20T00:00:00Z }\n"
               "verified: { by: human:a, at: 2026-06-10T00:00:00Z }\n")
    assert "verification_predates_content" in old.warnings
    fresh = view("type: X\ngenerated: { by: agent/1, at: 2026-06-10T00:00:00Z }\n"
                 "verified: { by: human:a, at: 2026-06-20T00:00:00Z }\n")
    assert "verification_predates_content" not in fresh.warnings
    none = view("type: X\ngenerated: { by: agent/1, at: 2026-06-20T00:00:00Z }\n")
    assert none.warnings == () and none.trust_tier == "unverified"


def test_generated_validation_and_date_normalization():
    v = view("type: X\ngenerated: { by: agent/1, at: 2026-05-28T16:30:00+02:00 }\n")
    assert v.metadata["generated"]["at"] == "2026-05-28T14:30:00Z" and v.warnings == ()
    bad = view("type: X\ngenerated: { at: nope }\n")
    assert "generated_invalid" in bad.warnings and "generated_at_invalid" in bad.warnings
    assert bad.metadata["generated"]["at"] == "nope"  # niepoprawne pola nie są poprawiane


def test_shape_warnings_do_not_reject():
    v = view("type: X\ntags: [a, 1]\ndescription: 5\nsources: oops\n")
    assert {"tags_invalid", "description_invalid", "sources_invalid"} <= set(v.warnings)
    assert v.tags == ("a",) and v.description is None
    s = view("type: X\nsources:\n  - title: no resource\n  - resource: all queries in project X\n"
             "    last_modified: 2026-05-30T00:00:00Z\n")
    assert s.warnings == ("sources_entry_invalid",)
    d = view("type: X\nsources:\n  - resource: r\n    last_modified: yesterday\nusage_window: { from: 1, to: 2 }\n")
    assert "sources_date_invalid" in d.warnings


def test_attested_computation_is_never_executed():
    ok = view("type: Attested Computation\nruntime: bigquery\n")
    assert ok.warnings == ("computation_not_executed",)
    no_runtime = view("type: Attested Computation\n")
    assert "computation_runtime_missing" in no_runtime.warnings


# --- maskowanie metadanych ---

SECRET_META = (
    "type: Reference\napi_key: abc123\nnested:\n  client_secret: s3cr3t\nitems:\n  - token: tok999\n"
    "sources:\n  - resource: https://user:hunter2@example.com/path\nauthor: someone\n")


def test_metadata_masked_in_public_view_only():
    doc = make_doc(SECRET_META)
    assert doc.metadata["api_key"] == "abc123"  # prywatny snapshot jest surowy
    v = build_public_view(doc, NOW)
    assert v.metadata["api_key"] == "***" and v.metadata["nested"]["client_secret"] == "***"
    assert v.metadata["items"][0]["token"] == "***"
    assert v.metadata["sources"][0]["resource"] == "https://user:***@example.com/path"
    assert v.metadata["author"] == "someone"
    dumped = json.dumps(v.metadata) + v.title + repr(v)
    for raw in ("abc123", "s3cr3t", "tok999", "hunter2"):
        assert raw not in dumped
    assert v.masked
    assert set(v.masked_metadata_paths) == {
        "api_key", "nested.client_secret", "items[0].token", "sources[0].resource"}


def test_masked_structure_is_hidden_entirely():
    masked, count, paths = mask_metadata({"credentials": {"user": "u", "pw": "p"}, "ok": [1]})
    assert masked == {"credentials": "***", "ok": [1]} and count == 1 and paths == ("credentials",)


def test_empty_and_bool_secret_values_untouched():
    masked, count, _ = mask_metadata({"token": None, "secret": "", "password": False})
    assert count == 0 and masked == {"token": None, "secret": "", "password": False}


@pytest.mark.parametrize("name,expected", [
    ("password", True), ("PASSWORD", True), ("db_passwd", True), ("apiKey", True), ("APIKey", True),
    ("x-api-key", True), ("private_key", True), ("accessToken", True), ("client-secret", True),
    ("auth_token", True), ("credentials", True), ("token_count", True),  # znany false positive
    ("author", False), ("monkey", False), ("max_tokens", False), ("keyboard", False),
    ("key", False), ("authority", False), ("secretary", False), ("type", False), (5, False),
])
def test_secret_name_detection(name, expected):
    assert is_secret_name(name) is expected


# --- maskowanie body i mapa linii ---

BODY = (
    "# Title\n"
    "password: hunter2\n"
    "token = abc123xyz\n"
    "url https://user:pw123@host/x\n"
    "Authorization: Bearer abcdefghijkl\n"
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEvQIBADANBgkq\n"
    "AAAAAAAAAAAAAAAA\n"
    "-----END RSA PRIVATE KEY-----\n"
    "plain line\n")
RAW_SECRETS = ("hunter2", "abc123xyz", "pw123", "abcdefghijkl", "MIIEvQIBADANBgkq", "AAAAAAAAAAAAAAAA")


def test_body_masked_with_line_map():
    v = view("type: Reference\n", BODY)
    assert all(raw not in v.body for raw in RAW_SECRETS)
    assert v.body.count("\n") == BODY.count("\n")
    lines = v.body.split("\n")
    assert lines[0] == "# Title" and lines[1] == "password: ***" and lines[2] == "token = ***"
    assert lines[3] == "url https://user:***@host/x" and lines[4] == "Authorization: Bearer ***"
    assert lines[5] == "-----BEGIN RSA PRIVATE KEY-----" and lines[6] == "[ZAMASKOWANO]" and lines[7] == ""
    assert lines[8] == "-----END RSA PRIVATE KEY-----" and lines[9] == "plain line"
    assert v.body_start_line == 4
    assert v.masked_body_lines == (5, 6, 7, 8, 10, 11)
    assert v.masked


def test_masking_is_idempotent():
    once = mask_text(BODY)
    again = mask_text(once.text)
    assert once.count > 0 and again.count == 0 and again.text == once.text and again.lines == ()


def test_crlf_preserved_with_pem():
    text = "a\r\n-----BEGIN PRIVATE KEY-----\r\nSECRETDATA\r\nMOREDATA\r\n-----END PRIVATE KEY-----\r\nb\r\n"
    result = mask_text(text)
    assert result.text == ("a\r\n-----BEGIN PRIVATE KEY-----\r\n[ZAMASKOWANO]\r\n\r\n"
                           "-----END PRIVATE KEY-----\r\nb\r\n")
    assert result.lines == (3, 4)


def test_pem_variants():
    raw = "x\n-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\nBBBB\n"
    unterminated = mask_text(raw)
    assert "AAAA" not in unterminated.text and "BBBB" not in unterminated.text
    assert unterminated.text.count("\n") == raw.count("\n") == 4
    inline = mask_text("k: -----BEGIN PRIVATE KEY----- ABCDEF -----END PRIVATE KEY-----")
    assert "ABCDEF" not in inline.text and inline.count == 1
    tail = mask_text("-----BEGIN PRIVATE KEY-----\nAAAA\nBBBB-----END PRIVATE KEY-----\n")
    assert "AAAA" not in tail.text and "BBBB" not in tail.text and tail.text.endswith("-----END PRIVATE KEY-----\n")
    empty = mask_text("-----BEGIN PRIVATE KEY-----\n-----END PRIVATE KEY-----\n")
    assert empty.count == 0


def test_block_scalar_masked_until_dedent():
    result = mask_text("intro\nsecret: |\n  line1\n  line2\n\nafter: ok\n")
    assert result.text == "intro\nsecret: |\n  ***\n  ***\n\nafter: ok\n" and result.count == 1
    assert mask_text(result.text).count == 0


def test_code_fence_and_json_style_masked():
    text = "```yaml\ndb:\n  password: hunter2\n```\n```json\n{\n  \"api_key\": \"abc123\",\n  \"n\": 1\n}\n```\n"
    result = mask_text(text)
    assert "hunter2" not in result.text and "abc123" not in result.text
    assert '"api_key": "***",' in result.text and '"n": 1' in result.text
    assert result.text.count("\n") == text.count("\n")


def test_list_and_quoted_assignments():
    result = mask_text("- token: abc\n* secret = 'xyz'\n> passwd: qqq\n")
    assert result.text == "- token: ***\n* secret = \"***\"\n> passwd: ***\n"


def test_false_positives_left_alone():
    text = ("author: someone\nmonkey: banana\nmax_tokens: 100\nkeyboard: x\n## Token handling\n"
            "see http://example.com/path and ftp://host:21/file\nBearer short\n")
    assert mask_text(text).count == 0 and mask_text(text).text == text


def test_headings_survive_masking():
    v = view("type: X\n", "# A\npassword: x\n## B\ntext\n")
    assert v.body.split("\n")[0] == "# A" and v.body.split("\n")[2] == "## B"


def test_very_long_lines_stay_fast():
    mask_text("-" * 200000 + "\n")
    mask_text("a" * 200000 + "\n")
    mask_text("- " * 100000 + "\n")


# --- tożsamość, błędy, YAML publiczny ---

def test_unsafe_identity_blocked():
    ident = DocumentIdentity("general", "notes/password=abc123")
    with pytest.raises(PublicViewError) as info:
        build_public_view(make_doc("type: X\n", ident=ident), NOW)
    assert info.value.code == "IDENTITY_UNSAFE" and "abc123" not in str(info.value)


def test_invalid_document_has_no_view():
    with pytest.raises(PublicViewError) as info:
        build_public_view(make_doc("title: no type\n"), NOW)
    assert info.value.code == "INVALID_OKF"


def test_public_yaml_is_synthetic_and_safe():
    v = view(SECRET_META)
    text = render_public_yaml(v.metadata)
    assert text.startswith("# widok publiczny")
    assert "'***'" in text
    assert yaml.safe_load(text) == v.metadata
    for raw in ("abc123", "s3cr3t", "tok999", "hunter2"):
        assert raw not in text


def test_masked_dates_and_unknown_fields_survive():
    v = view("type: X\nx-ext: {nested: [1, 2]}\nusage_window: { from: 2026-06-01T00:00:00Z, to: 2026-06-30T00:00:00Z }\n")
    assert v.metadata["x-ext"] == {"nested": [1, 2]}
    assert v.metadata["usage_window"] == {"from": "2026-06-01T00:00:00Z", "to": "2026-06-30T00:00:00Z"}
