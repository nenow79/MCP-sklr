import os

import pytest

from skills_engine import SOURCE_CENTRAL, parse_skill_file
from skills_engine import content as content_module
from skills_engine.content import (
    build_skill_content,
    list_resources,
    read_body,
    read_resource_text,
    resolve_resource_path,
)


def _make_skill(tmp_path, name="demo", body="# Demo\nDo things.\n"):
    skill_dir = tmp_path / name
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "references").mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo skill\n---\n{body}", encoding="utf-8"
    )
    (skill_dir / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")
    (skill_dir / "references" / "guide.md").write_text("guide text\n", encoding="utf-8")
    record = parse_skill_file(skill_dir / "SKILL.md", SOURCE_CENTRAL).record
    return skill_dir, record


def test_read_body_strips_frontmatter(tmp_path):
    _, record = _make_skill(tmp_path)
    body = read_body(record)
    assert body.startswith("# Demo")
    assert "description:" not in body


def test_read_body_is_fresh_from_disk(tmp_path):
    skill_dir, record = _make_skill(tmp_path)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill\n---\nChanged\n", encoding="utf-8"
    )
    assert read_body(record) == "Changed"


def test_list_resources(tmp_path):
    skill_dir, _ = _make_skill(tmp_path)
    (skill_dir / "__pycache__").mkdir()
    (skill_dir / "__pycache__" / "x.pyc").write_bytes(b"x")
    resources, truncated = list_resources(skill_dir)
    assert resources == ["references/guide.md", "scripts/run.py"]
    assert truncated is False


def test_list_resources_cap(tmp_path):
    skill_dir, _ = _make_skill(tmp_path)
    for index in range(10):
        (skill_dir / "assets").mkdir(exist_ok=True)
        (skill_dir / "assets" / f"f{index}.txt").write_text("x", encoding="utf-8")
    resources, truncated = list_resources(skill_dir, limit=3)
    assert len(resources) == 3
    assert truncated is True


def test_build_skill_content(tmp_path):
    skill_dir, record = _make_skill(tmp_path)
    built = build_skill_content(record)
    text = built["content"]
    assert text.startswith('<skill_content name="demo">')
    assert text.rstrip().endswith("</skill_content>")
    assert "Do things." in text
    assert "description:" not in text
    assert f"Skill directory: {skill_dir.resolve()}" in text
    assert "<file>scripts/run.py</file>" in text
    assert "<file>references/guide.md</file>" in text
    assert built["warnings"] == []


def test_build_warns_on_long_body(tmp_path):
    long_body = "line\n" * 600
    _, record = _make_skill(tmp_path, body=long_body)
    built = build_skill_content(record)
    assert any("zalecane maks." in w for w in built["warnings"])


def test_resolve_ok(tmp_path):
    skill_dir, _ = _make_skill(tmp_path)
    target = resolve_resource_path(skill_dir, "references/guide.md")
    assert target == (skill_dir / "references" / "guide.md").resolve()


@pytest.mark.parametrize("bad", ["../outside.txt", "references/../../outside.txt"])
def test_resolve_blocks_traversal(tmp_path, bad):
    skill_dir, _ = _make_skill(tmp_path)
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_resource_path(skill_dir, bad)


def test_resolve_blocks_absolute(tmp_path):
    skill_dir, _ = _make_skill(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_resource_path(skill_dir, str(outside))


def test_resolve_blocks_empty(tmp_path):
    skill_dir, _ = _make_skill(tmp_path)
    with pytest.raises(ValueError):
        resolve_resource_path(skill_dir, "  ")


def test_resolve_missing_file(tmp_path):
    skill_dir, _ = _make_skill(tmp_path)
    with pytest.raises(FileNotFoundError):
        resolve_resource_path(skill_dir, "references/nope.md")


def test_resolve_blocks_symlink_escape(tmp_path):
    skill_dir, _ = _make_skill(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = skill_dir / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("Brak uprawnień do tworzenia symlinków")
    with pytest.raises(ValueError):
        resolve_resource_path(skill_dir, "link.txt")


def test_read_resource_text_and_truncation(tmp_path):
    path = tmp_path / "big.txt"
    path.write_text("a" * 5000, encoding="utf-8")
    text, truncated, total = read_resource_text(path, 1000)
    assert truncated is True
    assert total == 5000
    assert "skrócono" in text

    text, truncated, total = read_resource_text(path, 10000)
    assert truncated is False
    assert len(text) == 5000


def test_read_resource_blocks_binary(tmp_path):
    path = tmp_path / "bin.dat"
    path.write_bytes(b"abc\x00def")
    with pytest.raises(ValueError):
        read_resource_text(path)


def test_read_resource_blocks_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(content_module, "MAX_RESOURCE_BYTES", 10)
    path = tmp_path / "big.txt"
    path.write_text("x" * 100, encoding="utf-8")
    with pytest.raises(ValueError):
        read_resource_text(path)


def test_read_resource_cp1250_fallback(tmp_path):
    path = tmp_path / "pl.txt"
    path.write_bytes("zażółć".encode("cp1250"))
    text, _, _ = read_resource_text(path)
    assert text == "zażółć"
