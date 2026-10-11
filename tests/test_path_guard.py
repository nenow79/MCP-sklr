"""Testy path guarda: ścieżki względne i bezwzględne, składnia Windows, junctiony, tryb ro."""
import os
import subprocess
from pathlib import Path

import pytest

from config_store import ConfigStore
from roots import MODE_RO, MODE_RW, Zones
from roots_registry import RootsRegistry
from server.path_guard import PathError, resolve_path
from state import load_config

BS = chr(92)
windows_only = pytest.mark.skipif(os.name != "nt", reason="test specyficzny dla Windows")


def _dir(base, *parts):
    path = Path(base).joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.json"
    store = ConfigStore(load_config(config_path), config_path)
    registry = RootsRegistry(store, zones=Zones(), central_dir=_dir(tmp_path, "central"))
    app = _dir(tmp_path, "app")
    docs = _dir(tmp_path, "docs")
    inner = _dir(app, "inner")
    registry.add(app, MODE_RW, alias="app")
    registry.add(docs, MODE_RO, alias="docs")
    registry.add(inner, MODE_RO, alias="inner")  # ro wewnątrz rw chroni podfolder

    class Env:
        pass

    e = Env()
    e.registry, e.tmp, e.app, e.docs, e.inner = registry, tmp_path, app, docs, inner
    return e


def _rejected(env, path, **kwargs):
    with pytest.raises(PathError):
        resolve_path(env.registry, path, **kwargs)


# --- ścieżki względne ---

def test_relative_path_resolves_in_default_root(env):
    result = resolve_path(env.registry, "src/main.py")
    assert result.path == (env.app / "src" / "main.py").resolve()
    assert result.root.alias == "app" and result.relative == "src/main.py" and result.writable


def test_empty_path_means_root_only_when_allowed(env):
    result = resolve_path(env.registry, "", allow_empty=True)
    assert result.path == env.app.resolve() and result.relative == "."
    assert resolve_path(env.registry, None, allow_empty=True).relative == "."
    _rejected(env, "")
    _rejected(env, "   ")
    _rejected(env, 5)


def test_dot_segments_inside_root_are_fine(env):
    assert resolve_path(env.registry, "a/./b/../c.txt").relative == "a/c.txt"


def test_parent_escape_rejected(env):
    _rejected(env, "..")
    _rejected(env, "../outside.txt")
    _rejected(env, "a/../../outside.txt")


def test_parent_escape_into_other_root_points_to_it(env):
    with pytest.raises(PathError) as error:
        resolve_path(env.registry, "../docs/x.txt")
    assert "docs" in str(error.value)


# --- ścieżki bezwzględne i wybór roota ---

def test_absolute_path_inside_default_root(env):
    result = resolve_path(env.registry, str(env.app / "x.txt"))
    assert result.root.alias == "app" and result.relative == "x.txt"


def test_absolute_path_selects_root_by_location(env):
    result = resolve_path(env.registry, str(env.docs / "a.txt"))
    assert result.root.alias == "docs" and result.relative == "a.txt" and not result.writable


def test_absolute_path_without_default_selects_root_by_location(env):
    env.registry.remove("app")  # brak domyślnego; docs i inner nadal istnieją
    assert env.registry.default() is None
    for root in (None, "", False, "false"):
        result = resolve_path(env.registry, str(env.docs / "a.txt"), root=root)
        assert result.root.alias == "docs" and result.relative == "a.txt"
        assert not result.writable
    _rejected(env, "a.txt")
    _rejected(env, "", allow_empty=True)
    _rejected(env, str(env.tmp / "outside.txt"))
    _rejected(env, str(env.docs / "a.txt"), root="nie-ma")
    _rejected(env, str(env.inner / "a.txt"), write=True)
    assert resolve_path(env.registry, "a.txt", root="docs").root.alias == "docs"


def test_absolute_path_with_wrong_explicit_root_rejected(env):
    with pytest.raises(PathError) as error:
        resolve_path(env.registry, str(env.docs / "a.txt"), root="app")
    assert "docs" in str(error.value)


def test_absolute_path_outside_all_roots_rejected(env):
    outside = _dir(env.tmp, "outside") / "x.txt"
    _rejected(env, str(outside))
    _rejected(env, str(outside), root="app")


def test_other_drive_rejected(env):
    _rejected(env, "Z:" + BS + "nic" + BS + "plik.txt")


def test_explicit_root_alias_is_used_for_relative_paths(env):
    result = resolve_path(env.registry, "a.txt", root="docs")
    assert result.root.alias == "docs" and result.path == (env.docs / "a.txt").resolve()


def test_default_words_select_default_root(env):
    for word in (None, "", "false", "False", False):
        assert resolve_path(env.registry, "a.txt", root=word).root.alias == "app"


def test_unknown_root_lists_aliases(env):
    with pytest.raises(PathError) as error:
        resolve_path(env.registry, "a.txt", root="nie-ma")
    message = str(error.value)
    assert "app" in message and "docs" in message


# --- składnia Windows ---

def test_unc_and_device_prefixes_rejected(env):
    _rejected(env, BS * 2 + "server" + BS + "share" + BS + "x.txt")
    _rejected(env, "//server/share/x.txt")
    _rejected(env, BS * 2 + "?" + BS + "C:" + BS + "x.txt")
    _rejected(env, BS * 2 + "." + BS + "PhysicalDrive0")


def test_ntfs_streams_rejected(env):
    _rejected(env, "a.txt:stream")
    _rejected(env, "a.txt:stream:$DATA")
    _rejected(env, "dir/a.txt::$DATA")


@pytest.mark.parametrize("name", ["CON", "con", "NUL", "nul.txt", "dir/COM1", "LPT9.log", "AUX.tar.gz", "PRN "])
def test_reserved_names_rejected(env, name):
    _rejected(env, name)


def test_names_resembling_reserved_are_allowed(env):
    for name in ("console.txt", "com10.txt", "null/x.txt", "aux_file.py"):
        assert resolve_path(env.registry, name).relative == name


def test_trailing_dots_and_spaces_rejected(env):
    _rejected(env, "a.")
    _rejected(env, "dir /file.txt")
    _rejected(env, "dir./file.txt")
    _rejected(env, "...")
    _rejected(env, "file.txt ")
    _rejected(env, "dir/file.txt ")
    _rejected(env, str(env.app / "file.txt") + " ")
    assert resolve_path(env.registry, " leading.txt").relative == " leading.txt"


@pytest.mark.parametrize("name", ["a*.py", "a?.py", "a|b", "a<b", "a>b", "a" + chr(34) + "b"])
def test_wildcards_rejected(env, name):
    _rejected(env, name)


def test_ambiguous_slash_and_drive_forms_rejected(env):
    _rejected(env, "/etc/passwd")
    _rejected(env, BS + "Windows" + BS + "win.ini")
    _rejected(env, "C:foo.txt")


def test_control_characters_rejected(env):
    _rejected(env, "a" + chr(0) + "b")
    _rejected(env, "a" + chr(10) + "b")
    _rejected(env, "a" + chr(9) + "b")
    for control in (chr(9), chr(10), chr(13)):
        _rejected(env, control + "file.txt")
        _rejected(env, "file.txt" + control)
        _rejected(env, control, allow_empty=True)


def test_normal_names_with_unusual_characters_allowed(env):
    for name in ("my file (1).txt", "ąęł/zażółć.txt", "a-b_c.d.e", "[x]/{y}/#z!.txt", "a'b.txt"):
        assert resolve_path(env.registry, name).relative == name


@windows_only
def test_case_insensitive_root_match(env):
    result = resolve_path(env.registry, str(env.app).upper() + BS + "X.TXT")
    assert result.root.alias == "app"


# --- tryb zapisu ---

def test_write_allowed_in_rw_and_denied_in_ro(env):
    assert resolve_path(env.registry, "x.txt", write=True).writable
    with pytest.raises(PathError) as error:
        resolve_path(env.registry, "x.txt", root="docs", write=True)
    assert "tylko do odczytu" in str(error.value)
    assert resolve_path(env.registry, "x.txt", root="docs").writable is False


def test_nested_ro_inside_rw_blocks_write_but_allows_read(env):
    read = resolve_path(env.registry, "inner/x.txt")
    assert read.root.alias == "app" and read.owner.alias == "inner" and read.relative == "inner/x.txt"
    assert read.writable is False
    with pytest.raises(PathError):
        resolve_path(env.registry, "inner/x.txt", write=True)
    with pytest.raises(PathError):
        resolve_path(env.registry, str(env.inner / "x.txt"), write=True)
    assert resolve_path(env.registry, "outer.txt", write=True).writable


# --- junctiony i symlinki ---

def _junction(link, target):
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"nie udało się utworzyć junctiona: {result.stdout} {result.stderr}")


@windows_only
def test_junction_pointing_outside_root_rejected(env):
    outside = _dir(env.tmp, "outside")
    (outside / "secret.txt").write_text("tajne", encoding="utf-8")
    _junction(env.app / "link", outside)
    _rejected(env, "link/secret.txt")
    _rejected(env, "link")
    _rejected(env, str(env.app / "link" / "secret.txt"))


@windows_only
def test_junction_pointing_inside_root_allowed(env):
    target = _dir(env.app, "real")
    (target / "a.txt").write_text("x", encoding="utf-8")
    _junction(env.app / "alias-dir", target)
    result = resolve_path(env.registry, "alias-dir/a.txt")
    assert result.path == (target / "a.txt").resolve()


def test_symlink_pointing_outside_root_rejected(env):
    outside = _dir(env.tmp, "outside")
    link = env.app / "slink"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("brak uprawnień do tworzenia symlinków (Developer Mode)")
    _rejected(env, "slink/x.txt")
