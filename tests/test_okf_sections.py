"""Samodzielne testy C2a: publiczne body, bez dysku, indeksu i MCP."""
from datetime import datetime, timezone

import pytest

from okf_engine.limits import MAX_SECTIONS_PER_DOCUMENT
from okf_engine.models import DocumentIdentity
from okf_engine.parser import parse_document
from okf_engine.public_view import build_public_view
from okf_engine.sections import WARN_SECTION_LIMIT, build_outline


def test_hierarchy_repeated_headings_and_modes():
    outline = build_outline("intro\n# A\none\n### A\nchild\n## B\nb\n# A\nlast", 4)
    assert [(s.id, s.heading, s.level, s.parent_id) for s in outline.entries] == [
        ("s0", None, 0, None), ("s1", "A", 1, None),
        ("s2", "A", 3, "s1"), ("s3", "B", 2, "s1"), ("s4", "A", 1, None)]
    assert [(s.start_line, s.end_line, s.subtree_end_line) for s in outline.entries] == [
        (4, 4, 4), (5, 6, 10), (7, 8, 8), (9, 10, 10), (11, 12, 12)]
    assert outline.get_section("s1", "direct").content == "# A\none\n"
    assert outline.get_section("s1").content == "# A\none\n### A\nchild\n## B\nb\n"
    assert outline.get_section("s4").content == "# A\nlast"
    assert outline.get_section("s0").content == "intro\n"
    assert not outline.partial and outline.warnings == ()


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
def test_setext_and_physical_lines(eol):
    body = eol.join(["Title", "===", "", "Subtitle", "---", "text", "# End"])
    outline = build_outline(body, 8)
    assert [(s.heading, s.level, s.start_line, s.end_line) for s in outline.entries] == [
        ("Title", 1, 8, 10), ("Subtitle", 2, 11, 13), ("End", 1, 14, 14)]
    assert outline.get_section("s2").content == eol.join(["Subtitle", "---", "text", ""])
    assert outline.get_section("s1").end_line == 13


@pytest.mark.parametrize("body", [
    "```md\n# hidden\nSetext\n===\n```\n# visible\n",
    "~~~~\n# hidden\n~~~\n# still hidden\n~~~~~\n# visible\n",
    "   ```\n# hidden\n``` trailing\n# still hidden\n   ```  \n# visible\n",
    "    # hidden\n\t# hidden\n    Setext\n    ===\n# visible\n",
])
def test_headings_in_code_ignored(body):
    outline = build_outline(body, 1)
    assert [s.heading for s in outline.entries if s.level] == ["visible"]


def test_unclosed_fence_and_wrong_delimiter():
    outline = build_outline("# A\n```\n# hidden\n~~~\n## hidden too\n", 1)
    assert len(outline.entries) == 1
    assert outline.get_section("s1").end_line == 5


@pytest.mark.parametrize("line,title,level", [
    ("#", "", 1), ("###   ###", "", 3), ("  ## A ###  ", "A", 2),
    ("###### Six", "Six", 6), ("# A#", "A#", 1), ("# A ### x", "A ### x", 1),
])
def test_atx_profile(line, title, level):
    section = build_outline(line, 1).entries[0]
    assert (section.id, section.heading, section.level) == ("s1", title, level)


@pytest.mark.parametrize("body", [
    "####### no\n", "#nospace\n", "    # indented\n", "\t# indented\n",
    "---\n", "> quoted\n===\n", "- item\n---\n", "first\nsecond\n===\n",
])
def test_unsupported_variants_remain_body(body):
    outline = build_outline(body, 1)
    assert [s.id for s in outline.entries] == ["s0"]
    assert outline.get_section("s0").content == body


def test_invalid_backtick_info_is_not_a_fence():
    outline = build_outline("```bad`info\n# visible\n", 1)
    assert outline.entries[-1].heading == "visible"


def test_empty_body_and_blank_preamble():
    assert build_outline("", 4).entries == ()
    outline = build_outline("\n\n# A\n", 4)
    assert outline.entries[0].id == "s0"
    assert outline.get_section("s0").content == "\n\n"
    assert outline.entries[1].start_line == 6
    assert [s.id for s in build_outline("# A\n", 1).entries] == ["s1"]


def test_unicode_line_separators_are_not_physical_lines():
    outline = build_outline("# A\ntext\u2028# not heading\u0085more\n# B", 10)
    assert [s.start_line for s in outline.entries] == [10, 12]
    assert outline.get_section("s1").end_line == 11


def test_public_view_masking_preserves_ranges_and_content():
    raw = ("---\r\ntype: Reference\r\n---\r\n# A\r\n"
           "-----BEGIN PRIVATE KEY-----\r\nRAWKEYONE\r\nRAWKEYTWO\r\n"
           "-----END PRIVATE KEY-----\r\n## B\r\npassword: hiddenvalue\r\n# C\r\n")
    doc = parse_document(raw.encode(), DocumentIdentity("general", "sample"))
    public = build_public_view(doc, datetime(2026, 6, 1, tzinfo=timezone.utc))
    outline = build_outline(public.body, public.body_start_line)
    assert [(s.start_line, s.end_line, s.subtree_end_line) for s in outline.entries] == [
        (4, 8, 10), (9, 10, 10), (11, 11, 11)]
    content = outline.get_section("s1").content
    assert "RAWKEYONE" not in content and "RAWKEYTWO" not in content
    assert "hiddenvalue" not in content and "password: ***" in content
    assert content.count("\r\n") == 7


@pytest.mark.parametrize("preamble", ["", "intro\n"])
def test_exact_limit_is_complete(preamble):
    count = MAX_SECTIONS_PER_DOCUMENT - bool(preamble)
    outline = build_outline(preamble + "# A\n" * count, 1)
    assert len(outline.entries) == MAX_SECTIONS_PER_DOCUMENT
    assert not outline.partial and outline.warnings == ()


@pytest.mark.parametrize("preamble", ["", "intro\n"])
def test_limit_is_explicit_and_omitted_sections_not_absorbed(preamble):
    count = MAX_SECTIONS_PER_DOCUMENT - bool(preamble)
    body = preamble + "# A\n" * count + "# omitted\nprivate to omitted section\n"
    outline = build_outline(body, 7)
    assert len(outline.entries) == MAX_SECTIONS_PER_DOCUMENT
    assert outline.partial and outline.warnings == (WARN_SECTION_LIMIT,)
    last = outline.entries[-1]
    assert last.end_line == last.subtree_end_line == 7 + count - 1 + bool(preamble)
    assert outline.get_section(last.id).content == "# A\n"
    with pytest.raises(ValueError, match="section_id"):
        outline.get_section("s" + str(count + 1))


def test_limit_caps_parent_subtree_before_omitted_setext():
    body = "# Parent\n" + "## Child\n" * (MAX_SECTIONS_PER_DOCUMENT - 1)
    outline = build_outline(body + "Omitted\n===\nrest\n", 1)
    assert outline.partial
    assert outline.entries[0].subtree_end_line == MAX_SECTIONS_PER_DOCUMENT
    assert "Omitted" not in outline.get_section("s1").content


@pytest.mark.parametrize("start", [True, False, 0, -1, 1.0, "1", None])
def test_invalid_start_line(start):
    with pytest.raises(ValueError, match="body_start_line"):
        build_outline("# A", start)


def test_invalid_body_and_section_arguments():
    with pytest.raises(ValueError, match="body"):
        build_outline(None, 1)
    outline = build_outline("# A", 1)
    with pytest.raises(ValueError, match="section_id"):
        outline.get_section("unknown")
    with pytest.raises(ValueError, match="section_mode"):
        outline.get_section("s1", "other")
