"""Testy walidatora skilla skill-writer.

Walidator leży poza repo (w skillu). Ścieżka: zmienna SKILL_WRITER_DIR (folder skilla)
albo ~/.agents/skills/skill-writer. Gdy pliku brak, testy są pomijane.
"""
import importlib.util
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_skill_dir = os.environ.get("SKILL_WRITER_DIR") or str(
    Path.home() / ".agents" / "skills" / "skill-writer")
VALIDATOR = Path(_skill_dir) / "scripts" / "validate_skill.py"

pytestmark = pytest.mark.skipif(
    not VALIDATOR.is_file(), reason=f"brak walidatora: {VALIDATOR}")


@pytest.fixture(scope="module")
def vs():
    spec = importlib.util.spec_from_file_location("validate_skill_under_test", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skill(tmp_path, name="demo", front=None, body="# Demo\nTreść.\n"):
    folder = tmp_path / name
    folder.mkdir()
    if front is None:
        front = f"name: {name}\ndescription: Robi demo. Użyj do testów."
    (folder / "SKILL.md").write_text(f"---\n{front}\n---\n{body}", encoding="utf-8")
    return folder


def _run(vs, folder):
    return vs.validate(folder, str(REPO_ROOT))


def _has(messages, fragment):
    return any(fragment in m for m in messages)


def test_valid_skill_is_clean(vs, tmp_path):
    errors, warnings = _run(vs, _skill(tmp_path))
    assert errors == []
    assert warnings == []


def test_missing_folder(vs, tmp_path):
    errors, _ = _run(vs, tmp_path / "nope")
    assert _has(errors, "Folder nie istnieje")


def test_missing_skill_md(vs, tmp_path):
    folder = tmp_path / "demo"
    folder.mkdir()
    errors, _ = _run(vs, folder)
    assert _has(errors, "Brak pliku SKILL.md")


def test_wrong_case_skill_md(vs, tmp_path):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "skill.md").write_text("---\ndescription: d\n---\nx\n", encoding="utf-8")
    errors, _ = _run(vs, folder)
    assert _has(errors, "Brak pliku SKILL.md")
    assert _has(errors, "skill.md")


def test_missing_description(vs, tmp_path):
    errors, _ = _run(vs, _skill(tmp_path, front="name: demo"))
    assert _has(errors, "description")


def test_missing_frontmatter(vs, tmp_path):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# bez frontmattera\n", encoding="utf-8")
    errors, _ = _run(vs, folder)
    assert _has(errors, "frontmatter")


def test_name_differs_from_folder(vs, tmp_path):
    folder = _skill(tmp_path, front="name: other\ndescription: d")
    errors, warnings = _run(vs, folder)
    assert errors == []
    assert _has(warnings, "różni się")


def test_description_too_long(vs, tmp_path):
    folder = _skill(tmp_path, front="name: demo\ndescription: " + "a" * 1100)
    errors, warnings = _run(vs, folder)
    assert errors == []
    assert _has(warnings, "1024")


def test_missing_referenced_file_is_error(vs, tmp_path):
    folder = _skill(tmp_path, body="Zobacz `references/x.md`.\n")
    errors, _ = _run(vs, folder)
    assert _has(errors, "references/x.md")


def test_existing_referenced_file_ok(vs, tmp_path):
    folder = _skill(tmp_path, body="Zobacz `references/x.md`.\n")
    (folder / "references").mkdir()
    (folder / "references" / "x.md").write_text("x\n", encoding="utf-8")
    errors, warnings = _run(vs, folder)
    assert errors == []
    assert warnings == []


def test_too_deep_reference_warns(vs, tmp_path):
    folder = _skill(tmp_path, body="Zobacz `references/a/b.md`.\n")
    (folder / "references" / "a").mkdir(parents=True)
    (folder / "references" / "a" / "b.md").write_text("x\n", encoding="utf-8")
    errors, warnings = _run(vs, folder)
    assert errors == []
    assert _has(warnings, "głęboka")


def test_metadata_non_string_warns(vs, tmp_path):
    front = "name: demo\ndescription: d\nmetadata:\n  version: 1.0"
    _, warnings = _run(vs, _skill(tmp_path, front=front))
    assert _has(warnings, "metadata.version")


def test_unknown_frontmatter_field_warns(vs, tmp_path):
    front = "name: demo\ndescription: d\nfoo: bar"
    _, warnings = _run(vs, _skill(tmp_path, front=front))
    assert _has(warnings, "'foo'")


def test_conda_env_without_py_warns(vs, tmp_path):
    front = 'name: demo\ndescription: d\nmetadata:\n  conda-env: "data"'
    _, warnings = _run(vs, _skill(tmp_path, front=front))
    assert _has(warnings, "conda-env")


def test_conda_env_with_py_ok(vs, tmp_path):
    front = 'name: demo\ndescription: d\nmetadata:\n  conda-env: "data"'
    folder = _skill(tmp_path, front=front)
    (folder / "scripts").mkdir()
    (folder / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    _, warnings = _run(vs, folder)
    assert not _has(warnings, "conda-env")


def test_confirm_false_warns(vs, tmp_path):
    front = 'name: demo\ndescription: d\nmetadata:\n  vibecoder-confirm: "false"'
    _, warnings = _run(vs, _skill(tmp_path, front=front))
    assert _has(warnings, "vibecoder-confirm")


def test_unsupported_script_extension_warns(vs, tmp_path):
    folder = _skill(tmp_path)
    (folder / "scripts").mkdir()
    (folder / "scripts" / "run.exe").write_bytes(b"MZ")
    _, warnings = _run(vs, folder)
    assert _has(warnings, "run.exe")


def test_long_body_warns(vs, tmp_path):
    folder = _skill(tmp_path, body="linia\n" * 600)
    errors, warnings = _run(vs, folder)
    assert errors == []
    assert _has(warnings, "linii")


def test_main_exit_codes(vs, tmp_path, capsys):
    good = _skill(tmp_path, name="good")
    assert vs.main([str(good), "--engine-path", str(REPO_ROOT)]) == 0
    bad = _skill(tmp_path, name="bad", front="name: bad")
    assert vs.main([str(bad), "--engine-path", str(REPO_ROOT)]) == 1
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "Wynik:" in out
