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


def test_js_has_no_external_urls():
    js = read("app.js")
    assert not re.search(r"https?://(?!127\.0\.0\.1:8102)", js.replace("http://127.0.0.1:8102/#token", ""))


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
