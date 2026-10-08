import os

import pytest

from skills_engine.conda_env import detect_conda_base, env_python_path, resolve_env_python


def _make_env(env_dir):
    python = env_python_path(env_dir)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    return python


def test_invalid_env_name():
    result = resolve_env_python("bad name; rm", {}, home=None, environ={})
    assert result.python is None
    assert "Niepoprawna" in result.error


def test_explicit_python_file(tmp_path):
    python = tmp_path / "custom" / "python.exe"
    python.parent.mkdir()
    python.write_text("", encoding="utf-8")
    config = {"conda": {"envs": {"data": str(python)}}}
    result = resolve_env_python("data", config, home=tmp_path, environ={})
    assert result.python == python


def test_explicit_env_dir(tmp_path):
    env_dir = tmp_path / "custom-env"
    python = _make_env(env_dir)
    config = {"conda": {"envs": {"data": str(env_dir)}}}
    assert resolve_env_python("data", config, home=tmp_path, environ={}).python == python


def test_found_under_configured_base(tmp_path):
    base = tmp_path / "miniforge"
    python = _make_env(base / "envs" / "data")
    config = {"conda": {"base": str(base)}}
    assert resolve_env_python("data", config, home=tmp_path, environ={}).python == python


def test_found_under_home_conda_envs(tmp_path):
    home = tmp_path / "home"
    python = _make_env(home / ".conda" / "envs" / "data")
    assert resolve_env_python("data", {}, home=home, environ={}).python == python


def test_found_via_environments_txt(tmp_path):
    home = tmp_path / "home"
    (home / ".conda").mkdir(parents=True)
    custom = tmp_path / "elsewhere" / "data"
    python = _make_env(custom)
    (home / ".conda" / "environments.txt").write_text(f"{custom}\n", encoding="utf-8")
    assert resolve_env_python("data", {}, home=home, environ={}).python == python


def test_base_env(tmp_path):
    base = tmp_path / "miniforge"
    python = _make_env(base)
    config = {"conda": {"base": str(base)}}
    assert resolve_env_python("base", config, home=tmp_path, environ={}).python == python


def test_not_found_gives_command(tmp_path):
    result = resolve_env_python("missing", {}, home=tmp_path, environ={})
    assert result.python is None
    assert "conda create -n missing" in result.error
    assert "config.json" in result.error


def test_not_found_mentions_environment_yml(tmp_path):
    yml = tmp_path / "environment.yml"
    result = resolve_env_python("missing", {}, home=tmp_path, environ={}, environment_yml=yml)
    assert "conda env create -n missing" in result.error
    assert str(yml) in result.error


def test_detect_base_prefers_config(tmp_path):
    config = {"conda": {"base": str(tmp_path / "x")}}
    assert detect_conda_base(config, environ={"CONDA_EXE": "ignored"}) == tmp_path / "x"


def test_detect_base_from_conda_exe(tmp_path):
    exe = tmp_path / "miniforge" / "Scripts" / "conda.exe"
    assert detect_conda_base({}, environ={"CONDA_EXE": str(exe)}) == tmp_path / "miniforge"


def test_detect_base_none_when_nothing_known():
    assert detect_conda_base({}, environ={}) is None
