"""Testy zewnętrznego skilla cost-centers; nigdy nie czytają danych CTG."""
import argparse
import importlib.util
import os
from pathlib import Path

import pytest

SKILL_DIR = Path(os.environ.get("COST_CENTERS_DIR") or
                 str(Path.home() / "1_MyScripts" / "agent-dev" / "skills-dev" / "cost-centers"))
SCRIPT = SKILL_DIR / "scripts" / "search.py"
pytestmark = pytest.mark.skipif(not SCRIPT.is_file(), reason=f"Brak skilla: {SCRIPT}")


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location("cost_centers_test", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_highest_year_not_modification_time(module, tmp_path):
    old = tmp_path / "Cost_center_list_2026.xlsx"
    new = tmp_path / "Cost_center_list_2027.xlsx"
    old.touch()
    new.touch()
    os.utime(new, (1, 1))
    assert module.select_file(tmp_path) == new


def test_ignores_archive_temporary_and_nonmatching(module, tmp_path):
    expected = tmp_path / "Cost_center_list_2026.xlsx"
    expected.touch()
    for name in ("~$Cost_center_list_2029.xlsx", "Cost_center_list_2028.xls",
                 "Cost_center_list_2027_copy.xlsx", "Cost_center_list_27.xlsx"):
        (tmp_path / name).touch()
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "Cost_center_list_2030.xlsx").touch()
    (tmp_path / "Cost_center_list_2031.xlsx").mkdir()
    assert module.select_file(tmp_path) == expected


def test_missing_source(module, tmp_path):
    with pytest.raises(ValueError):
        module.select_file(tmp_path / "missing")
    with pytest.raises(ValueError):
        module.select_file(tmp_path)


@pytest.fixture
def frame():
    pd = pytest.importorskip("pandas")
    return pd.DataFrame({
        "S4": ["001", "002", "003", "004"],
        "Area": ["Finance", None, "IT", None],
        "Name": ["Alpha", "a.b", None, None],
        "Responsible": [None, "Jan", "ANNA", None],
    })


@pytest.mark.parametrize("query,expected", [
    ("finance", ["001"]), ("ALPHA", ["001"]), ("anna", ["003"]),
    ("a.b", ["002"]), (".*", []), ("004", []), ("missing", []),
])
def test_literal_search_three_fields_and_nulls(module, frame, query, expected):
    result = module.search_frame(frame, query)
    assert result["S4"].tolist() == expected
    assert result.columns.tolist() == module.COLUMNS


def test_missing_columns(module, frame):
    with pytest.raises(ValueError, match="Responsible"):
        module.search_frame(frame.drop(columns=["Responsible"]), "x")


@pytest.mark.parametrize("value", ["0", "101", "abc", "-1"])
def test_invalid_limit(module, value):
    with pytest.raises(argparse.ArgumentTypeError):
        module.limit_value(value)


def test_valid_limit(module):
    assert module.limit_value("30") == 30


def test_empty_query(module):
    with pytest.raises(SystemExit) as exc:
        module.main(["   "])
    assert exc.value.code == 2


def test_cli_real_synthetic_excel_and_limit(module, tmp_path, monkeypatch, capsys):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    path = tmp_path / "Cost_center_list_2027.xlsx"
    pd.DataFrame({"S4": ["001", "002"], "Area": ["Test", "Test"],
                  "Name": ["Demo A", "Demo B"], "Responsible": ["Fake", None]}).to_excel(
                      path, index=False)
    monkeypatch.setattr(module, "SOURCE_DIR", tmp_path)
    assert module.main(["Test", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert path.name in out
    assert "Dopasowania: 2; pokazano: 1" in out
    assert "001" in out and "002" not in out
    assert "Wynik skrócony" in out


def test_broken_newest_does_not_fallback(module, tmp_path, monkeypatch, capsys):
    pd = pytest.importorskip("pandas")
    for year in (2026, 2027):
        (tmp_path / f"Cost_center_list_{year}.xlsx").touch()
    monkeypatch.setattr(module, "SOURCE_DIR", tmp_path)
    seen = []

    def fail(path, **kwargs):
        seen.append(path.name)
        raise OSError("synthetic failure")

    monkeypatch.setattr(pd, "read_excel", fail)
    assert module.main(["Test"]) == 1
    assert seen == ["Cost_center_list_2027.xlsx"]
    assert "BŁĄD" in capsys.readouterr().err
