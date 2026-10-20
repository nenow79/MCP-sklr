import json
import sys

import pytest

from skills_engine import SkillRegistry


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


def test_default_central_skill_asks_and_runs(tmp_path, make_env):
    central, _ = _setup(tmp_path)
    env = make_env(SkillRegistry(central_dir=central), "allow")
    result = env.call()
    assert result["success"] is True
    assert "ran ['a', 'b']" in result["data"]["stdout"]
    assert result["data"]["status"] == "done"
    assert len(env.requests) == 1
    request = env.requests[0]
    assert request.offer_trust is False
    assert request.script == "scripts/run.py"
    assert "print('ran'" in request.script_preview  # podgląd skryptu
    assert request.skill_dir.endswith("demo")


def test_denied_does_not_run(tmp_path, make_env):
    central, skill_dir = _setup(tmp_path, script_body="open('marker.txt', 'w').write('x')")
    env = make_env(SkillRegistry(central_dir=central), "deny")
    result = env.call()
    assert result["success"] is False
    assert "zablokował" in result["error"]
    assert not (skill_dir / "marker.txt").exists()


def test_timeout_answer_does_not_run(tmp_path, make_env):
    central, skill_dir = _setup(tmp_path, script_body="open('marker.txt', 'w').write('x')")
    env = make_env(SkillRegistry(central_dir=central), "timeout")
    result = env.call()
    assert result["success"] is False
    assert "Brak odpowiedzi" in result["error"]
    assert not (skill_dir / "marker.txt").exists()


def test_flagged_skill_first_run_offers_trust_then_skips(tmp_path, make_env):
    central, _ = _setup(tmp_path, metadata='  vibecoder-confirm: "false"\n')
    registry = SkillRegistry(central_dir=central)
    config = {"skills": {"trust": {}}}

    first_env = make_env(registry, "trust", config=config)
    first = first_env.call()
    assert first["success"] is True
    assert first_env.requests[0].offer_trust is True
    assert "demo" in config["skills"]["trust"]

    second_env = make_env(registry, "deny", config=config)
    second = second_env.call()
    assert second["success"] is True
    assert second_env.requests == []  # bez pytania


def test_allow_without_trust_does_not_store_trust(tmp_path, make_env):
    central, _ = _setup(tmp_path, metadata='  vibecoder-confirm: "false"\n')
    config = {"skills": {"trust": {}}}
    env = make_env(SkillRegistry(central_dir=central), "allow", config=config)
    assert env.call()["success"] is True
    assert config["skills"]["trust"] == {}


def test_modified_script_invalidates_trust(tmp_path, make_env):
    central, skill_dir = _setup(tmp_path, metadata='  vibecoder-confirm: "false"\n')
    registry = SkillRegistry(central_dir=central)
    config = {"skills": {"trust": {}}}
    make_env(registry, "trust", config=config).call()

    (skill_dir / "scripts" / "run.py").write_text("print('changed')", encoding="utf-8")
    env = make_env(registry, "allow", config=config)
    result = env.call()
    assert result["success"] is True
    assert len(env.requests) == 1  # zaufanie unieważnione


def test_project_skill_ignores_flag_and_always_asks(tmp_path, make_env):
    project = tmp_path / "proj"
    skills_root = project / ".agents" / "skills"
    skill_dir = skills_root / "demo"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nname: demo\ndescription: d\nmetadata:\n  vibecoder-confirm: "false"\n---\nbody\n', encoding="utf-8"
    )
    (skill_dir / "scripts" / "run.py").write_text("print('p')", encoding="utf-8")
    registry = SkillRegistry(central_dir=tmp_path / "empty", project_root=project)

    env = make_env(registry, "allow")
    result = env.call()
    assert result["success"] is True
    assert len(env.requests) == 1
    assert env.requests[0].offer_trust is False


def test_skill_changed_during_approval_is_rejected(tmp_path, make_env):
    central, skill_dir = _setup(tmp_path)
    registry = SkillRegistry(central_dir=central)

    def tamper(_task):
        (skill_dir / "scripts" / "run.py").write_text("print('evil')", encoding="utf-8")

    env = make_env(registry, "allow", before_decision=tamper)
    result = env.call()
    assert result["success"] is False
    assert "zmienił się" in result["error"]


def test_nonzero_exit_reports_failure_with_data(tmp_path, make_env):
    central, _ = _setup(tmp_path, script_body="import sys; print('boom', file=sys.stderr); sys.exit(4)")
    env = make_env(SkillRegistry(central_dir=central), "allow")
    result = env.call()
    assert result["success"] is False
    assert "kodem 4" in result["error"]
    assert result["data"]["exit_code"] == 4
    assert result["data"]["stderr"] == "boom"


def test_timeout_reports_failure_with_partial_output(tmp_path, make_env):
    central, _ = _setup(
        tmp_path, script_body="import time\nprint('partial', flush=True)\ntime.sleep(30)\n"
    )
    env = make_env(SkillRegistry(central_dir=central), "allow")
    result = env.call(timeout=2)
    assert result["success"] is False
    assert "timeout" in result["error"]
    assert result["data"]["timed_out"] is True
    assert result["data"]["status"] == "timed_out"
    assert "partial" in result["data"]["stdout"]


def test_invalid_timeout_and_args(tmp_path, make_env):
    central, _ = _setup(tmp_path)
    env = make_env(SkillRegistry(central_dir=central), "allow")
    assert env.call(timeout=0)["success"] is False
    assert env.call(timeout=9999)["success"] is False
    assert env.call(args="not-a-list")["success"] is False
    assert env.call(args=[1, 2])["success"] is False
    assert env.requests == []
    assert env.manager.list() == []  # błąd walidacji nie tworzy zadania


def test_unknown_skill_and_bad_script_path(tmp_path, make_env):
    central, _ = _setup(tmp_path)
    env = make_env(SkillRegistry(central_dir=central), "allow")
    assert env.call(name="nope")["success"] is False
    assert env.call(script="../SKILL.md")["success"] is False
    assert env.call(script="SKILL.md")["success"] is False
    assert env.call(script="scripts/missing.py")["success"] is False
    assert env.requests == []
    assert env.manager.list() == []


def test_missing_conda_env_returns_instructions(tmp_path, monkeypatch, make_env):
    central, _ = _setup(tmp_path, metadata="  conda-env: no-such-env-xyz\n")
    registry = SkillRegistry(central_dir=central)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "fakehome")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    config = {"skills": {"trust": {}}, "conda": {"base": None, "envs": {}}}
    env = make_env(registry, "allow", config=config)
    result = env.call()
    assert result["success"] is False
    assert "conda create -n no-such-env-xyz" in result["error"]
    assert env.requests == []  # bez środowiska nie ma sensu pytać


def test_conda_env_python_is_used(tmp_path, make_env):
    central, _ = _setup(tmp_path, metadata="  conda-env: myenv\n", script_body="import sys; print('v')")
    registry = SkillRegistry(central_dir=central)
    # jawna ścieżka do interpretera środowiska = bieżący python
    config = {"skills": {"trust": {}}, "conda": {"base": None, "envs": {"myenv": sys.executable}}}
    env = make_env(registry, "allow", config=config)
    result = env.call(default_python="SHOULD-NOT-BE-USED")
    assert result["success"] is True
    assert env.requests[0].command[0] == sys.executable


@pytest.mark.parametrize("metadata,expected", [
    ("", False), ('  network-env: ""\n', False),
    ('  network-env: "proxy-ca"\n', True),
])
def test_network_env_end_to_end(tmp_path, make_env, monkeypatch, metadata, expected):
    monkeypatch.setenv("HTTPS_PROXY", "SENTINEL-PROXY-CREDENTIAL")
    monkeypatch.setenv("SSL_CERT_FILE", "SENTINEL-CA-PATH")
    monkeypatch.setenv("DATABRICKS_TOKEN", "SENTINEL-TOKEN")
    body = ("import os, json; print(json.dumps({"
            "'proxy': bool(os.environ.get('HTTPS_PROXY')),"
            "'ca': bool(os.environ.get('SSL_CERT_FILE')),"
            "'token': bool(os.environ.get('DATABRICKS_TOKEN'))}))")
    central, _ = _setup(tmp_path, metadata=metadata, script_body=body)
    env = make_env(SkillRegistry(central_dir=central), "allow")
    response = env.call(args=[])
    assert response["success"]
    assert json.loads(response["data"]["stdout"]) == {
        "proxy": expected, "ca": expected, "token": False}
    assert len(env.requests) == 1
    assert ("network-env=proxy-ca" in env.requests[0].approval_reason) == expected
    assert "SENTINEL" not in repr(response)
    assert "SENTINEL" not in repr(env.requests[0].to_dict())
    assert "SENTINEL" not in repr(env.manager.get(1).to_dict())


@pytest.mark.parametrize("value", ['"all"', '"PROXY-CA"', '" proxy-ca"', 'null', 'true', '123'])
def test_invalid_network_env_creates_no_task(tmp_path, make_env, value):
    central, _ = _setup(tmp_path, metadata=f"  network-env: {value}\n")
    env = make_env(SkillRegistry(central_dir=central), "allow")
    response = env.call()
    assert not response["success"]
    assert "metadata.network-env" in response["error"]
    assert env.requests == [] and env.manager.list() == []


def test_network_env_denial_does_not_run(tmp_path, make_env):
    central, skill_dir = _setup(tmp_path, metadata='  network-env: "proxy-ca"\n',
                               script_body="open('marker', 'w').write('ran')")
    env = make_env(SkillRegistry(central_dir=central), "deny")
    assert not env.call()["success"]
    assert not (skill_dir / "marker").exists()


def test_network_env_changed_during_approval(tmp_path, make_env):
    central, skill_dir = _setup(tmp_path, metadata='  network-env: "proxy-ca"\n',
                               script_body="open('marker', 'w').write('ran')")
    def tamper(_task):
        path = skill_dir / "SKILL.md"
        path.write_text(path.read_text(encoding="utf-8").replace('"proxy-ca"', '""'), encoding="utf-8")
    env = make_env(SkillRegistry(central_dir=central), "allow", before_decision=tamper)
    assert not env.call()["success"]
    assert not (skill_dir / "marker").exists()


def test_network_env_read_at_dispatch_only(tmp_path, make_env, monkeypatch):
    from server.task_tools import get_task_impl
    monkeypatch.setenv("HTTPS_PROXY", "SENTINEL-BEFORE")
    central, _ = _setup(tmp_path, metadata='  network-env: "proxy-ca"\n',
                       script_body="print('safe')")
    env = make_env(SkillRegistry(central_dir=central), answer=None)
    response = env.call()
    task_id = response["data"]["task_id"]
    assert "SENTINEL" not in repr(env.manager.get(task_id).to_dict())
    monkeypatch.setenv("HTTPS_PROXY", "SENTINEL-AT-DISPATCH")
    seen = []
    def fake_run(command, cwd, timeout, child_env, cancel_event):
        seen.append(child_env.get("HTTPS_PROXY") == "SENTINEL-AT-DISPATCH")
        assert "DATABRICKS_TOKEN" not in child_env
        return {"started": True, "exit_code": 0, "stdout": "safe", "stderr": ""}
    monkeypatch.setattr("server.task_tools.run_command", fake_run)
    env.approval.resolve(task_id, "allow")
    assert env.manager.wait(task_id, 8).status.value == "done"
    assert seen == [True]
    assert "SENTINEL" not in repr(get_task_impl(env.manager, task_id))


def test_disabled_skill_cannot_run(tmp_path, make_env):
    central, _ = _setup(tmp_path)
    registry = SkillRegistry(central_dir=central, disabled=["demo"])
    env = make_env(registry, "allow")
    result = env.call()
    assert result["success"] is False
    assert "wyłączony" in result["error"]
