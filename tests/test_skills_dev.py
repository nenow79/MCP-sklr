"""P1: źródło dev, dostępność i polityka; bez uruchamiania procesów."""
import sys

import pytest

from server.skills_tools import (
    PreparedRun, activate_skill_impl, list_skills_impl,
    prepare_script_run, read_skill_resource_impl,
)
from server.task_tools import run_skill_script_impl
from skills_engine import SOURCE_CENTRAL, SOURCE_DEV, SOURCE_PROJECT, SkillRegistry, scan_skills
from skills_engine.policy import decide_confirmation
from skills_engine.trust import TrustStore, compute_skill_hash
from state import load_config


def skill(root, name="demo", description="d"):
    folder = root / name
    (folder / "scripts").mkdir(parents=True)
    (folder / "references").mkdir()
    (folder / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: {description}\nmetadata:\n'
        '  vibecoder-confirm: "false"\n---\nbody\n', encoding="utf-8")
    (folder / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    (folder / "references" / "guide.md").write_text("guide\n", encoding="utf-8")
    return folder


def test_dev_scan_without_other_sources(tmp_path):
    dev = tmp_path / "dev"
    skill(dev)
    result = scan_skills(None, None, [dev])
    assert result.skills["demo"].source == SOURCE_DEV


@pytest.mark.parametrize("central,project,expected", [
    (True, False, SOURCE_CENTRAL),
    (False, True, SOURCE_PROJECT),
    (True, True, SOURCE_PROJECT),
])
def test_source_priority(tmp_path, central, project, expected):
    dev = tmp_path / "dev"
    c = tmp_path / "central"
    p = tmp_path / "project"
    skill(dev, description="dev")
    if central:
        skill(c, description="central")
    if project:
        skill(p / ".agents" / "skills", description="project")
    result = scan_skills(c, p, [dev])
    assert result.skills["demo"].source == expected
    assert len(result.shadowed) == int(central) + int(project)
    assert result.diagnostics


def test_first_dev_directory_wins(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    skill(a, description="first")
    skill(b, description="second")
    result = scan_skills(None, None, [a, b])
    assert result.skills["demo"].description == "first"
    assert len(result.shadowed) == 1


def test_missing_dev_directory_allowed(tmp_path):
    assert scan_skills(None, None, [tmp_path / "missing"]).skills == {}


def test_dev_scan_depth_limit(tmp_path):
    dev = tmp_path / "dev"
    skill(dev / "a" / "b" / "c", "visible")
    skill(dev / "a" / "b" / "c" / "d", "hidden")
    result = scan_skills(None, None, [dev])
    assert set(result.skills) == {"visible"}


def test_dev_scan_directory_limit(tmp_path, monkeypatch):
    from skills_engine import scanner
    monkeypatch.setattr(scanner, "MAX_DIRS", 2)
    dev = tmp_path / "dev"
    for name in ("a", "b", "c"):
        skill(dev, name)
    result = scan_skills(None, None, [dev])
    assert len(result.skills) < 3
    assert any("limit" in d.message for d in result.diagnostics)


def test_dev_flags_and_snapshot(tmp_path):
    dev = tmp_path / "dev"
    skill(dev)
    registry = SkillRegistry(dev_dirs=[dev], disabled=["demo"])
    registry.rescan()
    assert registry.is_disabled("demo")
    assert registry.get("demo") is None
    assert registry.names() == []
    assert registry.snapshot().disabled == ["demo"]
    registry.set_dev_enabled(["demo"])
    assert registry.get("demo").source == SOURCE_DEV
    assert registry.names() == ["demo"]
    assert registry.snapshot().disabled == []
    registry.set_dev_enabled([])
    assert registry.get("demo") is None


def test_dev_dir_setter_requires_rescan(tmp_path):
    dev = tmp_path / "dev"
    skill(dev)
    registry = SkillRegistry(dev_enabled=["demo"])
    registry.set_dev_dirs([dev])
    assert registry.get("demo") is None
    registry.rescan()
    assert registry.get("demo").source == SOURCE_DEV


def test_disabled_winner_does_not_expose_dev(tmp_path):
    dev, central = tmp_path / "dev", tmp_path / "central"
    skill(dev)
    skill(central)
    registry = SkillRegistry(central_dir=central, dev_dirs=[dev],
                             disabled=["demo"], dev_enabled=["demo"])
    registry.rescan()
    assert registry.get("demo") is None
    entries = registry.list_all()
    assert len(entries) == 2
    assert entries[0]["record"].source == SOURCE_CENTRAL
    assert entries[0]["enabled"] is False
    assert entries[0]["shadowed"] is False
    assert entries[1]["record"].source == SOURCE_DEV
    assert entries[1]["enabled"] is False
    assert entries[1]["shadowed"] is True
    registry.set_disabled([])
    assert registry.get("demo").source == SOURCE_CENTRAL


@pytest.mark.parametrize("trusted", [False, True])
def test_dev_confirmation_always_required(trusted):
    decision = decide_confirmation(SOURCE_DEV, {"vibecoder-confirm": "false"}, trusted)
    assert decision.required is True
    assert decision.offer_trust is False
    assert "deweloperski" in decision.reason


@pytest.mark.parametrize("source", [SOURCE_CENTRAL, SOURCE_PROJECT, SOURCE_DEV])
def test_disabled_skill_blocked_in_all_four_tools(tmp_path, source):
    root = tmp_path / "skills"
    if source == SOURCE_PROJECT:
        folder = skill(root / ".agents" / "skills")
        registry = SkillRegistry(project_root=root, disabled=["demo"])
    elif source == SOURCE_CENTRAL:
        folder = skill(root)
        registry = SkillRegistry(central_dir=root, disabled=["demo"])
    else:
        folder = skill(root)
        registry = SkillRegistry(dev_dirs=[root])
    config = {"skills": {"trust": {}}}
    trust = TrustStore(config)
    trust.trust("demo", compute_skill_hash(folder))
    data = list_skills_impl(registry)["data"]
    assert data["skills"] == []
    assert data["disabled_count"] == 1
    assert "disabled" not in data
    for result in (
        activate_skill_impl(registry, "demo"),
        read_skill_resource_impl(registry, "demo", "references/guide.md"),
        prepare_script_run(registry, config, trust, "demo", "scripts/run.py"),
    ):
        assert result["success"] is False
        assert "wyłączony" in result["error"]

    class NoSubmit:
        def submit(self, *args, **kwargs):
            pytest.fail("Wyłączony skill nie może utworzyć zadania")

    result = run_skill_script_impl(
        registry, config, trust, NoSubmit(), "demo", "scripts/run.py", wait=0)
    assert result["success"] is False
    assert "wyłączony" in result["error"]


def test_enabled_dev_activation_resources_and_preparation(tmp_path):
    dev = tmp_path / "dev"
    folder = skill(dev)
    registry = SkillRegistry(dev_dirs=[dev], dev_enabled=["demo"])
    config = {"skills": {"trust": {}}}
    trust = TrustStore(config)
    trust.trust("demo", compute_skill_hash(folder))
    assert activate_skill_impl(registry, "demo")["success"] is True
    assert read_skill_resource_impl(
        registry, "demo", "references/guide.md")["data"]["content"] == "guide\n"
    prepared = prepare_script_run(
        registry, config, trust, "demo", "scripts/run.py", default_python=sys.executable)
    assert isinstance(prepared, PreparedRun)
    assert prepared.source == SOURCE_DEV
    assert prepared.approval_required is True
    assert prepared.offer_trust is False
    assert prepared.command[0] == sys.executable
    assert list_skills_impl(registry)["data"]["skills"][0]["source"] == SOURCE_DEV


def test_legacy_config_gets_empty_dev_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"skills": {"disabled": ["demo"]}}', encoding="utf-8")
    config = load_config(path)
    assert config["skills"]["dev_dirs"] == []
    assert config["skills"]["dev_enabled"] == []
    assert config["skills"]["disabled"] == ["demo"]
