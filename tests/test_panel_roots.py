"""Testy endpointów /api/roots w panel/app.py (TestClient, bez uruchamiania serwera)."""
import shutil
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from config_store import ConfigStore
from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from roots import Zones
from roots_registry import RootsRegistry
from state import load_config
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore

ORIGIN = "http://127.0.0.1:8102"


def _dir(base, *parts):
    path = Path(base).joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def env(tmp_path):
    store = TaskStore(str(tmp_path / "panel.db"))
    approval = ApprovalService()
    manager = TaskManager(store, approval)
    auth = PanelAuth(port=8102)
    config_path = tmp_path / "config.json"
    config_store = ConfigStore(load_config(config_path), config_path)
    registry = RootsRegistry(config_store, zones=Zones(), central_dir=_dir(tmp_path, "central"))
    app = create_panel_app(manager, approval, auth, static_dir=tmp_path / "brak-static", roots=registry)
    client = TestClient(app, base_url=ORIGIN)
    session = auth.exchange_token(auth.token)

    class Env:
        pass

    e = Env()
    e.client, e.registry, e.tmp, e.auth, e.manager, e.approval = (
        client, registry, tmp_path, auth, manager, approval)
    e.headers = {"Origin": ORIGIN, "Cookie": f"{COOKIE_NAME}={session}"}
    e.read_headers = {"Cookie": f"{COOKIE_NAME}={session}"}

    def post(path, body=None):
        return client.post(path, json=body if body is not None else {}, headers=e.headers)

    e.post = post
    e.get = lambda path="/api/roots": client.get(path, headers=e.read_headers)
    yield e
    manager.shutdown()
    store.close()


def _add(env, name, mode="rw", **extra):
    path = _dir(env.tmp, name)
    return env.post("/api/roots", {"path": str(path), "mode": mode, **extra})


# --- opcjonalność ---

def test_without_registry_routes_are_404(tmp_path):
    store = TaskStore(str(tmp_path / "x.db"))
    approval = ApprovalService()
    manager = TaskManager(store, approval)
    auth = PanelAuth(port=8102)
    app = create_panel_app(manager, approval, auth, static_dir=tmp_path / "brak")
    client = TestClient(app, base_url=ORIGIN)
    session = auth.exchange_token(auth.token)
    r = client.get("/api/roots", headers={"Cookie": f"{COOKIE_NAME}={session}"})
    assert r.status_code == 404
    manager.shutdown()
    store.close()


# --- bezpieczeństwo ---

def test_roots_require_session(env):
    assert env.client.get("/api/roots").status_code == 401
    r = env.client.post("/api/roots", json={}, headers={"Origin": ORIGIN})
    assert r.status_code == 401


def test_roots_bad_host_and_origin(env):
    assert env.client.get("/api/roots", headers={**env.read_headers, "Host": "evil.com"}).status_code == 403
    assert env.client.get("/api/roots", headers={**env.read_headers, "Origin": "http://evil.com"}).status_code == 403


def test_roots_post_requires_origin(env):
    path = _dir(env.tmp, "app")
    r = env.client.post("/api/roots", json={"path": str(path), "mode": "rw"}, headers=env.read_headers)
    assert r.status_code == 403
    assert env.registry.list() == []


# --- dodawanie i odczyt ---

def test_list_empty(env):
    r = env.get()
    assert r.status_code == 200
    assert r.json()["data"] == {"count": 0, "roots": []}


def test_add_first_root_is_default(env):
    r = _add(env, "App", description="front")
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["alias"] == "app" and data["mode"] == "rw" and data["default"] is True
    listed = env.get().json()["data"]["roots"]
    assert len(listed) == 1 and listed[0]["valid"] is True and listed[0]["description"] == "front"


def test_add_ro_and_rw(env):
    assert _add(env, "a", "rw").status_code == 201
    r = _add(env, "b", "ro")
    assert r.status_code == 201 and r.json()["data"]["default"] is False
    assert [x["mode"] for x in env.get().json()["data"]["roots"]] == ["rw", "ro"]


def test_add_with_alias_and_default(env):
    _add(env, "a")
    r = _add(env, "b", alias="backend", default=True)
    assert r.json()["data"]["alias"] == "backend"
    assert env.registry.default().alias == "backend"


def test_add_validation_errors_400(env):
    assert env.post("/api/roots", {"path": str(_dir(env.tmp, "a")), "mode": "xx"}).status_code == 400
    assert env.post("/api/roots", {"path": str(env.tmp / "nie-ma"), "mode": "rw"}).status_code == 400
    assert env.post("/api/roots", {"mode": "rw"}).status_code == 400
    assert env.post("/api/roots", {"path": 5, "mode": "rw"}).status_code == 400
    assert env.post("/api/roots", {"path": str(env.tmp), "mode": "rw", "default": "tak"}).status_code == 400
    assert env.post("/api/roots", {"path": str(env.tmp), "mode": "rw", "alias": 7}).status_code == 400
    assert env.registry.list() == []


def test_add_drive_root_rejected(env):
    r = env.post("/api/roots", {"path": Path(env.tmp).anchor, "mode": "rw"})
    assert r.status_code == 400 and r.json()["success"] is False


def test_add_bad_json_400(env):
    r = env.client.post("/api/roots", content=b"nie-json",
                        headers={**env.headers, "Content-Type": "application/json"})
    assert r.status_code == 400
    r = env.client.post("/api/roots", content=b"[1]",
                        headers={**env.headers, "Content-Type": "application/json"})
    assert r.status_code == 400


def test_add_duplicate_alias_409(env):
    _add(env, "a", alias="x")
    r = _add(env, "b", alias="x")
    assert r.status_code == 409


def test_add_duplicate_path_409(env):
    _add(env, "a")
    r = env.post("/api/roots", {"path": str(env.tmp / "a"), "mode": "ro", "alias": "inny"})
    assert r.status_code == 409


def test_add_rw_inside_ro_400(env):
    _add(env, "outer", "ro")
    inner = _dir(env.tmp, "outer", "inner")
    r = env.post("/api/roots", {"path": str(inner), "mode": "rw"})
    assert r.status_code == 400


def test_add_ro_inside_rw_allowed(env):
    _add(env, "outer", "rw")
    inner = _dir(env.tmp, "outer", "inner")
    assert env.post("/api/roots", {"path": str(inner), "mode": "ro"}).status_code == 201


# --- update ---

def test_update_description_mode_alias(env):
    _add(env, "a", alias="one")
    r = env.post("/api/roots/one/update", {"description": "opis", "mode": "ro", "new_alias": "two"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["alias"] == "two" and data["mode"] == "ro" and data["description"] == "opis"
    assert env.registry.get("one") is None


def test_update_unknown_404(env):
    assert env.post("/api/roots/brak/update", {"description": "x"}).status_code == 404


def test_update_bad_mode_400(env):
    _add(env, "a", alias="one")
    assert env.post("/api/roots/one/update", {"mode": "zz"}).status_code == 400
    assert env.post("/api/roots/one/update", {"mode": 3}).status_code == 400


def test_update_alias_conflict_409(env):
    _add(env, "a", alias="one")
    _add(env, "b", alias="two")
    assert env.post("/api/roots/one/update", {"new_alias": "two"}).status_code == 409


def test_update_rw_inside_ro_400(env):
    _add(env, "outer", "ro", alias="outer")
    inner = _dir(env.tmp, "outer", "inner")
    env.post("/api/roots", {"path": str(inner), "mode": "ro", "alias": "inner"})
    assert env.post("/api/roots/inner/update", {"mode": "rw"}).status_code == 400
    assert env.registry.get("inner").mode == "ro"


# --- delete i default ---

def test_set_default(env):
    _add(env, "a", alias="one")
    _add(env, "b", alias="two")
    r = env.post("/api/roots/two/default")
    assert r.status_code == 200 and r.json()["data"]["default"] is True
    assert env.registry.default().alias == "two"


def test_set_default_unknown_404(env):
    assert env.post("/api/roots/brak/default").status_code == 404


def test_delete_root(env):
    _add(env, "a", alias="one")
    r = env.post("/api/roots/one/delete")
    assert r.status_code == 200 and r.json()["data"]["alias"] == "one"
    assert env.get().json()["data"]["count"] == 0
    assert (env.tmp / "a").is_dir()  # pliki na dysku zostają


def test_delete_unknown_404(env):
    assert env.post("/api/roots/brak/delete").status_code == 404


def test_roots_post_other_methods_not_allowed(env):
    _add(env, "a", alias="one")
    assert env.client.get("/api/roots/one/delete", headers=env.read_headers).status_code == 405


# --- dostępność ---

def test_get_marks_missing_folder_invalid(env):
    _add(env, "gone", alias="gone")
    shutil.rmtree(env.tmp / "gone")
    entry = env.get().json()["data"]["roots"][0]
    assert entry["valid"] is False and entry["reason"]


def test_default_change_notifies_listener(env):
    events = []
    env.registry.set_default_listener(lambda old, new: events.append(new.alias if new else None))
    _add(env, "a", alias="one")
    _add(env, "b", alias="two")
    env.post("/api/roots/two/default")
    assert events == ["one", "two"]
