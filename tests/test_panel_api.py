"""Testy panel/app.py: API panelu zatwierdzeń (TestClient, bez uruchamiania serwera)."""
import threading

import pytest
from starlette.testclient import TestClient

from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore

ORIGIN = "http://127.0.0.1:8102"


@pytest.fixture
def env(tmp_path):
    store = TaskStore(str(tmp_path / "panel.db"))
    approval = ApprovalService()
    manager = TaskManager(store, approval, max_parallel=2, max_queued=50, max_wait_s=8)
    auth = PanelAuth(port=8102)
    app = create_panel_app(manager, approval, auth, static_dir=tmp_path / "brak-static")
    client = TestClient(app, base_url=ORIGIN)
    session = auth.exchange_token(auth.token)

    class Env:
        pass

    e = Env()
    e.store, e.manager, e.approval, e.auth, e.client, e.session = (
        store, manager, approval, auth, client, session)
    e.headers = {"Origin": ORIGIN, "Cookie": f"{COOKIE_NAME}={session}"}
    e.read_headers = {"Cookie": f"{COOKIE_NAME}={session}"}

    def submit(needs_approval=True, offer_trust=False, executor=None):
        def default_executor(cancel_event):
            return {"exit_code": 0, "stdout": "ok", "stderr": "", "duration_s": 0.01}

        fields = dict(skill="demo", source="central", script="scripts/run.py", args=["a"],
                      timeout=30, offer_trust=offer_trust, script_preview="print('x')")
        return manager.submit(fields, executor or default_executor, needs_approval=needs_approval)

    e.submit = submit
    yield e
    manager.shutdown()
    store.close()


# --- Host / Origin / sesja ---

def test_bad_host_rejected(env):
    r = env.client.get("/api/tasks", headers={**env.read_headers, "Host": "evil.com"})
    assert r.status_code == 403


def test_bad_origin_rejected_on_get(env):
    r = env.client.get("/api/tasks", headers={**env.read_headers, "Origin": "http://evil.com"})
    assert r.status_code == 403


def test_post_without_origin_rejected(env):
    t = env.submit()
    r = env.client.post(f"/api/tasks/{t.id}/decision", json={"decision": "allow"},
                        headers=env.read_headers)
    assert r.status_code == 403
    assert env.manager.get(t.id).status.value == "awaiting_approval"


def test_post_bad_origin_rejected(env):
    t = env.submit()
    r = env.client.post(f"/api/tasks/{t.id}/decision", json={"decision": "allow"},
                        headers={**env.read_headers, "Origin": "http://evil.com"})
    assert r.status_code == 403
    assert env.manager.get(t.id).status.value == "awaiting_approval"


def test_api_without_cookie_unauthorized(env):
    assert env.client.get("/api/tasks").status_code == 401
    t = env.submit()
    r = env.client.post(f"/api/tasks/{t.id}/decision", json={"decision": "allow"},
                        headers={"Origin": ORIGIN})
    assert r.status_code == 401
    assert env.manager.get(t.id).status.value == "awaiting_approval"


def test_api_with_invalid_session_unauthorized(env):
    r = env.client.get("/api/tasks", headers={"Cookie": f"{COOKIE_NAME}=zla-sesja"})
    assert r.status_code == 401


def test_session_exchange_sets_secure_cookie(env):
    r = env.client.post("/api/session", json={"token": env.auth.token},
                        headers={"Origin": ORIGIN})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "httponly" in cookie.lower()
    assert "samesite=strict" in cookie.lower()
    session_id = cookie.split(";")[0].split("=", 1)[1]
    assert env.auth.is_valid_session(session_id)


def test_session_exchange_wrong_token(env):
    r = env.client.post("/api/session", json={"token": "zly"}, headers={"Origin": ORIGIN})
    assert r.status_code == 401
    assert "set-cookie" not in r.headers


def test_session_exchange_bad_body(env):
    r = env.client.post("/api/session", content="nie-json", headers={"Origin": ORIGIN})
    assert r.status_code == 401


def test_session_exchange_requires_origin(env):
    r = env.client.post("/api/session", json={"token": env.auth.token})
    assert r.status_code == 403


# --- lista i szczegóły ---

def test_list_tasks_summary_without_output(env):
    t = env.submit()
    r = env.client.get("/api/tasks", headers=env.read_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["count"] == 1
    item = data["tasks"][0]
    assert item["id"] == t.id and item["status"] == "awaiting_approval"
    assert "stdout" not in item and "stderr" not in item


def test_list_tasks_status_filter(env):
    env.submit()
    t2 = env.submit()
    env.manager.cancel(t2.id)
    r = env.client.get("/api/tasks?status=cancelled", headers=env.read_headers)
    tasks = r.json()["data"]["tasks"]
    assert [x["id"] for x in tasks] == [t2.id]


def test_list_tasks_bad_status_and_limit(env):
    assert env.client.get("/api/tasks?status=xyz", headers=env.read_headers).status_code == 400
    assert env.client.get("/api/tasks?limit=abc", headers=env.read_headers).status_code == 400


def test_list_tasks_limit_clamped(env):
    for _ in range(3):
        env.submit()
    r = env.client.get("/api/tasks?limit=2", headers=env.read_headers)
    assert r.json()["data"]["count"] == 2
    r = env.client.get("/api/tasks?limit=0", headers=env.read_headers)
    assert r.json()["data"]["count"] == 1


def test_get_task_full_details(env):
    t = env.submit()
    r = env.client.get(f"/api/tasks/{t.id}", headers=env.read_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["script_preview"] == "print('x')"
    assert data["args"] == ["a"]
    assert "stdout" in data and "stderr" in data


def test_get_task_not_found(env):
    assert env.client.get("/api/tasks/999", headers=env.read_headers).status_code == 404
    assert env.client.get("/api/tasks/abc", headers=env.read_headers).status_code == 404
    assert env.client.get("/api/tasks/0", headers=env.read_headers).status_code == 404


# --- decyzje ---

def test_decision_allow_runs_task(env):
    t = env.submit()
    r = env.client.post(f"/api/tasks/{t.id}/decision", json={"decision": "allow"},
                        headers=env.headers)
    assert r.status_code == 200
    assert r.json()["data"]["decision"] == "allow"
    done = env.manager.wait(t.id, 5)
    assert done.status.value == "done"
    assert done.stdout == "ok"


def test_decision_deny_rejects(env):
    t = env.submit()
    r = env.client.post(f"/api/tasks/{t.id}/decision", json={"decision": "deny"},
                        headers=env.headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "rejected" and data["decision"] == "deny"


def test_decision_trust_when_offered(env):
    t = env.submit(offer_trust=True)
    r = env.client.post(f"/api/tasks/{t.id}/decision", json={"decision": "trust"},
                        headers=env.headers)
    assert r.status_code == 200
    assert r.json()["data"]["decision"] == "trust"


def test_decision_trust_without_offer_degrades_to_allow(env):
    t = env.submit(offer_trust=False)
    r = env.client.post(f"/api/tasks/{t.id}/decision", json={"decision": "trust"},
                        headers=env.headers)
    assert r.status_code == 200
    assert r.json()["data"]["decision"] == "allow"


def test_decision_invalid_values(env):
    t = env.submit()
    for body in ({"decision": "timeout"}, {"decision": "xyz"}, {"decision": 1}, {}, []):
        r = env.client.post(f"/api/tasks/{t.id}/decision", json=body, headers=env.headers)
        assert r.status_code == 400, body
    r = env.client.post(f"/api/tasks/{t.id}/decision", content="nie-json", headers=env.headers)
    assert r.status_code == 400
    assert env.manager.get(t.id).status.value == "awaiting_approval"


def test_decision_not_found(env):
    r = env.client.post("/api/tasks/999/decision", json={"decision": "allow"},
                        headers=env.headers)
    assert r.status_code == 404
    r = env.client.post("/api/tasks/abc/decision", json={"decision": "allow"},
                        headers=env.headers)
    assert r.status_code == 404


def test_decision_on_non_waiting_task_conflict(env):
    t = env.submit()
    env.client.post(f"/api/tasks/{t.id}/decision", json={"decision": "deny"}, headers=env.headers)
    r = env.client.post(f"/api/tasks/{t.id}/decision", json={"decision": "allow"},
                        headers=env.headers)
    assert r.status_code == 409


# --- anulowanie ---

def test_cancel_awaiting_approval(env):
    t = env.submit()
    r = env.client.post(f"/api/tasks/{t.id}/cancel", headers=env.headers)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "cancelled"


def test_cancel_running_task(env):
    started = threading.Event()

    def blocking(cancel_event):
        started.set()
        cancel_event.wait(10)
        return {"cancelled": True, "exit_code": None, "stdout": "czesc"}

    t = env.submit(needs_approval=False, executor=blocking)
    assert started.wait(5)
    r = env.client.post(f"/api/tasks/{t.id}/cancel", headers=env.headers)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "cancelled"


def test_cancel_finished_task_unchanged(env):
    t = env.submit(needs_approval=False)
    env.manager.wait(t.id, 5)
    r = env.client.post(f"/api/tasks/{t.id}/cancel", headers=env.headers)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "done"


def test_cancel_not_found(env):
    r = env.client.post("/api/tasks/999/cancel", headers=env.headers)
    assert r.status_code == 404


def test_cancel_requires_origin_and_session(env):
    t = env.submit()
    assert env.client.post(f"/api/tasks/{t.id}/cancel",
                           headers=env.read_headers).status_code == 403
    assert env.client.post(f"/api/tasks/{t.id}/cancel",
                           headers={"Origin": ORIGIN}).status_code == 401
    assert env.manager.get(t.id).status.value == "awaiting_approval"


# --- nagłówki i statyka ---

def test_security_headers_present(env):
    r = env.client.get("/api/tasks", headers=env.read_headers)
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_security_headers_also_on_errors(env):
    r = env.client.get("/api/tasks")
    assert r.status_code == 401
    assert "content-security-policy" in r.headers


def test_index_missing_returns_404(env):
    assert env.client.get("/").status_code == 404


def test_index_served_and_host_checked(tmp_path, env):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>panel</html>", encoding="utf-8")
    app = create_panel_app(env.manager, env.approval, env.auth, static_dir=static)
    client = TestClient(app, base_url=ORIGIN)
    r = client.get("/")
    assert r.status_code == 200 and "panel" in r.text
    assert client.get("/", headers={"Host": "evil.com"}).status_code == 403
