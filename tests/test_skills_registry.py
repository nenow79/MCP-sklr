from skills_engine import SOURCE_PROJECT, SkillRegistry


def _skill(root, name, description="d"):
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n", encoding="utf-8"
    )


def test_empty_before_rescan(tmp_path):
    central = tmp_path / "central"
    _skill(central, "a")
    registry = SkillRegistry(central_dir=central)
    assert registry.list_active() == []
    assert registry.get("a") is None


def test_rescan_lists_sorted(tmp_path):
    central = tmp_path / "central"
    _skill(central, "b")
    _skill(central, "a")
    registry = SkillRegistry(central_dir=central)
    registry.rescan()
    assert registry.names() == ["a", "b"]
    assert registry.get("a").name == "a"


def test_disabled_are_filtered(tmp_path):
    central = tmp_path / "central"
    _skill(central, "a")
    _skill(central, "b")
    registry = SkillRegistry(central_dir=central, disabled=["a"])
    registry.rescan()
    assert registry.names() == ["b"]
    assert registry.get("a") is None
    assert registry.is_disabled("a")
    assert registry.snapshot().disabled == ["a"]


def test_set_disabled_takes_effect_without_rescan(tmp_path):
    central = tmp_path / "central"
    _skill(central, "a")
    registry = SkillRegistry(central_dir=central)
    registry.rescan()
    registry.set_disabled(["a"])
    assert registry.names() == []
    registry.set_disabled([])
    assert registry.names() == ["a"]


def test_new_skill_appears_after_rescan(tmp_path):
    central = tmp_path / "central"
    _skill(central, "a")
    registry = SkillRegistry(central_dir=central)
    registry.rescan()
    _skill(central, "new")
    assert registry.names() == ["a"]
    registry.rescan()
    assert registry.names() == ["a", "new"]


def test_project_root_setter_adds_project_skills(tmp_path):
    central = tmp_path / "central"
    project = tmp_path / "project"
    _skill(central, "a")
    _skill(project / ".agents" / "skills", "local")
    registry = SkillRegistry(central_dir=central)
    registry.rescan()
    assert registry.names() == ["a"]

    registry.set_project_root(project)
    registry.rescan()
    assert registry.names() == ["a", "local"]
    assert registry.get("local").source == SOURCE_PROJECT


def test_snapshot_reports_shadowed_and_diagnostics(tmp_path):
    central = tmp_path / "central"
    project = tmp_path / "project"
    _skill(central, "dup", "central")
    _skill(project / ".agents" / "skills", "dup", "project")
    registry = SkillRegistry(central_dir=central, project_root=project)
    registry.rescan()
    snap = registry.snapshot()
    assert snap.shadowed_count == 1
    assert snap.active[0].description == "project"
    assert snap.diagnostics
