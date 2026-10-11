#!/usr/bin/env python3
"""Testy migracji configu sprzed fazy 5.1 (workspace + references) na listę roots."""
import copy
from pathlib import Path

import pytest

from roots import MODE_RO, MODE_RW, Zones, migrate_legacy, needs_migration

NO_ZONES = Zones()


def _dir(base, *parts):
    path = Path(base).joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _config(workspace=None, references=None, roots=None, central=None):
    return {
        "workspace": {"path": str(workspace) if workspace else None},
        "references": references if references is not None else {},
        "skills": {"central_dir": str(central) if central else "~/.agents/skills"},
        "roots": roots,
    }


def test_needs_migration_only_when_roots_is_null():
    assert needs_migration({"roots": None}) is True
    assert needs_migration({}) is True
    assert needs_migration({"roots": []}) is False
    assert needs_migration({"roots": [{"alias": "a"}]}) is False


def test_already_migrated_config_is_not_touched(tmp_path):
    result = migrate_legacy(_config(workspace=_dir(tmp_path, "w"), roots=[]), zones=NO_ZONES)
    assert result.migrated is False and result.roots == [] and result.skipped == []


def test_empty_legacy_config_migrates_to_empty_list():
    result = migrate_legacy(_config(), zones=NO_ZONES)
    assert result.migrated is True and result.roots == [] and result.skipped == []


def test_workspace_becomes_default_rw_root(tmp_path):
    project = _dir(tmp_path, "My Project")
    result = migrate_legacy(_config(workspace=project), zones=NO_ZONES)
    assert len(result.roots) == 1
    root = result.roots[0]
    assert root["alias"] == "my-project" and root["mode"] == MODE_RW
    assert root["default"] is True and root["path"] == str(project.resolve())


def test_references_become_ro_roots(tmp_path):
    old = _dir(tmp_path, "old")
    docs = _dir(tmp_path, "docs")
    refs = {"vibecoder": {"path": str(old), "description": "stary MCP"}, "CSM": str(docs)}
    result = migrate_legacy(_config(references=refs), zones=NO_ZONES)
    by_alias = {r["alias"]: r for r in result.roots}
    assert set(by_alias) == {"vibecoder", "csm"}
    assert by_alias["vibecoder"]["mode"] == MODE_RO and by_alias["vibecoder"]["description"] == "stary MCP"
    assert by_alias["csm"]["mode"] == MODE_RO and by_alias["csm"]["default"] is False
    assert not any(r["default"] for r in result.roots)


def test_workspace_and_references_together_keep_single_default(tmp_path):
    work = _dir(tmp_path, "work")
    ref = _dir(tmp_path, "ref")
    result = migrate_legacy(_config(workspace=work, references={"ref": {"path": str(ref)}}),
                            zones=NO_ZONES)
    assert [r["alias"] for r in result.roots] == ["work", "ref"]
    assert [r["default"] for r in result.roots] == [True, False]


def test_alias_collision_gets_suffix(tmp_path):
    work = _dir(tmp_path, "a", "app")
    ref = _dir(tmp_path, "b", "app")
    result = migrate_legacy(_config(workspace=work, references={"app": {"path": str(ref)}}),
                            zones=NO_ZONES)
    assert [r["alias"] for r in result.roots] == ["app", "app-2"]


def test_invalid_entries_are_skipped_with_reason(tmp_path):
    good = _dir(tmp_path, "good")
    refs = {
        "missing": {"path": str(tmp_path / "gone")},
        "good": {"path": str(good)},
        "broken": 42,
        "nopath": {"description": "x"},
    }
    result = migrate_legacy(_config(references=refs), zones=NO_ZONES)
    assert [r["alias"] for r in result.roots] == ["good"]
    labels = {label for label, _ in result.skipped}
    assert labels == {"references.missing", "references.broken", "references.nopath"}
    assert all(reason for _, reason in result.skipped)


def test_missing_workspace_directory_is_skipped(tmp_path):
    result = migrate_legacy(_config(workspace=tmp_path / "gone"), zones=NO_ZONES)
    assert result.roots == []
    assert result.skipped and result.skipped[0][0] == "workspace"


def test_forbidden_workspace_is_skipped(tmp_path):
    home = _dir(tmp_path, "Users", "me")
    zones = Zones(guard=(home.resolve(), home.parent.resolve()))
    result = migrate_legacy(_config(workspace=home), zones=zones)
    assert result.roots == [] and result.skipped[0][0] == "workspace"


def test_workspace_in_central_skills_dir_is_skipped_but_reference_is_kept(tmp_path):
    central = _dir(tmp_path, ".agents", "skills")
    result = migrate_legacy(
        _config(workspace=central, references={"skills": {"path": str(central)}}, central=central),
        zones=NO_ZONES)
    assert [r["alias"] for r in result.roots] == ["skills"]
    assert result.roots[0]["mode"] == MODE_RO
    assert result.skipped[0][0] == "workspace"


def test_nested_rw_in_ro_reference_is_skipped(tmp_path):
    outer = _dir(tmp_path, "mono")
    inner = _dir(outer, "web")
    cfg = _config(workspace=inner, references={"mono": {"path": str(outer)}})
    # workspace (rw) przetworzony pierwszy; referencja ro obejmująca rw jest odrzucona
    result = migrate_legacy(cfg, zones=NO_ZONES)
    assert [r["alias"] for r in result.roots] == ["web"]
    assert result.skipped[0][0] == "references.mono"


def test_migration_does_not_modify_config(tmp_path):
    cfg = _config(workspace=_dir(tmp_path, "w"), references={"r": {"path": str(_dir(tmp_path, "r"))}})
    before = copy.deepcopy(cfg)
    migrate_legacy(cfg, zones=NO_ZONES)
    assert cfg == before


def test_migration_is_idempotent_when_result_is_stored(tmp_path):
    cfg = _config(workspace=_dir(tmp_path, "w"))
    first = migrate_legacy(cfg, zones=NO_ZONES)
    cfg["roots"] = first.roots
    second = migrate_legacy(cfg, zones=NO_ZONES)
    assert first.migrated is True and second.migrated is False
    assert cfg["roots"] == first.roots


def test_references_with_wrong_container_type_are_ignored(tmp_path):
    cfg = _config()
    cfg["references"] = ["not", "a", "dict"]
    result = migrate_legacy(cfg, zones=NO_ZONES)
    assert result.migrated is True and result.roots == []
