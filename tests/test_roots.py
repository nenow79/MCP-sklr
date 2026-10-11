#!/usr/bin/env python3
"""Testy roots.py: alias, model, strefy zakazane, walidacja ścieżek, zagnieżdżanie."""
import os
import subprocess
from pathlib import Path

import pytest

from roots import (
    MAX_ROOTS, MODE_RO, MODE_RW, Root, RootError, Zones, check_new_root, clean_description,
    find_root_for, forbidden_zones, sanitize_alias, unique_alias, validate_alias, validate_root,
)

NO_ZONES = Zones()  # katalog tymczasowy leży w %LOCALAPPDATA%, więc testy podają własne strefy


def _dir(base, *parts):
    path = Path(base).joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- alias ---
@pytest.mark.parametrize("raw,expected", [
    ("CSM", "csm"), ("My App!", "my-app"), ("  front_end ", "front_end"),
    ("Ąę", "root"), ("", "root"), (None, "root"), ("--a--", "a"), ("a" * 50, "a" * 32),
])
def test_sanitize_alias(raw, expected):
    assert sanitize_alias(raw) == expected


def test_unique_alias_adds_suffix_case_insensitively():
    assert unique_alias("app", []) == "app"
    assert unique_alias("app", ["APP"]) == "app-2"
    assert unique_alias("app", ["app", "app-2"]) == "app-3"


def test_unique_alias_keeps_max_length():
    result = unique_alias("a" * 32, ["a" * 32])
    assert len(result) == 32 and result.endswith("-2")


@pytest.mark.parametrize("alias", ["a", "front-end", "a_b", "0abc", "x" * 32])
def test_validate_alias_ok(alias):
    assert validate_alias(alias) == alias


@pytest.mark.parametrize("alias", ["", "-a", "_a", "A", "a b", "ą", "x" * 33, None, 5])
def test_validate_alias_errors(alias):
    with pytest.raises(RootError):
        validate_alias(alias)


def test_clean_description_trims_and_limits():
    assert clean_description("  a \n b  ") == "a b"
    assert clean_description(None) == ""
    assert len(clean_description("x" * 500)) == 200


# --- model ---
def test_root_from_dict_roundtrip():
    data = {"alias": "app", "path": "C:/x", "mode": "rw", "description": "d", "default": True}
    root = Root.from_dict(data)
    assert root.to_dict() == data


@pytest.mark.parametrize("data", [
    None, [], {"alias": "A", "path": "x", "mode": "rw"}, {"alias": "a", "path": "x", "mode": "rx"},
    {"alias": "a", "path": "", "mode": "rw"}, {"alias": "a", "path": 5, "mode": "rw"},
    {"alias": "a", "path": "x", "mode": "rw", "default": "yes"},
])
def test_root_from_dict_errors(data):
    with pytest.raises(RootError):
        Root.from_dict(data)


# --- walidacja ścieżek ---
def test_valid_directory_returns_resolved_path(tmp_path):
    project = _dir(tmp_path, "proj")
    assert validate_root(str(project), MODE_RW, zones=NO_ZONES) == project.resolve()


def test_invalid_mode(tmp_path):
    with pytest.raises(RootError):
        validate_root(str(tmp_path), "xx", zones=NO_ZONES)


@pytest.mark.parametrize("value", ["", "   ", None, 5])
def test_empty_or_wrong_type_path(value):
    with pytest.raises(RootError):
        validate_root(value, MODE_RO, zones=NO_ZONES)


def test_missing_directory(tmp_path):
    with pytest.raises(RootError, match="nie istnieje"):
        validate_root(str(tmp_path / "nope"), MODE_RO, zones=NO_ZONES)


def test_file_is_not_a_directory(tmp_path):
    file = tmp_path / "a.txt"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(RootError, match="nie jest folder|To nie jest"):
        validate_root(str(file), MODE_RO, zones=NO_ZONES)


def test_relative_path_rejected():
    with pytest.raises(RootError, match="bezwzględna"):
        validate_root("some/relative", MODE_RO, zones=NO_ZONES)


@pytest.mark.parametrize("raw", ["\\\\server\\share\\x", "//server/share/x", "\\\\?\\C:\\x"])
def test_unc_and_extended_prefix_rejected(raw):
    with pytest.raises(RootError, match="UNC"):
        validate_root(raw, MODE_RO, zones=NO_ZONES)


def test_drive_root_rejected(tmp_path):
    with pytest.raises(RootError, match="główny"):
        validate_root(tmp_path.anchor, MODE_RO, zones=NO_ZONES)


# --- strefy zakazane ---
def test_guard_zone_blocks_home_and_its_parent_but_not_projects_inside(tmp_path):
    home = _dir(tmp_path, "Users", "me")
    project = _dir(home, "projects", "app")
    zones = Zones(guard=(home.resolve(), home.parent.resolve()))
    with pytest.raises(RootError):
        validate_root(str(home), MODE_RO, zones=zones)
    with pytest.raises(RootError):
        validate_root(str(home.parent), MODE_RO, zones=zones)
    with pytest.raises(RootError):  # przodek strefy (zawiera katalog domowy)
        validate_root(str(tmp_path), MODE_RO, zones=zones)
    assert validate_root(str(project), MODE_RW, zones=zones) == project.resolve()


def test_tree_zone_blocks_itself_inside_and_ancestors(tmp_path):
    appdata = _dir(tmp_path, "home", "AppData", "Local")
    chrome = _dir(appdata, "Google", "Chrome", "User Data")
    sibling = _dir(tmp_path, "home", "projects")
    zones = Zones(tree=(appdata.resolve(),))
    for blocked in (appdata, chrome, appdata.parent, appdata.parent.parent):
        with pytest.raises(RootError):
            validate_root(str(blocked), MODE_RO, zones=zones)
    assert validate_root(str(sibling), MODE_RO, zones=zones) == sibling.resolve()


def test_forbidden_zones_cover_secret_dirs_even_if_missing(tmp_path):
    home = _dir(tmp_path, "h")
    zones = forbidden_zones(env={}, home=home)
    names = {zone.name for zone in zones.tree}
    assert {".ssh", ".aws", ".azure", ".kube", ".gnupg"} <= names
    assert home.resolve() in zones.guard and home.parent.resolve() in zones.guard


def test_forbidden_zones_read_environment(tmp_path):
    appdata = _dir(tmp_path, "ad")
    local = _dir(tmp_path, "lad")
    zones = forbidden_zones(env={"APPDATA": str(appdata), "LOCALAPPDATA": str(local), "WINDIR": ""},
                            home=_dir(tmp_path, "h"))
    assert appdata.resolve() in zones.tree and local.resolve() in zones.tree
    assert len(zones.tree) == len(set(zones.tree))


def test_secret_dir_blocked_when_present(tmp_path):
    home = _dir(tmp_path, "h")
    ssh = _dir(home, ".ssh")
    zones = forbidden_zones(env={}, home=home)
    with pytest.raises(RootError):
        validate_root(str(ssh), MODE_RO, zones=zones)


# --- katalog centralny skills ---
def test_central_skills_dir_blocked_for_rw_only(tmp_path):
    central = _dir(tmp_path, ".agents", "skills")
    inside = _dir(central, "hello-skill")
    parent = central.parent
    for blocked in (central, inside, parent):
        with pytest.raises(RootError, match="skills"):
            validate_root(str(blocked), MODE_RW, zones=NO_ZONES, central_dir=central)
        assert validate_root(str(blocked), MODE_RO, zones=NO_ZONES, central_dir=central)


def test_unrelated_rw_allowed_with_central_dir(tmp_path):
    central = _dir(tmp_path, ".agents", "skills")
    project = _dir(tmp_path, "proj")
    assert validate_root(str(project), MODE_RW, zones=NO_ZONES, central_dir=central)


# --- junction (Windows) ---
def _make_junction(link, target):
    if os.name != "nt":
        pytest.skip("junction tylko na Windows")
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"nie można utworzyć junction: {result.stderr.strip()}")


def test_junction_is_resolved_before_zone_check(tmp_path):
    secret = _dir(tmp_path, "home", ".ssh")
    link = tmp_path / "innocent"
    _make_junction(link, secret)
    zones = Zones(tree=(secret.resolve(),))
    with pytest.raises(RootError):
        validate_root(str(link), MODE_RO, zones=zones)


def test_junction_to_allowed_folder_returns_target(tmp_path):
    target = _dir(tmp_path, "real")
    link = tmp_path / "alias_dir"
    _make_junction(link, target)
    assert validate_root(str(link), MODE_RW, zones=NO_ZONES) == target.resolve()


# --- zagnieżdżanie i duplikaty ---
def _root(alias, path, mode=MODE_RW):
    return Root(alias, str(path), mode)


def test_duplicate_alias_case_insensitive(tmp_path):
    existing = [_root("app", _dir(tmp_path, "a"))]
    with pytest.raises(RootError, match="Alias"):
        check_new_root(_root("APP", _dir(tmp_path, "b")), existing)


def test_duplicate_path_rejected(tmp_path):
    folder = _dir(tmp_path, "a")
    with pytest.raises(RootError, match="już na liście"):
        check_new_root(_root("other", folder, MODE_RO), [_root("app", folder)])


def test_ro_inside_rw_allowed(tmp_path):
    outer = _dir(tmp_path, "app")
    inner = _dir(outer, "vendor")
    check_new_root(_root("vendor", inner, MODE_RO), [_root("app", outer, MODE_RW)])


def test_rw_inside_ro_rejected(tmp_path):
    outer = _dir(tmp_path, "ref")
    inner = _dir(outer, "sub")
    with pytest.raises(RootError, match="tylko do odczytu"):
        check_new_root(_root("sub", inner, MODE_RW), [_root("ref", outer, MODE_RO)])


def test_ro_containing_rw_rejected(tmp_path):
    outer = _dir(tmp_path, "ref")
    inner = _dir(outer, "work")
    with pytest.raises(RootError, match="nie może zawierać"):
        check_new_root(_root("ref", outer, MODE_RO), [_root("work", inner, MODE_RW)])


def test_rw_and_rw_nested_allowed(tmp_path):
    outer = _dir(tmp_path, "mono")
    inner = _dir(outer, "web")
    check_new_root(_root("web", inner, MODE_RW), [_root("mono", outer, MODE_RW)])


def test_separate_frontend_and_backend_without_common_parent(tmp_path):
    frontend = _dir(tmp_path, "drive1", "frontend")
    backend = _dir(tmp_path, "drive2", "deep", "backend")
    roots = [_root("frontend", frontend)]
    check_new_root(_root("backend", backend), roots)


def test_limit_of_roots(tmp_path):
    existing = [_root(f"r{i}", _dir(tmp_path, f"d{i}")) for i in range(MAX_ROOTS)]
    with pytest.raises(RootError, match="limit"):
        check_new_root(_root("extra", _dir(tmp_path, "extra")), existing)


def test_find_root_for_picks_deepest(tmp_path):
    outer = _dir(tmp_path, "app")
    inner = _dir(outer, "vendor")
    roots = [_root("app", outer, MODE_RW), _root("vendor", inner, MODE_RO)]
    assert find_root_for(inner / "x.py", roots).alias == "vendor"
    assert find_root_for(outer / "main.py", roots).alias == "app"
    assert find_root_for(tmp_path / "elsewhere", roots) is None
