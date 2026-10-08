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


def test_atomic_save_leaves_no_tmp_file(tmp_path):
    path = tmp_path / "config.json"
    save_config(load_config(path), path)
    assert path.is_file()
    assert not (tmp_path / "config.json.tmp").exists()


def test_resolve_central_skills_dir_expands_home():
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    resolved = resolve_central_skills_dir(config)
    assert resolved == Path.home() / ".agents" / "skills"
