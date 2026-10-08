from pathlib import Path

from state import DEFAULT_CONFIG, load_config, resolve_workspace_path


def test_workspace_default_is_none(tmp_path):
    config = load_config(tmp_path / "config.json")
    assert config["workspace"]["path"] is None
    assert resolve_workspace_path(config) is None


def test_workspace_path_is_resolved(tmp_path):
    config = {"workspace": {"path": str(tmp_path)}}
    assert resolve_workspace_path(config) == Path(tmp_path)


def test_workspace_missing_key_is_none():
    assert resolve_workspace_path({}) is None


def test_default_config_has_workspace():
    assert "workspace" in DEFAULT_CONFIG
