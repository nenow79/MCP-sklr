from skills_engine import SOURCE_CENTRAL, parse_skill_file


def _write(tmp_path, folder, content):
    skill_dir = tmp_path / folder
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_valid_skill(tmp_path):
    path = _write(tmp_path, "pdf-processing", (
        "---\nname: pdf-processing\n"
        "description: Extract PDF text. Use when handling PDFs.\n"
        "license: Apache-2.0\n"
        "metadata:\n  author: org\n  version: \"1.0\"\n"
        "---\n# Body\n"
    ))
    result = parse_skill_file(path, SOURCE_CENTRAL)
    assert result.record is not None
    assert result.record.name == "pdf-processing"
    assert result.record.license == "Apache-2.0"
    assert result.record.metadata == {"author": "org", "version": "1.0"}
    assert result.record.warnings == []
    assert result.record.base_dir == path.parent.resolve()


def test_missing_description_is_skipped(tmp_path):
    path = _write(tmp_path, "x", "---\nname: x\n---\nbody\n")
    result = parse_skill_file(path, SOURCE_CENTRAL)
    assert result.record is None
    assert result.diagnostics[0].level == "error"


def test_empty_description_is_skipped(tmp_path):
    path = _write(tmp_path, "x", "---\nname: x\ndescription: \"\"\n---\n")
    assert parse_skill_file(path, SOURCE_CENTRAL).record is None


def test_no_frontmatter_is_skipped(tmp_path):
    path = _write(tmp_path, "x", "# Just markdown\n")
    assert parse_skill_file(path, SOURCE_CENTRAL).record is None


def test_unclosed_frontmatter_is_skipped(tmp_path):
    path = _write(tmp_path, "x", "---\nname: x\ndescription: d\n")
    assert parse_skill_file(path, SOURCE_CENTRAL).record is None


def test_unparsable_yaml_is_skipped(tmp_path):
    path = _write(tmp_path, "x", "---\nname: [unclosed\ndescription: d\n---\n")
    assert parse_skill_file(path, SOURCE_CENTRAL).record is None


def test_colon_in_description_is_repaired(tmp_path):
    path = _write(tmp_path, "pdf", (
        "---\nname: pdf\n"
        "description: Use this skill when: the user asks about PDFs\n"
        "---\nbody\n"
    ))
    result = parse_skill_file(path, SOURCE_CENTRAL)
    assert result.record is not None
    assert result.record.description == "Use this skill when: the user asks about PDFs"
    assert any("naprawiony" in w for w in result.record.warnings)


def test_name_mismatch_warns_but_loads(tmp_path):
    path = _write(tmp_path, "folder-name", "---\nname: other-name\ndescription: d\n---\n")
    result = parse_skill_file(path, SOURCE_CENTRAL)
    assert result.record is not None
    assert result.record.name == "other-name"
    assert any("różni się" in w for w in result.record.warnings)


def test_invalid_name_chars_warn(tmp_path):
    path = _write(tmp_path, "PDF--Proc", "---\nname: PDF--Proc\ndescription: d\n---\n")
    result = parse_skill_file(path, SOURCE_CENTRAL)
    assert result.record is not None
    assert any("łamie reguły" in w for w in result.record.warnings)


def test_too_long_name_warns(tmp_path):
    long_name = "a" * 65
    path = _write(tmp_path, long_name, f"---\nname: {long_name}\ndescription: d\n---\n")
    result = parse_skill_file(path, SOURCE_CENTRAL)
    assert result.record is not None
    assert any("limit 64" in w for w in result.record.warnings)


def test_missing_name_uses_folder(tmp_path):
    path = _write(tmp_path, "my-skill", "---\ndescription: d\n---\n")
    result = parse_skill_file(path, SOURCE_CENTRAL)
    assert result.record is not None
    assert result.record.name == "my-skill"
    assert any("Brak pola 'name'" in w for w in result.record.warnings)


def test_metadata_values_are_stringified(tmp_path):
    path = _write(tmp_path, "m", (
        "---\nname: m\ndescription: d\nmetadata:\n  conda-env: data\n  count: 3\n  flag: false\n---\n"
    ))
    result = parse_skill_file(path, SOURCE_CENTRAL)
    assert result.record.metadata == {"conda-env": "data", "count": "3", "flag": "False"}


def test_bom_and_crlf_are_handled(tmp_path):
    skill_dir = tmp_path / "bom"
    skill_dir.mkdir()
    path = skill_dir / "SKILL.md"
    path.write_bytes(b"\xef\xbb\xbf---\r\nname: bom\r\ndescription: d\r\n---\r\nbody\r\n")
    result = parse_skill_file(path, SOURCE_CENTRAL)
    assert result.record is not None
    assert result.record.name == "bom"
