"""Directory picker tests without opening real windows or launching a server."""
import json
import subprocess
import threading
from types import SimpleNamespace
import sys

import pytest
from starlette.testclient import TestClient

from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from panel.directory_dialog import choose_directory
from panel.directory_picker import DirectoryPicker, DirectoryPickerBusy, DirectoryPickerUnavailable

ORIGIN = "http://127.0.0.1:8102"


def test_picker_selected_path_and_command(monkeypatch, tmp_path):
    selected = tmp_path / "zażółć folder"
    selected.mkdir()
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"path": str(selected)}))

    monkeypatch.setattr(subprocess, "run", run)
    assert DirectoryPicker().pick() == str(selected)
    args, kwargs = calls[0]
    assert args[0] == sys.executable and args[1] == "-I"
    assert args[2].endswith("directory_dialog.py")
    assert kwargs == dict(capture_output=True, encoding="utf-8", timeout=300, check=False)


def test_picker_cancel(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='{"path": null}'))
    assert DirectoryPicker().pick() is None


@pytest.mark.parametrize("output", ['bad json', '[]', '{}', '{"path": 42}',
                                    '{"path": "relative"}', '{"path": ""}',
                                    '{"path": null, "extra": true}'])
def test_picker_invalid_result_releases_lock(monkeypatch, output):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=output))
    picker = DirectoryPicker()
    for _ in range(2):
        with pytest.raises(DirectoryPickerUnavailable):
            picker.pick()


@pytest.mark.parametrize("kind", ["missing", "file", "exit", "timeout", "oserror"])
def test_picker_failures(monkeypatch, tmp_path, kind):
    target = tmp_path / "target"
    if kind == "file":
        target.write_text("x")

    def run(*args, **kwargs):
        if kind == "timeout":
            raise subprocess.TimeoutExpired(args[0], 300)
        if kind == "oserror":
            raise OSError("private details")
        return SimpleNamespace(returncode=1 if kind == "exit" else 0,
                               stdout=json.dumps({"path": str(target)}))

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(DirectoryPickerUnavailable):
        DirectoryPicker().pick()


def test_picker_busy_does_not_launch_another_process(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(returncode=0, stdout='{"path": null}')

    monkeypatch.setattr(subprocess, "run", run)
    picker = DirectoryPicker()
    thread = threading.Thread(target=picker.pick)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(DirectoryPickerBusy):
            picker.pick()
        assert len(calls) == 1
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert picker.pick() is None


@pytest.mark.parametrize("selection", ["", "C:/projekty/zażółć"])
def test_dialog_options_and_cleanup(monkeypatch, selection):
    calls = []
    root = SimpleNamespace(withdraw=lambda: calls.append("withdraw"),
                           attributes=lambda *a: calls.append(a),
                           destroy=lambda: calls.append("destroy"))

    def askdirectory(**kwargs):
        assert kwargs == dict(parent=root, title="sklr — Wybierz folder", mustexist=True)
        return selection

    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(
        Tk=lambda: root, filedialog=SimpleNamespace(askdirectory=askdirectory)))
    assert choose_directory() == (selection or None)
    assert calls == ["withdraw", ("-topmost", True), "destroy"]


def test_dialog_cleanup_on_error(monkeypatch):
    destroyed = []

    def fail(**kwargs):
        raise RuntimeError("dialog failed")

    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(
        Tk=lambda: SimpleNamespace(withdraw=lambda: None, attributes=lambda *a: None,
                                   destroy=lambda: destroyed.append(True)),
        filedialog=SimpleNamespace(askdirectory=fail)))
    with pytest.raises(RuntimeError):
        choose_directory()
    assert destroyed == [True]


@pytest.fixture
def api(tmp_path):
    calls = []
    picker = SimpleNamespace(pick=lambda: calls.append(True) or str(tmp_path))
    auth = PanelAuth()
    # This endpoint must not touch the roots registry or task manager.
    app = create_panel_app(object(), object(), auth, roots=object(), directory_picker=picker)
    headers = {"Origin": ORIGIN, "Cookie": f"{COOKIE_NAME}={auth.exchange_token(auth.token)}"}
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, headers, picker, calls


def test_api_selection_and_cancellation(api, tmp_path):
    client, headers, picker, calls = api
    response = client.post("/api/roots/pick-directory", json={}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"path": str(tmp_path)}}
    picker.pick = lambda: None
    assert client.post("/api/roots/pick-directory", json={}, headers=headers).json()["data"] == {"path": None}
    assert calls == [True]


@pytest.mark.parametrize("kind,code", [(DirectoryPickerBusy, 409), (DirectoryPickerUnavailable, 503)])
def test_api_dialog_errors(api, kind, code):
    client, headers, picker, calls = api

    def fail():
        raise kind("private details")

    picker.pick = fail
    response = client.post("/api/roots/pick-directory", json={}, headers=headers)
    assert response.status_code == code
    assert "private details" not in response.text


def test_api_security_and_validation(api):
    client, headers, picker, calls = api
    url = "/api/roots/pick-directory"
    assert client.post(url, json={}, headers={"Origin": ORIGIN}).status_code == 401
    assert client.post(url, json={}, headers={"Cookie": headers["Cookie"]}).status_code == 403
    for override in ({"Host": "evil.com"}, {"Origin": "http://evil.com"}):
        assert client.post(url, json={}, headers={**headers, **override}).status_code == 403
    assert client.get(url, headers=headers).status_code == 405
    for body in ([], None, {"path": "C:/private"}, {"root": "unknown"}, {"root": ""},
                 {"root": 3}, {"root": None}, {"root": "x", "extra": True}):
        assert client.post(url, json=body, headers=headers).status_code == 400
    assert client.post(url, content="bad json", headers=headers).status_code == 400
    assert calls == []


def test_api_optional_without_roots():
    auth = PanelAuth()
    app = create_panel_app(object(), object(), auth)
    headers = {"Origin": ORIGIN, "Cookie": f"{COOKIE_NAME}={auth.exchange_token(auth.token)}"}
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post("/api/roots/pick-directory", json={}, headers=headers).status_code == 404
