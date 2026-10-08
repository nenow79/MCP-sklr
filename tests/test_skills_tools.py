from server.skills_tools import (
    activate_skill_impl,
    list_skills_impl,
    read_skill_resource_impl,
)
from skills_engine import SkillRegistry


def _skill(root, name, description="d", with_files=True):
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        "metadata:\n  conda-env: data\n---\n# Body\nInstructions.\n",
        encoding="utf-8",
    )
    if with_files:
        (skill_dir / "references").mkdir(exist_ok=True)
        (skill_dir / "references" / "guide.md").write_text("guide\n", encoding="utf-8")
        (skill_dir / "scripts").mkdir(exist_ok=True)
        (skill_dir / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
        (skill_dir / "assets").mkdir(exist_ok=True)
        (skill_dir / "assets" / "bin.dat").write_bytes(b"a\x00b")
    return skill_dir


def _registry(tmp_path, disabled=()):
    central = tmp_path / "central"
    central.mkdir(exist_ok=True)
    return SkillRegistry(central_dir=central, disabled=disabled), central


def test_list_skills(tmp_path):
    registry, central = _registry(tmp_path)
    _skill(central, "alpha", "Alpha skill")
    _skill(central, "beta", "Beta skill")
    result = list_skills_impl(registry)
    assert result["success"] is True
    data = result["data"]
    assert data["count"] == 2
    assert [s["name"] for s in data["skills"]] == ["alpha", "beta"]
    assert data["skills"][0]["description"] == "Alpha skill"
    assert data["skills"][0]["source"] == "central"


def test_list_skills_reports_diagnostics_and_disabled(tmp_path):
    registry, central = _registry(tmp_path, disabled=["beta"])
    _skill(central, "alpha")
    _skill(central, "beta")
    bad = central / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter", encoding="utf-8")

    data = list_skills_impl(registry)["data"]
    assert data["count"] == 1
    assert data["disabled"] == ["beta"]
    assert any(d["level"] == "error" for d in data["diagnostics"])


def test_list_skills_picks_up_new_skill(tmp_path):
    registry, central = _registry(tmp_path)
    assert list_skills_impl(registry)["data"]["count"] == 0
    _skill(central, "late")
    assert list_skills_impl(registry)["data"]["count"] == 1


def test_activate_skill(tmp_path):
    registry, central = _registry(tmp_path)
    skill_dir = _skill(central, "alpha")
    result = activate_skill_impl(registry, "alpha")
    assert result["success"] is True
    data = result["data"]
    assert data["name"] == "alpha"
    assert data["metadata"] == {"conda-env": "data"}
    assert data["skill_directory"] == str(skill_dir.resolve())
    assert '<skill_content name="alpha">' in data["content"]
    assert "Instructions." in data["content"]
    assert "scripts/run.py" in data["resources"]


def test_activate_works_without_prior_list(tmp_path):
    registry, central = _registry(tmp_path)
    _skill(central, "alpha")
    assert activate_skill_impl(registry, "alpha")["success"] is True


def test_activate_unknown_lists_available(tmp_path):
    registry, central = _registry(tmp_path)
    _skill(central, "alpha")
    result = activate_skill_impl(registry, "nope")
    assert result["success"] is False
    assert "alpha" in result["error"]


def test_activate_disabled(tmp_path):
    registry, central = _registry(tmp_path, disabled=["alpha"])
    _skill(central, "alpha")
    result = activate_skill_impl(registry, "alpha")
    assert result["success"] is False
    assert "wyłączony" in result["error"]


def test_read_resource(tmp_path):
    registry, central = _registry(tmp_path)
    _skill(central, "alpha")
    result = read_skill_resource_impl(registry, "alpha", "references/guide.md")
    assert result["success"] is True
    assert result["data"]["content"] == "guide\n"
    assert result["data"]["path"] == "references/guide.md"
    assert result["data"]["truncated"] is False


def test_read_resource_traversal_blocked(tmp_path):
    registry, central = _registry(tmp_path)
    _skill(central, "alpha")
    (central / "secret.txt").write_text("secret", encoding="utf-8")
    result = read_skill_resource_impl(registry, "alpha", "../secret.txt")
    assert result["success"] is False
    assert "Path Traversal" in result["error"]


def test_read_resource_missing(tmp_path):
    registry, central = _registry(tmp_path)
    _skill(central, "alpha")
    result = read_skill_resource_impl(registry, "alpha", "references/nope.md")
    assert result["success"] is False


def test_read_resource_binary_blocked(tmp_path):
    registry, central = _registry(tmp_path)
    _skill(central, "alpha")
    result = read_skill_resource_impl(registry, "alpha", "assets/bin.dat")
    assert result["success"] is False
    assert "binarny" in result["error"]


def test_read_resource_unknown_skill(tmp_path):
    registry, _ = _registry(tmp_path)
    result = read_skill_resource_impl(registry, "nope", "x.md")
    assert result["success"] is False


def test_read_resource_invalid_max_chars(tmp_path):
    registry, central = _registry(tmp_path)
    _skill(central, "alpha")
    for bad in (10, 10**9, True, "5000"):
        result = read_skill_resource_impl(registry, "alpha", "references/guide.md", bad)
        assert result["success"] is False


def test_read_resource_truncation(tmp_path):
    registry, central = _registry(tmp_path)
    skill_dir = _skill(central, "alpha")
    (skill_dir / "references" / "long.md").write_text("z" * 5000, encoding="utf-8")
    result = read_skill_resource_impl(registry, "alpha", "references/long.md", 1000)
    assert result["success"] is True
    assert result["data"]["truncated"] is True
    assert result["data"]["total_chars"] == 5000
