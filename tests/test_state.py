import json
from pathlib import Path

from state import DEFAULT_CONFIG, load_config, resolve_central_skills_dir, save_config


def test_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "config.json")
    assert config == DEFAULT_CONFIG


def test_defaults_are_a_copy(tmp_path):
    config = load_config(tmp_path / "config.json")
    config["server"]["port"] = 1
    assert DEFAULT_CONFIG["server"]["port"] == 8101


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    config = load_config(path)
    config["references"]["old"] = {"path": "C:/x", "description": "d"}
    config["skills"]["disabled"].append("some-skill")
    save_config(config, path)

    loaded = load_config(path)
    assert loaded["references"]["old"]["path"] == "C:/x"
    assert loaded["skills"]["disabled"] == ["some-skill"]


def test_corrupted_json_returns_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")
    assert load_config(path) == DEFAULT_CONFIG


def test_non_dict_json_returns_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_config(path) == DEFAULT_CONFIG


def test_partial_file_is_merged_with_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"server": {"port": 9999}}), encoding="utf-8")
    config = load_config(path)
    assert config["server"]["port"] == 9999
    assert config["server"]["host"] == "0.0.0.0"
    assert config["skills"]["central_dir"] == "~/.agents/skills"


def test_old_file_without_tasks_section_gets_task_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"server": {"port": 8101}}), encoding="utf-8")
    config = load_config(path)
    assert config["tasks"] == DEFAULT_CONFIG["tasks"]
    assert config["tasks"]["max_parallel"] == 2
    assert config["tasks"]["max_wait_s"] == 8


def test_partial_tasks_section_is_merged(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"tasks": {"max_parallel": 4}}), encoding="utf-8")
    config = load_config(path)
    assert config["tasks"]["max_parallel"] == 4
    assert config["tasks"]["max_queued"] == 50


def test_panel_and_approval_defaults(tmp_path):
    config = load_config(tmp_path / "config.json")
    assert config["panel"] == {"enabled": True, "port": 8102, "open_browser": True}
    assert config["tasks"]["approval_ui"] == "panel"
    assert config["tasks"]["approval_expiry_min"] == 60


def test_old_file_without_panel_section_gets_panel_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"tasks": {"max_parallel": 3}}), encoding="utf-8")
    config = load_config(path)
    assert config["panel"]["port"] == 8102
    assert config["tasks"]["approval_ui"] == "panel"
    assert config["tasks"]["max_parallel"] == 3


def test_atomic_save_leaves_no_tmp_file(tmp_path):
    path = tmp_path / "config.json"
    save_config(load_config(path), path)
    assert path.is_file()
    assert not (tmp_path / "config.json.tmp").exists()


def test_save_retries_when_replace_is_temporarily_denied(tmp_path, monkeypatch):
    import state
    path = tmp_path / "config.json"
    real_replace = state.os.replace
    calls = []

    def flaky_replace(src, dst):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError(13, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(state.os, "replace", flaky_replace)
    monkeypatch.setattr(state.time, "sleep", lambda _s: None)
    save_config(load_config(path), path)
    assert len(calls) == 3
    assert path.is_file()
    assert not (tmp_path / "config.json.tmp").exists()


def test_save_gives_up_after_retries_and_cleans_tmp(tmp_path, monkeypatch):
    import pytest
    import state
    path = tmp_path / "config.json"
    calls = []

    def always_denied(src, dst):
        calls.append(1)
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(state.os, "replace", always_denied)
    monkeypatch.setattr(state.time, "sleep", lambda _s: None)
    with pytest.raises(PermissionError):
        save_config(load_config(path), path)
    assert len(calls) == state.REPLACE_RETRIES
    assert not path.exists()
    assert not (tmp_path / "config.json.tmp").exists()


def test_resolve_central_skills_dir_expands_home():
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    resolved = resolve_central_skills_dir(config)
    assert resolved == Path.home() / ".agents" / "skills"
