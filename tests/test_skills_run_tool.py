import sys

from server.skills_tools import run_skill_script_impl
from skills_engine import SkillRegistry
from skills_engine.trust import TrustStore


class FakeApprover:
    def __init__(self, answer="allow"):
        self.answer = answer
        self.calls = []

    def __call__(self, title, message, allow_trust, timeout_s):
        self.calls.append({"title": title, "message": message, "allow_trust": allow_trust, "timeout_s": timeout_s})
        return self.answer


def _setup(tmp_path, metadata="", source_dir="central", script_body="import sys; print('ran', sys.argv[1:])"):
    root = tmp_path / source_dir
    skill_dir = root / "demo"
    (skill_dir / "scripts").mkdir(parents=True)
    meta = f"metadata:\n{metadata}" if metadata else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: demo\ndescription: d\n{meta}---\nbody\n", encoding="utf-8"
    )
    (skill_dir / "scripts" / "run.py").write_text(script_body, encoding="utf-8")
    return root, skill_dir


def _call(tmp_path, registry, approver, config=None, **kwargs):
    config = config if config is not None else {"skills": {"trust": {}}}
    store = TrustStore(config)
    params = dict(skill="demo", script="scripts/run.py", args=["a", "b"], timeout=30, default_python=sys.executable)
    params.update(kwargs)
    return run_skill_script_impl(registry, config, store, approver, **params), config, store


def test_default_central_skill_asks_and_runs(tmp_path):
    central, _ = _setup(tmp_path)
    registry = SkillRegistry(central_dir=central)
    approver = FakeApprover("allow")
    result, _, _ = _call(tmp_path, registry, approver)
    assert result["success"] is True
    assert "ran ['a', 'b']" in result["data"]["stdout"]
    assert len(approver.calls) == 1
    assert approver.calls[0]["allow_trust"] is False
    assert "scripts/run.py" in approver.calls[0]["message"]
    assert "print('ran'" in approver.calls[0]["message"]  # podgląd skryptu


def test_denied_does_not_run(tmp_path):
    central, skill_dir = _setup(tmp_path, script_body="open('marker.txt', 'w').write('x')")
    registry = SkillRegistry(central_dir=central)
    result, _, _ = _call(tmp_path, registry, FakeApprover("deny"))
    assert result["success"] is False
    assert "zablokował" in result["error"]
    assert not (skill_dir / "marker.txt").exists()


def test_timeout_answer_does_not_run(tmp_path):
    central, skill_dir = _setup(tmp_path, script_body="open('marker.txt', 'w').write('x')")
    registry = SkillRegistry(central_dir=central)
    result, _, _ = _call(tmp_path, registry, FakeApprover("timeout"))
    assert result["success"] is False
    assert "Brak odpowiedzi" in result["error"]
    assert not (skill_dir / "marker.txt").exists()


def test_flagged_skill_first_run_offers_trust_then_skips(tmp_path):
    central, _ = _setup(tmp_path, metadata='  vibecoder-confirm: "false"\n')
    registry = SkillRegistry(central_dir=central)
    config = {"skills": {"trust": {}}}

    approver = FakeApprover("trust")
    first, _, store = _call(tmp_path, registry, approver, config=config)
    assert first["success"] is True
    assert approver.calls[0]["allow_trust"] is True
    assert "Ufaj" in approver.calls[0]["title"]
    assert "demo" in config["skills"]["trust"]

    second_approver = FakeApprover("deny")
    second, _, _ = _call(tmp_path, registry, second_approver, config=config)
    assert second["success"] is True
    assert second_approver.calls == []  # bez pytania


def test_allow_without_trust_does_not_store_trust(tmp_path):
    central, _ = _setup(tmp_path, metadata='  vibecoder-confirm: "false"\n')
    registry = SkillRegistry(central_dir=central)
    config = {"skills": {"trust": {}}}
    _call(tmp_path, registry, FakeApprover("allow"), config=config)
    assert config["skills"]["trust"] == {}


def test_modified_script_invalidates_trust(tmp_path):
    central, skill_dir = _setup(tmp_path, metadata='  vibecoder-confirm: "false"\n')
    registry = SkillRegistry(central_dir=central)
    config = {"skills": {"trust": {}}}
    _call(tmp_path, registry, FakeApprover("trust"), config=config)

    (skill_dir / "scripts" / "run.py").write_text("print('changed')", encoding="utf-8")
    approver = FakeApprover("allow")
    result, _, _ = _call(tmp_path, registry, approver, config=config)
    assert result["success"] is True
    assert len(approver.calls) == 1  # zaufanie unieważnione


def test_project_skill_ignores_flag_and_always_asks(tmp_path):
    project = tmp_path / "proj"
    skills_root = project / ".agents" / "skills"
    skill_dir = skills_root / "demo"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nname: demo\ndescription: d\nmetadata:\n  vibecoder-confirm: "false"\n---\nbody\n', encoding="utf-8"
    )
    (skill_dir / "scripts" / "run.py").write_text("print('p')", encoding="utf-8")
    registry = SkillRegistry(central_dir=tmp_path / "empty", project_root=project)

    approver = FakeApprover("allow")
    result, _, _ = _call(tmp_path, registry, approver)
    assert result["success"] is True
    assert len(approver.calls) == 1
    assert approver.calls[0]["allow_trust"] is False


def test_skill_changed_during_approval_is_rejected(tmp_path):
    central, skill_dir = _setup(tmp_path)
    registry = SkillRegistry(central_dir=central)

    def tampering_approver(title, message, allow_trust, timeout_s):
        (skill_dir / "scripts" / "run.py").write_text("print('evil')", encoding="utf-8")
        return "allow"

    result, _, _ = _call(tmp_path, registry, tampering_approver)
    assert result["success"] is False
    assert "zmienił się" in result["error"]


def test_nonzero_exit_reports_failure_with_data(tmp_path):
    central, _ = _setup(tmp_path, script_body="import sys; print('boom', file=sys.stderr); sys.exit(4)")
    registry = SkillRegistry(central_dir=central)
    result, _, _ = _call(tmp_path, registry, FakeApprover("allow"))
    assert result["success"] is False
    assert "kodem 4" in result["error"]
    assert result["data"]["exit_code"] == 4
    assert result["data"]["stderr"] == "boom"


def test_timeout_reports_failure_with_partial_output(tmp_path):
    central, _ = _setup(
        tmp_path, script_body="import time\nprint('partial', flush=True)\ntime.sleep(30)\n"
    )
    registry = SkillRegistry(central_dir=central)
    result, _, _ = _call(tmp_path, registry, FakeApprover("allow"), timeout=2)
    assert result["success"] is False
    assert "timeout" in result["error"]
    assert result["data"]["timed_out"] is True
    assert "partial" in result["data"]["stdout"]


def test_invalid_timeout_and_args(tmp_path):
    central, _ = _setup(tmp_path)
    registry = SkillRegistry(central_dir=central)
    approver = FakeApprover("allow")
    assert _call(tmp_path, registry, approver, timeout=0)[0]["success"] is False
    assert _call(tmp_path, registry, approver, timeout=9999)[0]["success"] is False
    assert _call(tmp_path, registry, approver, args="not-a-list")[0]["success"] is False
    assert _call(tmp_path, registry, approver, args=[1, 2])[0]["success"] is False
    assert approver.calls == []


def test_unknown_skill_and_bad_script_path(tmp_path):
    central, _ = _setup(tmp_path)
    registry = SkillRegistry(central_dir=central)
    approver = FakeApprover("allow")
    assert _call(tmp_path, registry, approver, skill="nope")[0]["success"] is False
    assert _call(tmp_path, registry, approver, script="../SKILL.md")[0]["success"] is False
    assert _call(tmp_path, registry, approver, script="SKILL.md")[0]["success"] is False
    assert _call(tmp_path, registry, approver, script="scripts/missing.py")[0]["success"] is False
    assert approver.calls == []


def test_missing_conda_env_returns_instructions(tmp_path, monkeypatch):
    central, _ = _setup(tmp_path, metadata="  conda-env: no-such-env-xyz\n")
    registry = SkillRegistry(central_dir=central)
    approver = FakeApprover("allow")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "fakehome")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    result, _, _ = _call(tmp_path, registry, approver, config={"skills": {"trust": {}}, "conda": {"base": None, "envs": {}}})
    assert result["success"] is False
    assert "conda create -n no-such-env-xyz" in result["error"]
    assert approver.calls == []  # bez środowiska nie ma sensu pytać


def test_conda_env_python_is_used(tmp_path):
    central, _ = _setup(tmp_path, metadata="  conda-env: myenv\n", script_body="import sys; print('v')")
    registry = SkillRegistry(central_dir=central)
    # jawna ścieżka do interpretera środowiska = bieżący python
    config = {"skills": {"trust": {}}, "conda": {"base": None, "envs": {"myenv": sys.executable}}}
    approver = FakeApprover("allow")
    result, _, _ = _call(tmp_path, registry, approver, config=config, default_python="SHOULD-NOT-BE-USED")
    assert result["success"] is True
    assert sys.executable in approver.calls[0]["message"]


def test_disabled_skill_cannot_run(tmp_path):
    central, _ = _setup(tmp_path)
    registry = SkillRegistry(central_dir=central, disabled=["demo"])
    result, _, _ = _call(tmp_path, registry, FakeApprover("allow"))
    assert result["success"] is False
    assert "wyłączony" in result["error"]
