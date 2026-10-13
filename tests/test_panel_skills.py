"""P2: API administracji skilli, bez serwera i procesów skryptów."""
import pytest
from starlette.testclient import TestClient

from config_store import ConfigStore
from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from server.skills_admin import SkillsAdmin
from skills_engine import SkillRegistry
from state import load_config
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore

ORIGIN = "http://127.0.0.1:8102"


def skill(root, name):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test\n---\nbody\n", encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    central, dev, project = (tmp_path / n for n in ("central", "dev", "project"))
    skill(central, "stable")
    skill(dev, "draft")
    skill(dev, "stable")
    skill(project / ".agents" / "skills", "local")
    config_path = tmp_path / "config.json"
    config = load_config(config_path)
    config["skills"].update(central_dir=str(central), dev_dirs=[str(dev)],
                            trust={"stable": {"hash": "sentinel"}})
    config_store = ConfigStore(config, config_path)
    config_store.save()
    registry = SkillRegistry(central_dir=central, project_root=project, dev_dirs=[dev])
    admin = SkillsAdmin(registry, config_store)
    store = TaskStore(str(tmp_path / "tasks.db"))
    approval = ApprovalService()
    manager = TaskManager(store, approval)
    auth = PanelAuth(port=8102)
    app = create_panel_app(manager, approval, auth, skills=admin)
    client = TestClient(app, base_url=ORIGIN)
    session = auth.exchange_token(auth.token)

    class Env:
        pass

    e = Env()
    e.client, e.admin, e.registry, e.config_store = client, admin, registry, config_store
    e.config_path, e.dev = config_path, dev
    e.manager, e.approval, e.auth = manager, approval, auth
    e.read_headers = {"Cookie": f"{COOKIE_NAME}={session}"}
    e.headers = {**e.read_headers, "Origin": ORIGIN}
    e.get = lambda: client.get("/api/skills", headers=e.read_headers)
    e.post = lambda name, body: client.post(
        f"/api/skills/{name}/enabled", json=body, headers=e.headers)
    yield e
    manager.shutdown()
    store.close()


def test_list_includes_disabled_and_shadowed(env):
    response = env.get()
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["count"] == 4
    assert data["dev_dirs"] == [str(env.dev)]
    entries = data["skills"]
    draft = next(e for e in entries if e["name"] == "draft")
    assert draft["enabled"] is False and draft["source"] == "dev"
    stable = [e for e in entries if e["name"] == "stable"]
    assert {e["source"] for e in stable} == {"central", "dev"}
    assert next(e for e in stable if e["source"] == "dev")["shadowed"] is True
    for entry in entries:
        assert {"path", "description", "warnings", "enabled", "shadowed"} <= set(entry)


@pytest.mark.parametrize("name,key", [("stable", "disabled"), ("local", "disabled")])
def test_disable_enable_regular_skill_persists(env, name, key):
    assert env.post(name, {"enabled": False}).status_code == 200
    assert env.registry.get(name) is None
    assert name in load_config(env.config_path)["skills"][key]
    assert env.post(name, {"enabled": True}).status_code == 200
    assert env.registry.get(name) is not None
    assert name not in load_config(env.config_path)["skills"][key]
    assert env.config_store.snapshot()["skills"]["trust"] == {
        "stable": {"hash": "sentinel"}}


def test_dev_enable_disable_uses_allowlist(env):
    assert env.post("draft", {"enabled": True}).status_code == 200
    assert env.registry.get("draft").source == "dev"
    config = load_config(env.config_path)["skills"]
    assert config["dev_enabled"] == ["draft"] and config["disabled"] == []
    assert env.post("draft", {"enabled": False}).status_code == 200
    assert env.registry.get("draft") is None
    assert load_config(env.config_path)["skills"]["dev_enabled"] == []


def test_disabled_central_does_not_expose_shadowed_dev(env):
    env.registry.set_dev_enabled(["stable"])
    assert env.post("stable", {"enabled": False}).status_code == 200
    assert env.registry.get("stable") is None
    assert load_config(env.config_path)["skills"]["disabled"] == ["stable"]


@pytest.mark.parametrize("body", [{}, [], {"enabled": None}, {"enabled": 1},
                                    {"enabled": "true"}, {"enabled": True, "extra": 1}])
def test_invalid_body_rejected(env, body):
    before = env.config_path.read_bytes()
    assert env.post("stable", body).status_code == 400
    assert env.config_path.read_bytes() == before


def test_malformed_json_rejected(env):
    response = env.client.post("/api/skills/stable/enabled", content="not-json",
                               headers=env.headers)
    assert response.status_code == 400


def test_unknown_skill_does_not_write(env):
    before = env.config_path.read_bytes()
    assert env.post("missing", {"enabled": True}).status_code == 404
    assert env.config_path.read_bytes() == before


def test_session_required(env):
    assert env.client.get("/api/skills").status_code == 401
    assert env.client.post("/api/skills/stable/enabled", json={"enabled": False},
                           headers={"Origin": ORIGIN}).status_code == 401


def test_host_origin_required(env):
    assert env.client.get("/api/skills", headers={
        **env.read_headers, "Host": "evil.com"}).status_code == 403
    assert env.client.get("/api/skills", headers={
        **env.read_headers, "Origin": "http://evil.com"}).status_code == 403
    for headers in (env.read_headers, {**env.headers, "Origin": "http://evil.com"},
                    {**env.headers, "Host": "evil.com"}):
        assert env.client.post("/api/skills/stable/enabled", json={"enabled": False},
                               headers=headers).status_code == 403


def test_optional_routes_absent(env):
    app = create_panel_app(env.manager, env.approval, env.auth)
    client = TestClient(app, base_url=ORIGIN)
    assert client.get("/api/skills", headers=env.read_headers).status_code == 404
    assert client.post("/api/skills/stable/enabled", json={"enabled": True},
                       headers=env.headers).status_code == 404


def test_save_failure_rolls_back_config_and_access(env, monkeypatch):
    env.get()
    before = env.config_store.snapshot()
    disk = env.config_path.read_bytes()

    def fail(*args):
        raise OSError("PRIVATE-FAILURE-SENTINEL")

    monkeypatch.setattr("config_store.save_config", fail)
    response = env.post("stable", {"enabled": False})
    assert response.status_code == 500
    assert "PRIVATE-FAILURE-SENTINEL" not in response.text
    assert env.config_store.snapshot() == before
    assert env.config_path.read_bytes() == disk
    assert env.registry.get("stable") is not None
    assert env.registry.get("draft") is None


def test_list_failure_has_constant_error(env, monkeypatch):
    def fail():
        raise RuntimeError("PRIVATE-LIST-SENTINEL")
    monkeypatch.setattr(env.admin, "list", fail)
    response = env.get()
    assert response.status_code == 500
    assert "PRIVATE-LIST-SENTINEL" not in response.text


def central_entry(env):
    return next(e for e in env.get().json()["data"]["skills"]
                if e["name"] == "stable" and not e["shadowed"])


def trust_post(env, entry, action="trust", body=None, headers=None):
    if body is None:
        body = {"path": entry["path"]}
        if action == "trust":
            body["hash"] = entry["current_hash"]
    return env.client.post(f'/api/skills/{entry["name"]}/{action}',
                           json=body, headers=headers or env.headers)


def test_trust_roundtrip_and_flags_independent(env):
    env.post("stable", {"enabled": False})
    entry = central_entry(env)
    assert entry["trust_status"] == "changed"
    assert trust_post(env, entry).status_code == 200
    saved = load_config(env.config_path)["skills"]
    assert saved["trust"]["stable"]["hash"] == entry["current_hash"]
    assert saved["disabled"] == ["stable"]
    entry = central_entry(env)
    assert entry["trust_status"] == "trusted" and entry["trusted_at"]
    assert entry["can_skip_approval"] is False
    assert env.registry.get("stable") is None
    assert trust_post(env, entry, "revoke-trust").status_code == 200
    assert central_entry(env)["trust_status"] == "untrusted"
    assert env.registry.get("stable") is None


def test_flagged_trusted_version_can_skip_approval(env):
    from pathlib import Path
    entry = central_entry(env)
    path = Path(entry["path"]) / "SKILL.md"
    path.write_text('---\nname: stable\ndescription: d\nmetadata:\n'
                    '  vibecoder-confirm: "false"\n---\nbody\n', encoding="utf-8")
    entry = central_entry(env)
    assert entry["declares_no_confirm"] is True
    assert entry["can_skip_approval"] is False
    assert trust_post(env, entry).status_code == 200
    assert central_entry(env)["can_skip_approval"] is True


def test_modified_hash_conflict_no_write(env):
    from pathlib import Path
    entry = central_entry(env)
    before = env.config_path.read_bytes()
    (Path(entry["path"]) / "scripts").mkdir()
    (Path(entry["path"]) / "scripts" / "new.py").write_text("print(1)", encoding="utf-8")
    assert trust_post(env, entry).status_code == 409
    assert env.config_path.read_bytes() == before


@pytest.mark.parametrize("action", ["trust", "revoke-trust"])
def test_path_or_source_change_conflict(env, action):
    entry = central_entry(env)
    before = env.config_path.read_bytes()
    wrong = dict(entry, path=entry["path"] + "-other")
    assert trust_post(env, wrong, action).status_code == 409
    project = env.registry._project_root / ".agents" / "skills"
    skill(project, "stable")
    assert trust_post(env, entry, action).status_code == 409
    assert env.config_path.read_bytes() == before


@pytest.mark.parametrize("name", ["draft", "local"])
@pytest.mark.parametrize("action", ["trust", "revoke-trust"])
def test_dev_project_cannot_be_trusted(env, name, action):
    entry = next(e for e in env.get().json()["data"]["skills"] if e["name"] == name)
    assert entry["trust_status"] == "not_applicable"
    entry["current_hash"] = "a" * 64
    assert trust_post(env, entry, action).status_code == 409


@pytest.mark.parametrize("body", [{}, {"path": "x"}, {"path": "x", "hash": "bad"},
                                    {"path": "x", "hash": 1},
                                    {"path": "x", "hash": "a" * 64, "extra": True}])
def test_bad_trust_body(env, body):
    assert trust_post(env, central_entry(env), body=body).status_code == 400


@pytest.mark.parametrize("action", ["trust", "revoke-trust"])
def test_trust_security(env, action):
    entry = central_entry(env)
    body = {"path": entry["path"]}
    if action == "trust":
        body["hash"] = entry["current_hash"]
    url = f'/api/skills/stable/{action}'
    assert env.client.post(url, json=body, headers={"Origin": ORIGIN}).status_code == 401
    for headers in (env.read_headers, {**env.headers, "Host": "evil.com"},
                    {**env.headers, "Origin": "http://evil.com"}):
        assert env.client.post(url, json=body, headers=headers).status_code == 403


@pytest.mark.parametrize("action", ["trust", "revoke-trust"])
def test_trust_save_failure_rolls_back(env, monkeypatch, action):
    entry = central_entry(env)
    before = env.config_store.snapshot()
    disk = env.config_path.read_bytes()

    def fail(*args):
        raise OSError("PRIVATE-TRUST-SENTINEL")
    monkeypatch.setattr("config_store.save_config", fail)
    response = trust_post(env, entry, action)
    assert response.status_code == 500
    assert "PRIVATE-TRUST-SENTINEL" not in response.text
    assert env.config_store.snapshot() == before
    assert env.config_path.read_bytes() == disk


def test_hash_read_failure_is_reported_without_exception(env, monkeypatch):
    def fail(*args):
        raise OSError("PRIVATE-HASH-SENTINEL")
    monkeypatch.setattr("server.skills_admin.compute_skill_hash", fail)
    response = env.get()
    assert response.status_code == 200
    entry = next(e for e in response.json()["data"]["skills"]
                 if e["name"] == "stable" and not e["shadowed"])
    assert entry["trust_status"] == "hash_error"
    assert entry["current_hash"] is None
    assert "PRIVATE-HASH-SENTINEL" not in response.text
