"""Testy rejestru bundles OKF (C1b): walidacja wpisów, wiązanie z rootem, kolizje, limity."""
import copy
import os
import subprocess
from pathlib import Path

import pytest

from okf_engine.bundles import BundleError, evaluate_bundles, parse_entry
from roots import MODE_RO, MODE_RW, Root


def make_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError, AttributeError):
        pass
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if result.returncode == 0:
            return
    pytest.skip("nie można utworzyć linku w tym środowisku")


@pytest.fixture
def env(tmp_path):
    base = tmp_path / "okf"
    for name in ("general", "other", "skills"):
        (base / name).mkdir(parents=True)
    root = Root("okf", str(base.resolve()), MODE_RW)
    return base, root


def entry(base, **overrides):
    data = {"id": "general", "label": "Wiedza", "scope": "central", "root": base.alias,
            "subpath": "general", "root_path": base.path, "description": "opis", "enabled": True}
    data.update(overrides)
    return data


def knowledge(*bundles, limits=None):
    return {"central_dir": "x", "bundles": list(bundles), "limits": limits if limits is not None else {}}


def only(states):
    assert len(states) == 1
    return states[0]


# --- poprawny wpis ---

def test_available_entry(env):
    base, root = env
    state = only(evaluate_bundles(knowledge(entry(root)), [root]))
    assert state.available and state.writable and state.reason == "" and state.id == "general"
    assert state.path == (base / "general").resolve() and state.entry.label == "Wiedza"


def test_ro_root_is_readable_not_writable(env):
    _, root = env
    ro = Root(root.alias, root.path, MODE_RO)
    state = only(evaluate_bundles(knowledge(entry(root)), [ro]))
    assert state.available and not state.writable


def test_root_alias_lookup_ignores_case(env):
    _, root = env
    upper = Root("OKF", root.path, MODE_RW)  # alias roota w innej wielkości ni w wpisie bundle
    assert only(evaluate_bundles(knowledge(entry(root)), [upper])).available


def test_subpath_empty_means_root_directory(env):
    base, root = env
    state = only(evaluate_bundles(knowledge(entry(root, subpath="")), [root]))
    assert state.available and state.path == base.resolve()


def test_new_entry_is_disabled_by_default(env):
    _, root = env
    raw = entry(root)
    del raw["enabled"]
    assert parse_entry(raw).enabled is False
    state = only(evaluate_bundles(knowledge(raw), [root]))
    assert not state.available and state.reason == "disabled" and state.path is not None


def test_disabled_entry(env):
    _, root = env
    state = only(evaluate_bundles(knowledge(entry(root, enabled=False)), [root]))
    assert not state.available and not state.writable and state.reason == "disabled"


# --- wiązanie z rootem ---

def test_project_scope_is_unavailable(env):
    _, root = env
    state = only(evaluate_bundles(knowledge(entry(root, scope="project")), [root]))
    assert not state.available and state.reason == "scope_project_unsupported"


def test_missing_root(env):
    _, root = env
    assert only(evaluate_bundles(knowledge(entry(root)), [])).reason == "root_missing"
    other = Root("another", root.path, MODE_RW)
    assert only(evaluate_bundles(knowledge(entry(root)), [other])).reason == "root_missing"


def test_root_moved_to_other_path(env, tmp_path):
    _, root = env
    (tmp_path / "elsewhere" / "general").mkdir(parents=True)
    moved = Root(root.alias, str((tmp_path / "elsewhere").resolve()), MODE_RW)
    state = only(evaluate_bundles(knowledge(entry(root)), [moved]))
    assert not state.available and state.reason == "root_changed" and state.path is None


def test_bundle_directory_missing_or_file(env):
    base, root = env
    assert only(evaluate_bundles(knowledge(entry(root, subpath="nope")), [root])).reason == "bundle_dir_missing"
    (base / "afile").write_text("x", encoding="utf-8")
    assert only(evaluate_bundles(knowledge(entry(root, subpath="afile")), [root])).reason == "bundle_dir_missing"


def test_bundle_directory_link_rejected(env, tmp_path):
    base, root = env
    outside = tmp_path / "outside"
    outside.mkdir()
    make_link(base / "linked", outside)
    assert only(evaluate_bundles(knowledge(entry(root, subpath="linked")), [root])).reason == "bundle_dir_link"


# --- skills, duplikaty, nakładanie ---

def test_skills_directory_is_never_a_bundle(env):
    base, root = env
    skills = base / "skills"
    assert only(evaluate_bundles(knowledge(entry(root, subpath="skills")), [root], skills)).reason == "overlaps_skills"
    assert only(evaluate_bundles(knowledge(entry(root, subpath="")), [root], skills)).reason == "overlaps_skills"
    (skills / "inner").mkdir()
    assert only(evaluate_bundles(knowledge(entry(root, subpath="skills/inner")), [root], skills)).reason == "overlaps_skills"
    assert only(evaluate_bundles(knowledge(entry(root)), [root], skills)).available


def test_duplicate_ids_block_all_copies(env):
    _, root = env
    states = evaluate_bundles(knowledge(entry(root), entry(root, subpath="other")), [root])
    assert [s.reason for s in states] == ["duplicate_id", "duplicate_id"]


def test_same_directory_twice(env):
    _, root = env
    states = evaluate_bundles(knowledge(entry(root), entry(root, id="second")), [root])
    assert [s.reason for s in states] == ["duplicate_directory", "duplicate_directory"]


def test_nested_directories(env):
    base, root = env
    (base / "general" / "sub").mkdir()
    states = evaluate_bundles(knowledge(entry(root), entry(root, id="nested", subpath="general/sub")), [root])
    assert [s.reason for s in states] == ["overlapping_directory", "overlapping_directory"]


def test_disabled_bundle_still_reserves_its_directory(env):
    _, root = env
    states = evaluate_bundles(knowledge(entry(root, enabled=False), entry(root, id="second")), [root])
    assert [s.reason for s in states] == ["duplicate_directory", "duplicate_directory"]


def test_sibling_directories_are_fine(env):
    _, root = env
    states = evaluate_bundles(knowledge(entry(root), entry(root, id="second", subpath="other")), [root])
    assert all(s.available for s in states)


# --- niepoprawne wpisy ---

@pytest.mark.parametrize("overrides,field", [
    ({"id": "Bad Id"}, "id"), ({"id": ""}, "id"), ({"id": 5}, "id"), ({"id": "x" * 33}, "id"),
    ({"label": ""}, "label"), ({"label": "   "}, "label"), ({"label": "x" * 201}, "label"),
    ({"label": "a\nb"}, "label"), ({"label": "a\u202eb"}, "label"), ({"label": 5}, "label"),
    ({"description": "x" * 1001}, "description"), ({"description": "a\x00"}, "description"),
    ({"description": None}, "description"),
    ({"scope": "global"}, "scope"), ({"scope": None}, "scope"),
    ({"root": "Bad Root"}, "root"), ({"root": ""}, "root"),
    ({"root_path": "relative/path"}, "root_path"), ({"root_path": ""}, "root_path"), ({"root_path": 5}, "root_path"),
    ({"enabled": "yes"}, "enabled"), ({"enabled": 1}, "enabled"),
    ({"subpath": ".."}, "subpath"), ({"subpath": "a/../b"}, "subpath"), ({"subpath": "/abs"}, "subpath"),
    ({"subpath": "a\\b"}, "subpath"), ({"subpath": "c:/x"}, "subpath"), ({"subpath": "a//b"}, "subpath"),
    ({"subpath": "con"}, "subpath"), ({"subpath": 5}, "subpath"),
])
def test_invalid_entries(env, overrides, field):
    _, root = env
    raw = entry(root, **overrides)
    with pytest.raises(BundleError) as info:
        parse_entry(raw)
    assert info.value.code == field
    state = only(evaluate_bundles(knowledge(raw), [root]))
    assert not state.available and state.reason == "invalid_entry" and state.detail == field
    assert state.entry is None and state.path is None


@pytest.mark.parametrize("raw", [None, 5, "x", [], {}])
def test_non_mapping_or_empty_entries(raw):
    with pytest.raises(BundleError):
        parse_entry(raw)


def test_missing_required_fields(env):
    _, root = env
    for field in ("id", "label", "scope", "root", "root_path"):
        raw = entry(root)
        del raw[field]
        with pytest.raises(BundleError) as info:
            parse_entry(raw)
        assert info.value.code == field


def test_invalid_entry_does_not_block_others_and_is_not_mutated(env):
    _, root = env
    config = knowledge(entry(root, id="Bad Id"), entry(root, id="good"))
    snapshot = copy.deepcopy(config)
    states = evaluate_bundles(config, [root])
    assert [s.reason for s in states] == ["invalid_entry", ""] and states[0].id is None
    assert states[1].available and config == snapshot


def test_invalid_entry_keeps_safe_id(env):
    _, root = env
    state = only(evaluate_bundles(knowledge(entry(root, label="")), [root]))
    assert state.id == "general" and state.reason == "invalid_entry"


def test_bundles_not_a_list_gives_nothing():
    assert evaluate_bundles({"bundles": "oops"}, []) == []
    assert evaluate_bundles(None, []) == []
    assert evaluate_bundles({}, []) == []


# --- limity ---

def test_invalid_limits_block_every_entry(env):
    _, root = env
    config = knowledge(entry(root), entry(root, id="second", subpath="other"), limits={"max_bundles": 0})
    states = evaluate_bundles(config, [root])
    assert [s.reason for s in states] == ["limits_invalid", "limits_invalid"]
    assert states[0].detail == "max_bundles" and not any(s.available for s in states)


def test_non_mapping_limits_block_entries(env):
    _, root = env
    config = {"bundles": [entry(root)], "limits": None}
    assert only(evaluate_bundles(config, [root])).reason == "limits_invalid"


def test_max_bundles_limit(env):
    _, root = env
    config = knowledge(entry(root), entry(root, id="second", subpath="other"), limits={"max_bundles": 1})
    assert [s.reason for s in evaluate_bundles(config, [root])] == ["", "over_limit"]


def test_deeper_ro_root_controls_bundle_writable(env):
    base, root = env
    inner = Root("protected", str((base / "general").resolve()), MODE_RO)
    state = only(evaluate_bundles(knowledge(entry(root)), [root, inner]))
    assert state.available and not state.writable
    assert only(evaluate_bundles(knowledge(entry(root)), [root])).writable


def test_sibling_ro_root_does_not_block_bundle(env):
    base, root = env
    sibling = Root("protected", str((base / "other").resolve()), MODE_RO)
    assert only(evaluate_bundles(knowledge(entry(root)), [root, sibling])).writable


@pytest.mark.parametrize("subpath", ['a*b', 'a?b', 'a|b', 'a<b', 'a>b', 'a"b', 'a\x00b'])
def test_bundle_subpath_bad_chars(env, subpath):
    _, root = env
    with pytest.raises(BundleError) as info:
        parse_entry(entry(root, subpath=subpath))
    assert info.value.code == "subpath"
