"""C2b: świeży odczyt na sztucznych plikach, bez serwera i produkcyjnej wiedzy."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from okf_engine.bundles import evaluate_bundles
from okf_engine.documents import DocumentError, DocumentReader
from okf_engine.limits import KnowledgeLimits
from okf_engine.revisions import RevisionSigner
from roots import Root


@pytest.fixture
def reader_env(tmp_path):
    root = Root("test", str(tmp_path.resolve()), "ro")
    config = {"bundles": [{"id": "general", "label": "Test", "scope": "central",
              "root": root.alias, "root_path": root.path, "subpath": "", "enabled": True}]}
    controls = {"roots": [root], "allowed": True}

    def bundle(bundle_id):
        return evaluate_bundles(config, controls["roots"])[0]

    def authorize(state, path):
        return controls["allowed"]

    reader = DocumentReader(bundle, authorize, RevisionSigner(b"a" * 32), KnowledgeLimits(),
                            lambda: datetime(2026, 6, 1, tzinfo=timezone.utc))
    return tmp_path, config, controls, reader


def write(path, body="# Title\ntext\n", metadata="type: Reference\n"):
    path.write_bytes(("---\n" + metadata + "---\n" + body).encode())


def error(reader, concept, expected):
    with pytest.raises(DocumentError) as caught:
        reader.read("general", concept)
    assert caught.value.code == expected


def test_read_bytes_does_not_parse_and_build_matches_read(reader_env, monkeypatch):
    from okf_engine import documents as module
    base, _, _, reader = reader_env
    write(base / 'a.md', '# Title\npassword: RAW_SENTINEL\n')
    parser = module.parse_document

    def forbidden(*args, **kwargs):
        raise AssertionError('parser called during byte read')

    monkeypatch.setattr(module, 'parse_document', forbidden)
    source = reader.read_bytes('general', 'a')
    assert b'RAW_SENTINEL' in source.raw
    assert 'RAW_SENTINEL' not in repr(source)
    monkeypatch.setattr(module, 'parse_document', parser)
    built = reader.build_document(source)
    ordinary = reader.read('general', 'a')
    assert built.view == ordinary.view
    assert built.revision == ordinary.revision
    assert 'RAW_SENTINEL' not in built.view.body


def test_build_rejects_changed_byte_snapshot(reader_env):
    base, _, _, reader = reader_env
    write(base / 'a.md')
    source = reader.read_bytes('general', 'a')
    write(base / 'a.md', '# Changed\nlonger content\n')
    with pytest.raises(DocumentError, match='DOCUMENT_CHANGED'):
        reader.build_document(source)


def test_build_rechecks_access(reader_env):
    base, _, controls, reader = reader_env
    write(base / 'a.md')
    source = reader.read_bytes('general', 'a')
    controls['allowed'] = False
    with pytest.raises(DocumentError, match='DOCUMENT_UNAVAILABLE'):
        reader.build_document(source)


def test_reader_measurements_use_only_fixed_keys(reader_env, monkeypatch):
    import okf_engine.documents as module
    base, _, controls, reader = reader_env
    write(base / 'a.md', 'password: PRIVATE_SENTINEL\n')
    ticks = iter(range(0, 1000000000, 2000000))
    monkeypatch.setattr(module.time, 'perf_counter_ns', lambda: next(ticks))
    reader.read('general', 'a')
    metrics = reader.measurements()
    assert set(metrics) == {name + suffix for name in ('bundle_check', 'path_resolve', 'authorize')
                            for suffix in ('_calls', '_ns')}
    for name in ('bundle_check', 'path_resolve', 'authorize'):
        assert metrics[name + '_calls'] > 0
        assert metrics[name + '_ns'] == metrics[name + '_calls'] * 2000000
    controls['allowed'] = False
    before = metrics['authorize_calls']
    with pytest.raises(DocumentError):
        reader.read('general', 'a')
    assert reader.measurements()['authorize_calls'] == before + 1
    assert 'PRIVATE_SENTINEL' not in repr(reader.measurements())
    assert str(base) not in repr(reader.measurements())


def test_fresh_read_public_only_and_revision(reader_env):
    base, _, _, reader = reader_env
    write(base / "Orders.md", "# A\npassword: hiddenvalue\n## B\ntext\n")
    first = reader.read("general", "orders")
    assert first.view.identity.concept_id == "Orders"
    assert first.view.masked and "hiddenvalue" not in first.view.body
    assert first.outline.entries[1].start_line == 6
    assert first.outline.get_section("s1").content == first.view.body
    assert reader.read("general", "Orders").revision == first.revision
    write(base / "Orders.md", "# New\nchanged\n")
    second = reader.read("general", "Orders")
    assert second.revision != first.revision and second.view.body == "# New\nchanged\n"
    (base / "Orders.md").unlink()
    error(reader, "Orders", "DOCUMENT_UNAVAILABLE")


@pytest.mark.parametrize("kind", ["disabled", "removed_root", "denied"])
def test_access_changes(reader_env, kind):
    base, config, controls, reader = reader_env
    write(base / "a.md")
    reader.read("general", "a")
    if kind == "disabled":
        config["bundles"][0]["enabled"] = False
    elif kind == "removed_root":
        controls["roots"] = []
    else:
        controls["allowed"] = False
    error(reader, "a", "DOCUMENT_UNAVAILABLE" if kind == "denied" else "BUNDLE_UNAVAILABLE")


def test_access_lost_during_read(reader_env):
    base, _, controls, reader = reader_env
    write(base / "a.md")
    calls = []

    def authorize(state, path):
        calls.append(path)
        return len(calls) == 1

    reader.authorize = authorize
    error(reader, "a", "DOCUMENT_UNAVAILABLE")
    assert len(calls) == 2


def test_callback_failure_is_safe(reader_env):
    base, _, _, reader = reader_env
    write(base / "a.md")

    def denied(state, path):
        raise RuntimeError("do not expose sensitive path")

    reader.authorize = denied
    error(reader, "a", "DOCUMENT_UNAVAILABLE")


@pytest.mark.parametrize("concept", ["../a", "a.md", "index", "missing"])
def test_bad_or_missing_concept(reader_env, concept):
    error(reader_env[3], concept, "DOCUMENT_UNAVAILABLE")


def test_parser_errors_and_size(reader_env):
    base, _, _, reader = reader_env
    (base / "a.md").write_bytes(b"not OKF")
    error(reader, "a", "INVALID_OKF")
    reader.limits = replace(reader.limits, max_document_bytes=1024, max_frontmatter_bytes=256)
    write(base / "a.md", "x" * 1025)
    error(reader, "a", "SAFETY_LIMIT")


def test_root_binding_changed(reader_env, tmp_path):
    base, _, controls, reader = reader_env
    write(base / "a.md")
    old = reader.current_bundle("general")
    moved = tmp_path / "other"
    moved.mkdir()
    controls["roots"] = [Root("test", str(moved.resolve()), "ro")]
    with pytest.raises(DocumentError, match="BUNDLE_UNAVAILABLE"):
        reader.check_access(old, base / "a.md")


def test_revision_key_rotation(reader_env):
    base, _, _, reader = reader_env
    write(base / "a.md")
    first = reader.read("general", "a")
    reader.signer = RevisionSigner(b"b" * 32)
    assert reader.read("general", "a").revision != first.revision


def test_public_metadata_and_safe_repr(reader_env):
    base, _, _, reader = reader_env
    write(base / "a.md", "password: bodysecret\n", "type: Reference\napi_key: metasecret\n")
    doc = reader.read("general", "a")
    assert doc.view.metadata["api_key"] == "***"
    assert "bodysecret" not in doc.view.body
    assert repr(doc) == "PublicDocument()"
    assert doc.revision not in repr(doc) and str(base) not in repr(doc)


@pytest.mark.parametrize("kind", ["deleted", "disabled", "removed_root", "denied", "changed_binding"])
def test_cached_document_access_rechecked(reader_env, kind):
    base, config, controls, reader = reader_env
    write(base / "a.md")
    doc = reader.read("general", "a")
    reader.check_document(doc)
    if kind == "deleted":
        (base / "a.md").unlink()
    elif kind == "disabled":
        config["bundles"][0]["enabled"] = False
    elif kind == "removed_root":
        controls["roots"] = []
    elif kind == "changed_binding":
        controls["roots"] = [Root("new", str(base.resolve()), "ro")]
        config["bundles"][0]["root"] = "new"
    else:
        controls["allowed"] = False
    with pytest.raises(DocumentError):
        reader.check_document(doc)


def test_cached_document_is_not_a_freshness_check(reader_env):
    base, _, _, reader = reader_env
    write(base / "a.md")
    doc = reader.read("general", "a")
    write(base / "a.md", "changed body\n")
    reader.check_document(doc)  # search nadal może używać poprzedniego skanu
    assert reader.read("general", "a").revision != doc.revision


def test_change_during_public_view_is_rejected(reader_env):
    base, _, _, reader = reader_env
    write(base / "a.md")
    def clock():
        write(base / "a.md", "changed and longer content\n")
        return datetime(2026, 6, 1, tzinfo=timezone.utc)
    reader.now = clock
    error(reader, "a", "DOCUMENT_CHANGED")


def test_deleted_during_public_view_is_rejected(reader_env):
    base, _, _, reader = reader_env
    write(base / "a.md")
    def clock():
        (base / "a.md").unlink()
        return datetime(2026, 6, 1, tzinfo=timezone.utc)
    reader.now = clock
    error(reader, "a", "DOCUMENT_UNAVAILABLE")


def test_collision_during_public_view_is_rejected(reader_env, monkeypatch):
    import okf_engine.documents as documents
    from okf_engine.paths import PathSecurityError
    base, _, _, reader = reader_env
    write(base / "a.md")
    def collision(*args):
        raise PathSecurityError("ambiguous")
    def clock():
        monkeypatch.setattr(documents, "resolve_concept_file", collision)
        return datetime(2026, 6, 1, tzinfo=timezone.utc)
    reader.now = clock
    error(reader, "a", "DOCUMENT_UNAVAILABLE")


def test_reparse_during_public_view_is_rejected(reader_env, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    base, _, _, reader = reader_env
    write(base / "a.md")
    real = Path.lstat
    def fake(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        if path == base / "a.md":
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    def clock():
        monkeypatch.setattr(Path, "lstat", fake)
        return datetime(2026, 6, 1, tzinfo=timezone.utc)
    reader.now = clock
    error(reader, "a", "DOCUMENT_UNAVAILABLE")


@pytest.mark.parametrize("data,code", [
    (b"\xff", "INVALID_OKF"),
    (b"---\ntype: !!python/object:x {}\n---\n", "UNSUPPORTED_PROFILE"),
])
def test_encoding_and_unsupported_yaml(reader_env, data, code):
    base, _, _, reader = reader_env
    (base / "a.md").write_bytes(data)
    error(reader, "a", code)


def test_short_read_is_rejected(reader_env, monkeypatch):
    from pathlib import Path
    base, _, _, reader = reader_env
    write(base / "a.md")
    real = Path.open
    class ShortStream:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def fileno(self):
            return self.stream.fileno()
        def read(self, size):
            return self.stream.read(size)[:-1]
    def short(path, *args, **kwargs):
        stream = real(path, *args, **kwargs)
        return ShortStream(stream) if path == base / "a.md" and args == ("rb",) else stream
    monkeypatch.setattr(Path, "open", short)
    error(reader, "a", "DOCUMENT_CHANGED")


@pytest.mark.parametrize("windows", [True, False])
def test_snapshot_ctime_platform_policy(monkeypatch, windows):
    from types import SimpleNamespace
    import okf_engine.documents as documents
    monkeypatch.setattr(documents, "_WINDOWS", windows)
    first = SimpleNamespace(st_dev=1, st_ino=2, st_size=14, st_mtime_ns=100, st_ctime_ns=200)
    second = SimpleNamespace(**{**vars(first), "st_ctime_ns": 300})
    assert (documents._snapshot(first) == documents._snapshot(second)) is windows


@pytest.mark.parametrize("windows", [True, False])
@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_size", "st_mtime_ns"])
def test_snapshot_detects_identity_size_and_mtime_changes(monkeypatch, windows, field):
    from types import SimpleNamespace
    import okf_engine.documents as documents
    monkeypatch.setattr(documents, "_WINDOWS", windows)
    first = SimpleNamespace(st_dev=1, st_ino=2, st_size=14, st_mtime_ns=100, st_ctime_ns=200)
    second = SimpleNamespace(**{**vars(first), field: getattr(first, field) + 1})
    assert documents._snapshot(first) != documents._snapshot(second)


def test_windows_read_tolerates_fstat_ctime_difference(reader_env, monkeypatch):
    from types import SimpleNamespace
    import okf_engine.documents as documents
    base, _, _, reader = reader_env
    write(base / "a.md")
    monkeypatch.setattr(documents, "_WINDOWS", True)
    real = documents.os.fstat
    def different_ctime(fd):
        info = real(fd)
        fields = ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        values = {name: getattr(info, name) for name in fields}
        values["st_ctime_ns"] += 1000000
        values["st_file_attributes"] = getattr(info, "st_file_attributes", 0)
        return SimpleNamespace(**values)
    monkeypatch.setattr(documents.os, "fstat", different_ctime)
    doc = reader.read("general", "a")
    assert doc.view.body == "# Title\ntext\n"
