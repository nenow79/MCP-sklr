from skills_engine import SOURCE_CENTRAL, SOURCE_PROJECT, scan_skills
from skills_engine import scanner as scanner_module


def _skill(root, folder, name=None, description="d"):
    skill_dir = root / folder
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name or skill_dir.name}\ndescription: {description}\n---\nbody\n",
        encoding="utf-8",
    )
    return skill_dir


def test_missing_dirs_return_empty(tmp_path):
    result = scan_skills(tmp_path / "nope", tmp_path / "noproject")
    assert result.skills == {}


def test_none_dirs_are_allowed():
    result = scan_skills(None, None)
    assert result.skills == {}


def test_discovers_central_skills(tmp_path):
    central = tmp_path / "central"
    _skill(central, "alpha")
    _skill(central, "beta")
    (central / "README.md").write_text("ignored", encoding="utf-8")

    result = scan_skills(central)
    assert sorted(result.skills) == ["alpha", "beta"]
    assert all(s.source == SOURCE_CENTRAL for s in result.skills.values())


def test_project_overrides_central(tmp_path):
    central = tmp_path / "central"
    project = tmp_path / "project"
    _skill(central, "code-review", description="central")
    _skill(project / ".agents" / "skills", "code-review", description="project")

    result = scan_skills(central, project)
    assert result.skills["code-review"].source == SOURCE_PROJECT
    assert result.skills["code-review"].description == "project"
    assert len(result.shadowed) == 1
    assert result.shadowed[0].source == SOURCE_CENTRAL
    assert any("przesłania" in d.message for d in result.diagnostics)


def test_same_scope_collision_first_wins(tmp_path):
    central = tmp_path / "central"
    _skill(central, "a-folder", name="dup", description="first")
    _skill(central, "b-folder", name="dup", description="second")

    result = scan_skills(central)
    assert result.skills["dup"].description == "first"
    assert len(result.shadowed) == 1
    assert any("Kolizja" in d.message for d in result.diagnostics)


def test_invalid_skill_is_reported_not_loaded(tmp_path):
    central = tmp_path / "central"
    _skill(central, "good")
    bad = central / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter", encoding="utf-8")

    result = scan_skills(central)
    assert list(result.skills) == ["good"]
    assert any(d.level == "error" for d in result.diagnostics)


def test_ignored_dirs_are_skipped(tmp_path):
    central = tmp_path / "central"
    _skill(central / "node_modules", "hidden")
    _skill(central / ".git", "hidden2")
    _skill(central, "visible")

    result = scan_skills(central)
    assert list(result.skills) == ["visible"]


def test_does_not_descend_into_skill_dir(tmp_path):
    central = tmp_path / "central"
    outer = _skill(central, "outer")
    _skill(outer / "scripts", "nested")

    result = scan_skills(central)
    assert list(result.skills) == ["outer"]


def test_nested_group_directory_is_found(tmp_path):
    central = tmp_path / "central"
    _skill(central / "group", "inner")

    result = scan_skills(central)
    assert list(result.skills) == ["inner"]


def test_max_depth_limit(tmp_path):
    central = tmp_path / "central"
    _skill(central / "l1" / "l2" / "l3", "ok-depth4")
    _skill(central / "l1" / "l2" / "l3" / "l4", "too-deep")

    result = scan_skills(central)
    assert "ok-depth4" in result.skills
    assert "too-deep" not in result.skills


def test_max_dirs_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_module, "MAX_DIRS", 3)
    central = tmp_path / "central"
    for index in range(6):
        _skill(central, f"s{index}")

    result = scan_skills(central)
    assert len(result.skills) < 6
    assert any("limit" in d.message for d in result.diagnostics)
