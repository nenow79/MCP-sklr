"""Root-relative directory selection: no actual GUI."""
import pytest

from panel.directory_selection import relative_directory


def test_relative_directory(tmp_path):
    base = tmp_path / "project"
    target = base / "docs" / "zażółć folder"
    target.mkdir(parents=True)
    assert relative_directory(str(base), str(target)) == "docs/zażółć folder"
    assert relative_directory(str(base), str(base)) == ""
    assert relative_directory(str(base), None) is None


def test_outside_sibling_and_missing_rejected(tmp_path):
    base = tmp_path / "project"
    base.mkdir()
    sibling = tmp_path / "project-other"
    sibling.mkdir()
    file = base / "file"
    file.write_text("x")
    for target in [tmp_path, sibling, base / "missing", file]:
        with pytest.raises(ValueError, match="wewnątrz"):
            relative_directory(str(base), str(target))


def test_relative_input_rejected(tmp_path):
    with pytest.raises(ValueError):
        relative_directory(str(tmp_path), "relative")


def test_link_rejected(tmp_path):
    base, outside = tmp_path / "project", tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    link = base / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    with pytest.raises(ValueError):
        relative_directory(str(base), str(link))
