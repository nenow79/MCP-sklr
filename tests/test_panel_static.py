"""Testy statycznego frontendu panelu: zgodność z CSP i brak niebezpiecznych wzorców DOM."""
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from panel.app import DEFAULT_STATIC_DIR, create_panel_app
from panel.auth import PanelAuth
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore

STATIC = Path(DEFAULT_STATIC_DIR)
ORIGIN = "http://127.0.0.1:8102"

FORBIDDEN_JS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                "eval(", "new Function", "setTimeout(\"", "setTimeout('")


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_static_files_exist():
    for name in ("index.html", "app.js", "style.css"):
        assert (STATIC / name).is_file(), name


def test_index_references_local_assets():
    html = read("index.html")
    assert 'href="/static/style.css"' in html
    assert 'src="/static/app.js"' in html


def test_index_has_roots_tab_and_form():
    html = read("index.html")
    for element_id in ("tab-tasks", "tab-roots", "tasks-view", "roots-view", "roots-list",
                       "root-form", "root-path", "root-mode", "root-alias", "root-description",
                       "root-default"):
        assert f'id="{element_id}"' in html, element_id
    assert "Foldery" in html


def test_index_has_skills_tab_and_view():
    html = read("index.html")
    js = read("app.js")
    for element_id in ("tab-skills", "skills-view", "skills-list",
                       "skills-refresh", "skills-dev-dirs"):
        assert f'id="{element_id}"' in html, element_id
    assert "/api/skills" in js
    for tab in ("tasks", "roots", "skills"):
        assert f'id="tab-{tab}"' in html
        assert f'id="{tab}-view"' in html
    assert "To nie jest sandbox" in html


def test_knowledge_tab_and_configuration_controls():
    html, js = read("index.html"), read("app.js")
    for element_id in ("tab-knowledge", "knowledge-view", "knowledge-list", "knowledge-form",
                       "knowledge-id", "knowledge-root", "knowledge-scope", "knowledge-profile",
                       "knowledge-subpath", "knowledge-enabled", "knowledge-add"):
        assert f'id="{element_id}"' in html
    assert "/api/knowledge" in js and "body.revision = knowledgeData.revision" in js
    assert "Konwersja Markdown do OKF jest opcjonalna" in html


def test_knowledge_index_diagnostics_are_rendered_as_text():
    js = read("app.js")
    for field in ("bundle.reasons", "bundle.excluded_documents",
                  "bundle.refresh_task_id", "bundle.index_error", "bundle.last_refresh"):
        assert field in js
    assert 'visibleText(bundle.reasons.join(", "))' in js
    assert 'visibleText(bundle.index_error)' in js
    assert '["scan_checks", "publish_checks"]' in js
    for metric in ("bundle_check_calls", "path_resolve_calls", "authorize_calls"):
        assert "metrics." + metric in js


def test_js_uses_roots_api_and_ids_exist_in_html():
    js = read("app.js")
    html = read("index.html")
    assert "/api/roots" in js
    for element_id in set(re.findall(r'\$\("([a-z-]+)"\)', js)):
        assert f'id="{element_id}"' in html, element_id


def test_index_has_no_external_resources():
    html = read("index.html")
    assert not re.search(r'(?:src|href)\s*=\s*"(?:https?:)?//', html)


def test_index_has_no_inline_script_or_style():
    html = read("index.html")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html, re.IGNORECASE)
    assert not re.search(r"<style\b", html, re.IGNORECASE)
    assert not re.search(r"\sstyle\s*=", html, re.IGNORECASE)
    assert not re.search(r"\son[a-z]+\s*=", html, re.IGNORECASE)


def test_js_has_no_dangerous_dom_apis():
    js = read("app.js")
    for pattern in FORBIDDEN_JS:
        assert pattern not in js, pattern


def test_background_polling_keeps_notifications_before_render_guard():
    js = read("app.js")
    refresh = js.split("  function refresh() {", 1)[1].split("  function schedule() {", 1)[0]
    tick = js.split("  function tick() {", 1)[1].split("  // --- szczegóły ---", 1)[0]
    assert "var BACKGROUND_POLL_MS = 5000;" in js
    assert "document.hidden ? BACKGROUND_POLL_MS : POLL_MS" in js
    assert "document.hidden" not in tick
    assert "state.tickPromise" in tick and "state.refreshPromise" in refresh
    assert refresh.index("handleNewPending(pending)") < refresh.index("if (document.hidden)")
    assert refresh.index("updateTitleAndBadge(pending.length)") < refresh.index("if (document.hidden)")
    assert refresh.index("if (document.hidden)") < refresh.index("renderList(")


def test_notification_permission_audio_and_generic_task_title():
    js = read("app.js")
    assert "ctx.resume().then(play)" in js
    assert "powiadomienia zablokowane" in js
    assert "powiadomienia niedostępne" in js
    assert 'task.title || task.kind || "Zadanie"' in js
    assert 'tag: "vibecoder-task-" + task.id' in js


def test_js_has_no_external_urls():
    js = read("app.js")
    assert not re.search(r"https?://(?!127\.0\.0\.1:8102)", js.replace("http://127.0.0.1:8102/#token", ""))


def test_session_tab_static():
    html = read("index.html")
    js = read("app.js")
    for element_id in ("tab-session", "session-view", "session-state", "session-select", "session-roots",
                       "session-start", "session-pause", "session-refresh"):
        assert f'id="{element_id}"' in html, element_id
    assert "wszystkie rozmowy" in html and "Przygotowanie wznowienia nie otwiera bramki" in html
    assert "Przełączenie przypiętej sesji jest niedostępne" in html
    assert "tylko w RAM" in html and "/api/work-session/checkpoint" in js
    for element_id in ("session-checkpoint", "session-checkpoint-section"):
        assert f'id="{element_id}"' in html
    assert "/api/work-session/start" in js and "/api/work-session/pause" in js
    assert "work-session/switch" not in js
    for suffix in ("resume-preview", "prepare-resume", "resume-plan", "saved", "recover"):
        assert "/api/work-session/" + suffix in js
    for element_id in ("session-resume-section", "session-supplement", "session-prepare",
                       "session-plan-section", "session-plan", "session-saved-section", "session-saved"):
        assert f'id="{element_id}"' in html
    assert 'maxlength="3000" autocomplete="off"' in html
    session_code = js.split("  // --- sesja pracy:", 1)[1].split("  function init()", 1)[0]
    assert not re.search(r"setTimeout|setInterval|localStorage|innerHTML", session_code)


def test_css_has_no_external_imports():
    css = read("style.css")
    assert "@import" not in css
    assert not re.search(r"url\(\s*['\"]?https?:", css)


@pytest.fixture
def client(tmp_path):
    store = TaskStore(str(tmp_path / "static.db"))
    approval = ApprovalService()
    manager = TaskManager(store, approval)
    auth = PanelAuth(port=8102)
    app = create_panel_app(manager, approval, auth)
    yield TestClient(app, base_url=ORIGIN)
    manager.shutdown()
    store.close()


def test_index_served_by_app(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Vibecoder" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_assets_served_by_app(client):
    js = client.get("/static/app.js")
    css = client.get("/static/style.css")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert css.status_code == 200 and "css" in css.headers["content-type"]


def test_assets_require_allowed_host(client):
    assert client.get("/static/app.js", headers={"Host": "evil.com"}).status_code == 403
