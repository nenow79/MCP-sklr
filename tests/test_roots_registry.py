#!/usr/bin/env python3
"""Testy RootsRegistry: lista folderów w configu, zapis, domyślny root, migracja, współbieżność."""
import json
import shutil
import threading
from pathlib import Path

import pytest

from config_store import ConfigStore
from roots import MODE_RO, MODE_RW, RootError, Zones
from roots_registry import RootsRegistry
from state import load_config


def _dir(base, *parts):
    path = Path(base).joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.json"
    central = _dir(tmp_path, "central", "skills")
    store = ConfigStore(load_config(config_path), config_path)
    events = []
    registry = RootsRegistry(store, zones=Zones(), central_dir=central,
                             on_default_change=lambda old, new: events.append((old, new)))
    return registry, store, config_path, central, events, tmp_path


def _saved(config_path):
    return json.loads(config_path.read_text(encoding="utf-8"))


# --- dodawanie ---
def test_first_root_becomes_default(env):
    registry, store, config_path, _, events, tmp = env
    root = registry.add(_dir(tmp, "App"), MODE_RW)
    assert root.alias == "app" and root.default is True and root.mode == MODE_RW
    assert registry.default() == root
    assert _saved(config_path)["roots"][0]["alias"] == "app"
    assert len(events) == 1 and events[0][0] is None and events[0][1].alias == "app"


def test_second_root_is_not_default(env):
    registry, _, _, _, events, tmp = env
    registry.add(_dir(tmp, "a"), MODE_RW)
    second = registry.add(_dir(tmp, "b"), MODE_RO)
    assert second.default is False
    assert registry.default().alias == "a"
    assert len(events) == 1  # domyślny się nie zmienił


def test_make_default_switches_and_notifies(env):
    registry, _, config_path, _, events, tmp = env
    registry.add(_dir(tmp, "a"), MODE_RW)
    registry.add(_dir(tmp, "b"), MODE_RW, make_default=True)
    assert registry.default().alias == "b"
    assert sum(1 for r in _saved(config_path)["roots"] if r["default"]) == 1
    assert events[-1][0].alias == "a" and events[-1][1].alias == "b"


def test_alias_auto_unique_and_explicit(env):
    registry, _, _, _, _, tmp = env
    first = registry.add(_dir(tmp, "x", "app"), MODE_RW)
    second = registry.add(_dir(tmp, "y", "app"), MODE_RO)
    third = registry.add(_dir(tmp, "z"), MODE_RO, alias="mine", description="  opis \n tu ")
    assert (first.alias, second.alias, third.alias) == ("app", "app-2", "mine")
    assert third.description == "opis tu"


def test_invalid_explicit_alias_rejected(env):
    registry, _, _, _, _, tmp = env
    with pytest.raises(RootError):
        registry.add(_dir(tmp, "a"), MODE_RW, alias="Zły Alias")


def test_duplicate_alias_or_path_rejected_and_state_unchanged(env):
    registry, _, config_path, _, _, tmp = env
    folder = _dir(tmp, "a")
    registry.add(folder, MODE_RW, alias="one")
    before = _saved(config_path)
    with pytest.raises(RootError):
        registry.add(_dir(tmp, "b"), MODE_RW, alias="one")
    with pytest.raises(RootError):
        registry.add(folder, MODE_RO, alias="two")
    assert _saved(config_path) == before
    assert len(registry.list()) == 1


def test_frontend_and_backend_in_unrelated_places(env):
    registry, _, _, _, _, tmp = env
    registry.add(_dir(tmp, "disk1", "frontend"), MODE_RW)
    registry.add(_dir(tmp, "disk2", "deep", "backend"), MODE_RW)
    assert {r.alias for r in registry.list()} == {"frontend", "backend"}


def test_nesting_rules(env):
    registry, _, _, _, _, tmp = env
    outer = _dir(tmp, "mono")
    registry.add(outer, MODE_RW)
    registry.add(_dir(outer, "vendor"), MODE_RO)  # ro w rw: dozwolone
    ref = _dir(tmp, "ref")
    registry.add(ref, MODE_RO)
    with pytest.raises(RootError):
        registry.add(_dir(ref, "sub"), MODE_RW)  # rw w ro: błąd


def test_central_skills_dir_rejected_for_rw_allowed_for_ro(env):
    registry, _, _, central, _, _ = env
    with pytest.raises(RootError, match="skills"):
        registry.add(central, MODE_RW)
    assert registry.add(central, MODE_RO).mode == MODE_RO


def test_forbidden_zone_rejected(tmp_path):
    secret = _dir(tmp_path, "home", ".ssh")
    store = ConfigStore(load_config(tmp_path / "c.json"), tmp_path / "c.json")
    registry = RootsRegistry(store, zones=Zones(tree=(secret.resolve(),)), central_dir=tmp_path / "none")
    with pytest.raises(RootError):
        registry.add(secret, MODE_RO)
    assert registry.list() == []


def test_invalid_mode_and_missing_folder(env):
    registry, _, _, _, _, tmp = env
    with pytest.raises(RootError):
        registry.add(_dir(tmp, "a"), "rx")
    with pytest.raises(RootError):
        registry.add(tmp / "gone", MODE_RW)


def test_limit_of_roots(env):
    registry, _, _, _, _, tmp = env
    for i in range(20):
        registry.add(_dir(tmp, f"d{i}"), MODE_RO)
    with pytest.raises(RootError, match="limit"):
        registry.add(_dir(tmp, "extra"), MODE_RO)


# --- odczyt i resolve ---
def test_resolve_variants(env):
    registry, _, _, _, _, tmp = env
    registry.add(_dir(tmp, "front"), MODE_RW)
    registry.add(_dir(tmp, "ref"), MODE_RO)
    for value in (None, "", "  ", "false", "False", "none", False):
        assert registry.resolve(value).alias == "front"
    assert registry.resolve("REF").alias == "ref"
    with pytest.raises(RootError, match="Nieznany folder 'nope'.*front"):
        registry.resolve("nope")
    with pytest.raises(RootError):
        registry.resolve(5)


def test_resolve_without_default_gives_actionable_error(env):
    registry, _, _, _, _, tmp = env
    with pytest.raises(RootError, match="Brak domyślnego folderu"):
        registry.resolve(None)
    registry.add(_dir(tmp, "a"), MODE_RO)
    registry.remove("a")
    with pytest.raises(RootError, match="Brak domyślnego"):
        registry.resolve("false")


def test_find_returns_deepest_root(env):
    registry, _, _, _, _, tmp = env
    outer = _dir(tmp, "app")
    inner = _dir(outer, "vendor")
    registry.add(outer, MODE_RW)
    registry.add(inner, MODE_RO)
    assert registry.find(inner / "x.py").alias == "vendor"
    assert registry.find(outer / "a.py").alias == "app"
    assert registry.find(tmp / "other") is None


def test_list_status_marks_vanished_folder(env):
    registry, _, _, _, _, tmp = env
    folder = _dir(tmp, "temp_dir")
    registry.add(folder, MODE_RW)
    assert registry.list_status()[0]["valid"] is True
    shutil.rmtree(folder)
    entry = registry.list_status()[0]
    assert entry["valid"] is False and entry["reason"]


# --- zmiany ---
def test_update_mode_description_and_alias(env):
    registry, _, config_path, _, _, tmp = env
    registry.add(_dir(tmp, "a"), MODE_RW, alias="a")
    registry.add(_dir(tmp, "b"), MODE_RO, alias="b")
    updated = registry.update("b", description="nowy opis", new_alias="docs")
    assert (updated.alias, updated.description, updated.mode) == ("docs", "nowy opis", MODE_RO)
    assert registry.get("b") is None and registry.get("docs") is not None
    assert registry.update("docs", mode=MODE_RW).mode == MODE_RW
    assert [r["alias"] for r in _saved(config_path)["roots"]] == ["a", "docs"]


def test_update_to_rw_validates_central_dir(env):
    registry, _, _, central, _, _ = env
    registry.add(central, MODE_RO, alias="skills")
    with pytest.raises(RootError, match="skills"):
        registry.update("skills", mode=MODE_RW)
    assert registry.get("skills").mode == MODE_RO


def test_update_rejects_nesting_and_alias_clash(env):
    registry, _, _, _, _, tmp = env
    outer = _dir(tmp, "outer")
    inner = _dir(outer, "inner")
    registry.add(outer, MODE_RO, alias="outer")
    registry.add(inner, MODE_RO, alias="inner")
    with pytest.raises(RootError):
        registry.update("inner", mode=MODE_RW)  # rw w ro
    with pytest.raises(RootError, match="Alias"):
        registry.update("inner", new_alias="outer")


def test_update_description_works_for_vanished_folder(env):
    registry, _, _, _, _, tmp = env
    folder = _dir(tmp, "gone_soon")
    registry.add(folder, MODE_RO, alias="g")
    shutil.rmtree(folder)
    assert registry.update("g", description="stary").description == "stary"


def test_update_unknown_alias(env):
    registry, _, _, _, _, _ = env
    with pytest.raises(RootError):
        registry.update("nope", description="x")


def test_remove_default_leaves_no_default_and_notifies(env):
    registry, _, config_path, _, events, tmp = env
    registry.add(_dir(tmp, "a"), MODE_RW, alias="a")
    registry.add(_dir(tmp, "b"), MODE_RW, alias="b")
    removed = registry.remove("a")
    assert removed.alias == "a"
    assert registry.default() is None
    assert [r["alias"] for r in _saved(config_path)["roots"]] == ["b"]
    assert events[-1][0].alias == "a" and events[-1][1] is None
    with pytest.raises(RootError):
        registry.remove("a")


def test_remove_non_default_does_not_notify(env):
    registry, _, _, _, events, tmp = env
    registry.add(_dir(tmp, "a"), MODE_RW, alias="a")
    registry.add(_dir(tmp, "b"), MODE_RO, alias="b")
    count = len(events)
    registry.remove("b")
    assert len(events) == count


def test_set_default(env):
    registry, _, config_path, _, events, tmp = env
    registry.add(_dir(tmp, "a"), MODE_RW, alias="a")
    registry.add(_dir(tmp, "b"), MODE_RO, alias="b")
    chosen = registry.set_default("b")  # domyślny może być ro
    assert chosen.default is True and registry.default().alias == "b"
    assert [r["default"] for r in _saved(config_path)["roots"]] == [False, True]
    assert events[-1][1].alias == "b"
    count = len(events)
    registry.set_default("b")  # bez zmiany: bez powiadomienia
    assert len(events) == count
    with pytest.raises(RootError):
        registry.set_default("nope")


def test_listener_error_does_not_break_operation(env):
    registry, store, _, _, _, tmp = env
    registry.set_default_listener(lambda old, new: (_ for _ in ()).throw(RuntimeError("boom")))
    root = registry.add(_dir(tmp, "a"), MODE_RW)
    assert registry.get(root.alias) is not None


# --- trwałość i odporność ---
def test_registry_survives_reload_from_disk(env):
    registry, _, config_path, central, _, tmp = env
    registry.add(_dir(tmp, "a"), MODE_RW, alias="a", description="x")
    store2 = ConfigStore(load_config(config_path), config_path)
    registry2 = RootsRegistry(store2, zones=Zones(), central_dir=central)
    assert registry2.default().alias == "a" and registry2.get("a").description == "x"


def test_unparseable_entries_are_hidden_but_kept(env):
    registry, store, config_path, _, _, tmp = env
    store.data["roots"] = [{"alias": "BAD ALIAS", "path": "x", "mode": "rw"}, 42]
    assert registry.list() == []
    registry.add(_dir(tmp, "a"), MODE_RW, alias="a")
    saved = _saved(config_path)["roots"]
    assert len(saved) == 3 and saved[0]["alias"] == "BAD ALIAS" and saved[1] == 42


def test_multiple_defaults_in_file_are_normalized_on_read(env):
    registry, store, _, _, _, tmp = env
    a, b = _dir(tmp, "a"), _dir(tmp, "b")
    store.data["roots"] = [
        {"alias": "a", "path": str(a), "mode": "rw", "default": True},
        {"alias": "b", "path": str(b), "mode": "rw", "default": True},
    ]
    assert [r.default for r in registry.list()] == [True, False]


def test_failed_save_rolls_back(env, monkeypatch):
    registry, _, _, _, _, tmp = env

    def broken_save(config, path=None):
        raise OSError("disk full")

    monkeypatch.setattr("config_store.save_config", broken_save)
    with pytest.raises(OSError):
        registry.add(_dir(tmp, "a"), MODE_RW)
    assert registry.list() == []


# --- check_add ---
def test_check_add_returns_candidate_without_saving(env):
    registry, store, config_path, _, events, tmp = env
    candidate = registry.check_add(_dir(tmp, "App"), MODE_RW, description="  opis  ")
    assert candidate.alias == "app" and candidate.mode == MODE_RW and candidate.default is True
    assert candidate.description == "opis"
    assert registry.list() == [] and events == []
    assert not config_path.exists() or _saved(config_path).get("roots") in (None, [])


def test_check_add_default_false_when_default_exists(env):
    registry, _, _, _, _, tmp = env
    registry.add(_dir(tmp, "a"), MODE_RW)
    assert registry.check_add(_dir(tmp, "b"), MODE_RO).default is False


def test_check_add_raises_like_add(env):
    registry, _, _, _, _, tmp = env
    registry.add(_dir(tmp, "outer"), MODE_RO, alias="outer")
    with pytest.raises(RootError):
        registry.check_add(_dir(tmp, "outer"), MODE_RO, alias="x")
    with pytest.raises(RootError):
        registry.check_add(_dir(tmp, "outer", "in"), MODE_RW)
    with pytest.raises(RootError):
        registry.check_add(tmp / "brak", MODE_RO)
    with pytest.raises(RootError):
        registry.check_add(_dir(tmp, "z"), "zz")
    with pytest.raises(RootError):
        registry.check_add(_dir(tmp, "z"), MODE_RO, alias="outer")


# --- migracja ---
def test_migrate_legacy_config(env):
    registry, store, config_path, _, events, tmp = env
    work = _dir(tmp, "work")
    ref = _dir(tmp, "ref")
    store.data["workspace"]["path"] = str(work)
    store.data["references"] = {"old": {"path": str(ref), "description": "stary"}}
    result = registry.migrate()
    assert result.migrated is True and result.skipped == []
    assert [(r.alias, r.mode, r.default) for r in registry.list()] == [
        ("work", MODE_RW, True), ("old", MODE_RO, False)]
    saved = _saved(config_path)
    assert saved["roots"] == store.data["roots"]
    assert saved["workspace"]["path"] == str(work)  # stare klucze zostają
    assert saved["references"]["old"]["path"] == str(ref)


def test_migrate_is_idempotent_and_keeps_empty_list(env):
    registry, store, _, _, _, _ = env
    assert registry.migrate().migrated is True
    assert store.data["roots"] == []
    assert registry.migrate().migrated is False


def test_migrate_skips_after_user_added_roots(env):
    registry, store, _, _, _, tmp = env
    registry.add(_dir(tmp, "a"), MODE_RW)
    store.data["workspace"]["path"] = str(_dir(tmp, "zzz"))
    assert registry.migrate().migrated is False
    assert [r.alias for r in registry.list()] == ["a"]


# --- współbieżność ---
def test_concurrent_adds_keep_consistent_state(env):
    registry, _, config_path, _, _, tmp = env
    folders = [_dir(tmp, f"proj{i}") for i in range(12)]
    errors = []

    def worker(chunk):
        for folder in chunk:
            try:
                registry.add(folder, MODE_RO)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(folders[i::3],)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    roots = registry.list()
    assert len(roots) == 12 and len({r.alias for r in roots}) == 12
    assert sum(1 for r in roots if r.default) == 1
    assert len(_saved(config_path)["roots"]) == 12
