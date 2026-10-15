"""Odkrywanie docs: limity i guardy pozostają wspólne z ręcznymi bundles."""
import os
import subprocess

import pytest

from okf_engine.bundles import evaluate_bundles
from okf_engine.discovery import effective_knowledge
from roots import Root


def test_discovery_preserves_input_and_avoids_manual_id_collision(tmp_path):
    (tmp_path / "docs").mkdir()
    root = Root("project", str(tmp_path.resolve()), "ro")
    knowledge = {"bundles": []}
    first = effective_knowledge(knowledge, [root])["bundles"][0]
    assert knowledge == {"bundles": []}
    # Niepoprawny wpis nadal rezerwuje swoje id, ale nie zakres dokumentów.
    knowledge["bundles"].append({"id": first["id"]})
    effective = effective_knowledge(knowledge, [root])
    assert effective["bundles"][1]["id"] != first["id"]
    assert evaluate_bundles(effective, [root], allow_project=True)[1].available


def test_discovery_limits_and_skills_guard(tmp_path):
    roots = []
    for name in ("first", "second"):
        base = tmp_path / name
        (base / "docs").mkdir(parents=True)
        roots.append(Root(name, str(base.resolve()), "ro"))
    section = effective_knowledge({"bundles": [], "limits": {"max_bundles": 1}}, roots)
    states = evaluate_bundles(section, roots, allow_project=True)
    assert states[0].available and states[1].reason == "over_limit"
    states = evaluate_bundles(section, roots, tmp_path / "first/docs", allow_project=True)
    assert states[0].reason == "overlaps_skills"


@pytest.mark.parametrize("value", [None, "project", [False], {}])
def test_invalid_optout_configuration_does_not_enable_docs(tmp_path, value):
    (tmp_path / "docs").mkdir()
    root = Root("project", str(tmp_path.resolve()), "ro")
    assert effective_knowledge({"project_docs_disabled": value}, [root])["bundles"] == []


def test_discovery_rejects_symlink_or_windows_junction(tmp_path):
    base, outside = tmp_path / "project", tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    link = base / "docs"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("symlink unavailable")
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        if result.returncode:
            pytest.skip("junction unavailable")
    root = Root("project", str(base.resolve()), "rw")
    section = effective_knowledge({}, [root])
    state = evaluate_bundles(section, [root], allow_project=True)[0]
    assert not state.available and state.reason == "bundle_dir_link"
