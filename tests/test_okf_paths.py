"""Testy ścieżek OKF (C1b): normalizacja linków, kolizje wielkości liter, linki i junctiony na dysku."""
import os
import subprocess
from pathlib import Path

import pytest

from okf_engine.models import DocumentIdentity, IdentityError
from okf_engine.paths import (
    PathSecurityError, find_case_collisions, is_link, locate_bundle_dir, normalize_link, relative_file,
    resolve_concept_file)


def make_link(link: Path, target: Path, directory: bool = True) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
        return
    except (OSError, NotImplementedError, AttributeError):
        pass
    if os.name == "nt" and directory:
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if result.returncode == 0:
            return
    pytest.skip("nie można utworzyć linku w tym środowisku")


def code_of(func, *args):
    with pytest.raises(PathSecurityError) as info:
        func(*args)
    return info.value.code


# --- normalize_link ---

@pytest.mark.parametrize("link,source,expected", [
    ("/tables/orders.md", "a/b", "tables/orders"),
    ("/Tables/Orders.md", "x", "Tables/Orders"),
    ("./other.md", "tables/orders", "tables/other"),
    ("other.md", "a", "other"),
    ("other.md", "tables/orders", "tables/other"),
    ("../x/y.md", "tables/orders", "x/y"),
    ("/a/../b.md", "x", "b"),
    ("/tables/orders.md#schema", "x", "tables/orders"),
    ("/tables/orders.md?x=1", "x", "tables/orders"),
    ("/tables/ORDERS.MD", "x", "tables/ORDERS"),
    ("/zażółć/gęślą.md", "x", "zażółć/gęślą"),
])
def test_normalize_link_ok(link, source, expected):
    assert normalize_link(link, source) == expected


@pytest.mark.parametrize("link", [
    "", "#only", "https://example.com/x.md", "mailto:a@b.md", "C:/x.md", "c:\\x.md", "//host/share/x.md",
    "a\\b.md", "/tables/", "/tables/orders", "/tables/orders.txt", "/index.md", "/a/log.md", "/a/con.md",
    "/x/.md", "/a//b.md", "../../x.md", "/../b.md", "/a/\x00b.md", "x" * 1100 + ".md", "/a/b .md",
    "/a/dir./b.md", 5, None, b"/a.md",
])
def test_normalize_link_rejected(link):
    assert normalize_link(link, "tables/orders") is None


def test_percent_encoding_is_not_decoded():
    result = normalize_link("/a/%2e%2e/b.md", "x")
    assert result != "b" and (result is None or ".." not in result.split("/"))


def test_invalid_source_id_gives_none():
    assert normalize_link("/a.md", "../x") is None
    assert normalize_link("/a.md", "") is None


# --- nazwy Windows w concept_id (walidacja z models) ---

@pytest.mark.parametrize("concept_id", ["con", "a/COM1", "nul.txt", "lpt9", "x/Aux.v2", "prn "])
def test_windows_device_names_rejected(concept_id):
    with pytest.raises(IdentityError):
        DocumentIdentity("general", concept_id)


@pytest.mark.parametrize("concept_id", ["console", "com10", "comm", "a/auxiliary", "null-values"])
def test_similar_names_allowed(concept_id):
    assert DocumentIdentity("general", concept_id).concept_id == concept_id


def test_relative_file():
    assert relative_file("tables/orders") == "tables/orders.md"
    with pytest.raises(IdentityError):
        relative_file("a.md")


# --- kolizje wielkości liter ---

def test_case_collisions():
    assert find_case_collisions(["a/B", "a/b", "c"]) == [("a/B", "a/b")]
    assert find_case_collisions(["é", "e\u0301"]) == [("e\u0301", "é")] or len(find_case_collisions(["é", "e\u0301"])) == 1
    assert find_case_collisions(["a", "b", "a"]) == []
    assert find_case_collisions([]) == []


# --- locate_bundle_dir ---

def test_locate_bundle_dir(tmp_path):
    (tmp_path / "okf" / "general" / "deep").mkdir(parents=True)
    root = str(tmp_path / "okf")
    assert locate_bundle_dir(root, "general") == (tmp_path / "okf" / "general").resolve()
    assert locate_bundle_dir(root, "general/deep") == (tmp_path / "okf" / "general" / "deep").resolve()
    assert locate_bundle_dir(root, "") == (tmp_path / "okf").resolve()


@pytest.mark.parametrize("subpath,code", [
    ("missing", "not_found"), ("..", "invalid_subpath"), ("a/../b", "invalid_subpath"),
    ("a\\b", "invalid_subpath"), ("/abs", "invalid_subpath"), ("c:/x", "invalid_subpath"),
    ("a//b", "invalid_subpath"), ("con", "invalid_subpath"),
])
def test_locate_bundle_dir_rejects(tmp_path, subpath, code):
    (tmp_path / "okf").mkdir()
    assert code_of(locate_bundle_dir, str(tmp_path / "okf"), subpath) == code


def test_locate_bundle_dir_file_and_missing_root(tmp_path):
    (tmp_path / "okf").mkdir()
    (tmp_path / "okf" / "file").write_text("x", encoding="utf-8")
    assert code_of(locate_bundle_dir, str(tmp_path / "okf"), "file") == "not_found"
    assert code_of(locate_bundle_dir, str(tmp_path / "nope"), "") == "not_found"


def test_locate_bundle_dir_rejects_link(tmp_path):
    (tmp_path / "okf").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    make_link(tmp_path / "okf" / "general", outside)
    assert is_link(tmp_path / "okf" / "general")
    assert code_of(locate_bundle_dir, str(tmp_path / "okf"), "general") == "link"


def test_locate_bundle_dir_rejects_link_in_the_middle(tmp_path):
    (tmp_path / "okf").mkdir()
    outside = tmp_path / "outside"
    (outside / "inner").mkdir(parents=True)
    make_link(tmp_path / "okf" / "hop", outside)
    assert code_of(locate_bundle_dir, str(tmp_path / "okf"), "hop/inner") == "link"


# --- resolve_concept_file ---

@pytest.fixture
def bundle(tmp_path):
    base = tmp_path / "bundle"
    (base / "tables").mkdir(parents=True)
    (base / "tables" / "Orders.md").write_text("---\ntype: X\n---\n", encoding="utf-8")
    (base / "top.md").write_text("---\ntype: X\n---\n", encoding="utf-8")
    (base / "folder.md").mkdir()
    return base


def test_resolve_concept_file_basic(bundle):
    path, canonical = resolve_concept_file(bundle, "tables/Orders")
    assert path == (bundle / "tables" / "Orders.md").resolve() and canonical == "tables/Orders"
    assert resolve_concept_file(bundle, "top")[1] == "top"


def test_resolve_concept_file_returns_canonical_case(bundle):
    path, canonical = resolve_concept_file(bundle, "Tables/orders")
    assert canonical == "tables/Orders" and path.name == "Orders.md"


def test_resolve_concept_file_ambiguous_case(bundle):
    (bundle / "Ab.md").write_text("x", encoding="utf-8")
    (bundle / "aB.md").write_text("x", encoding="utf-8")
    if len([n for n in os.listdir(bundle) if n.lower() == "ab.md"]) < 2:
        pytest.skip("system plików nie rozróżnia wielkości liter")
    assert code_of(resolve_concept_file, bundle, "Ab") == "ambiguous"
    assert code_of(resolve_concept_file, bundle, "AB") == "ambiguous"


@pytest.mark.parametrize("concept_id,code", [
    ("missing", "not_found"), ("tables/missing", "not_found"), ("nodir/x", "not_found"),
    ("folder", "not_file"), ("top/x", "not_found"), ("index", "invalid_id"), ("../x", "invalid_id"),
    ("a.md", "invalid_id"), ("", "invalid_id"), ("tables//x", "invalid_id"),
])
def test_resolve_concept_file_errors(bundle, concept_id, code):
    assert code_of(resolve_concept_file, bundle, concept_id) == code


def test_resolve_concept_file_rejects_linked_directory(bundle, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("---\ntype: X\n---\n", encoding="utf-8")
    make_link(bundle / "linked", outside)
    assert code_of(resolve_concept_file, bundle, "linked/secret") == "link"


def test_resolve_concept_file_rejects_linked_file(bundle, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntype: X\n---\n", encoding="utf-8")
    make_link(bundle / "alias.md", outside, directory=False)
    assert code_of(resolve_concept_file, bundle, "alias") == "link"


@pytest.mark.parametrize("segment", ['a*b', 'a?b', 'a|b', 'a<b', 'a>b', 'a"b', 'a\x00b', 'a\x7fb'])
def test_subpath_bad_chars_before_io(tmp_path, segment):
    assert code_of(locate_bundle_dir, str(tmp_path / "missing"), segment) == "invalid_subpath"


def test_root_itself_link_rejected(bundle, tmp_path):
    alias = tmp_path / "root-alias"
    make_link(alias, bundle)
    assert code_of(locate_bundle_dir, str(alias), "") == "link"
    assert code_of(resolve_concept_file, alias, "top") == "link"


def test_linked_root_ancestor_rejected(bundle, tmp_path):
    alias = tmp_path / "ancestor-alias"
    make_link(alias, bundle.parent)
    indirect = alias / bundle.name
    assert code_of(locate_bundle_dir, str(indirect), "") == "link"
    assert code_of(resolve_concept_file, indirect, "top") == "link"


def test_exact_collision_without_case_sensitive_fs(bundle, monkeypatch):
    import okf_engine.paths as paths

    class Entry:
        def __init__(self, name):
            self.name = name

    class Entries:
        def __enter__(self):
            return iter([Entry("top.md"), Entry("TOP.md")])

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(paths.os, "scandir", lambda directory: Entries())
    assert code_of(resolve_concept_file, bundle, "top") == "ambiguous"


def test_reparse_attribute_without_windows(bundle, monkeypatch):
    from types import SimpleNamespace

    real = Path.lstat

    def fake(path, *args, **kwargs):
        if path == bundle:
            return SimpleNamespace(st_mode=0o040755, st_file_attributes=0x400)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fake)
    assert code_of(resolve_concept_file, bundle, "top") == "link"
