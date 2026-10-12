"""5b A1: przygotowanie zmian bez zapisów, publiczny podgląd bez sekretów."""
import codecs
import hashlib
import json
import os
from pathlib import Path

import pytest

from config_store import ConfigStore
from roots import MODE_RO, MODE_RW, Zones
from roots_registry import RootsRegistry
from server import file_changes as fc
from server.file_changes import ChangeError, SensitivePolicy, prepare_edit, prepare_write
from state import load_config


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.json"
    store = ConfigStore(load_config(config_path), config_path)
    central = tmp_path / "central"
    central.mkdir()
    registry = RootsRegistry(store, zones=Zones(), central_dir=central)
    app, other = tmp_path / "app", tmp_path / "other"
    app.mkdir()
    other.mkdir()
    registry.add(app, MODE_RW, alias="app")
    registry.add(other, MODE_RW, alias="other")
    return registry, app, other


def put(base, name, content):
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


def change(old="old", new="new", filepath="a.txt", **extra):
    return dict(filepath=filepath, old_text=old, new_text=new, **extra)


def test_prepare_is_read_only_and_hashes_raw_bytes(env):
    registry, app, _ = env
    path = put(app, "a.txt", b"old\r\n")
    names_before = set(app.iterdir())
    plan = prepare_edit(registry, [change()])
    item = plan.files[0]
    assert path.read_bytes() == b"old\r\n" and set(app.iterdir()) == names_before
    assert item.after == b"new\r\n"
    assert item.before.sha256 == hashlib.sha256(b"old\r\n").hexdigest()
    assert item.after_hash == hashlib.sha256(item.after).hexdigest()
    public = plan.preview()["files"][0]
    assert public["before_hash"] == item.before.sha256
    assert public["after_hash"] == item.after_hash
    assert public["root"] == "app" and public["filepath"] == "a.txt"
    assert "-old" in public["diff"] and "+new" in public["diff"]


@pytest.mark.parametrize("text,old,count", [("old old", "old", 2), ("aaaa", "aa", 3)])
def test_multiple_matches_including_overlapping_rejected(env, text, old, count):
    registry, app, _ = env
    path = put(app, "a.txt", text)
    with pytest.raises(ChangeError, match=f"ma {count} dopasowań"):
        prepare_edit(registry, [change(old)])
    assert path.read_text(encoding="utf-8") == text


def test_zero_match_hint_explains_whitespace_and_eol(env):
    registry, app, _ = env
    put(app, "a.txt", "unrelated\r\n\tdef f():\r\n    return 1\r\n")
    with pytest.raises(ChangeError) as error:
        prepare_edit(registry, [change("  def f():\n")])
    message = str(error.value)
    assert "ma 0 dopasowań" in message and "Najbliższy fragment" in message
    assert "<TAB>" in message and "<CR><LF>" in message


def test_old_text_not_normalized(env):
    registry, app, _ = env
    put(app, "a.txt", "old\r\n")
    with pytest.raises(ChangeError, match="ma 0 dopasowań"):
        prepare_edit(registry, [change("old\n")])


def test_sequential_changes_same_file_and_no_partial_write(env):
    registry, app, _ = env
    path = put(app, "a.txt", "old")
    plan = prepare_edit(registry, [change(), change("new", "final")])
    assert len(plan.files) == 1 and plan.files[0].after == b"final"
    with pytest.raises(ChangeError, match="Zmiana 2"):
        prepare_edit(registry, [change(), change("absent", "x")])
    assert path.read_bytes() == b"old"


def test_multi_root_and_pinned_binding(env):
    registry, app, other = env
    put(app, "a.txt", "old")
    put(other, "a.txt", "old")
    plan = prepare_edit(registry, [change(), change(root="other")], root="app")
    assert [f.bindings[0].root.alias for f in plan.files] == ["app", "other"]
    registry.set_default("other")
    assert plan.files[0].bindings[0].root.alias == "app"
    assert plan.files[0].path == (app / "a.txt").resolve()


def test_different_spellings_group_same_target(env):
    registry, app, _ = env
    put(app, "a.txt", "old")
    plan = prepare_edit(registry, [change(), change("new", "final", filepath="./a.txt")])
    assert len(plan.files) == 1 and len(plan.files[0].bindings) == 2
    assert plan.files[0].after == b"final"


def test_nested_ro_rejected(env):
    registry, app, _ = env
    put(app, "inner/a.txt", "old")
    registry.add(app / "inner", MODE_RO, alias="inner")
    with pytest.raises(ValueError, match="tylko do odczytu"):
        prepare_edit(registry, [change(filepath="inner/a.txt")])


@pytest.mark.parametrize("raw,old,new,expected,encoding,bom,adjusted", [
    (b"old\nlast", "old\n", "new\r\n", b"new\nlast", "utf-8", False, True),
    (b"old\r\nlast", "old\r\n", "new\n", b"new\r\nlast", "utf-8", False, True),
    (b"old\rlast", "old\r", "new\n", b"new\rlast", "utf-8", False, True),
    (codecs.BOM_UTF8 + b"old\n", "old", "new", codecs.BOM_UTF8 + b"new\n", "utf-8", True, False),
    ("zażółć\r\n".encode("cp1250"), "zażółć", "gęślą", "gęślą\r\n".encode("cp1250"), "cp1250", False, False),
    (b"old\r\nx\n", "old", "new\n", b"new\n\r\nx\n", "utf-8", False, False),
    (b"old", "old", "new\r\n", b"new\r\n", "utf-8", False, False),
])
def test_format_preservation(env, raw, old, new, expected, encoding, bom, adjusted):
    registry, app, _ = env
    put(app, "a.txt", raw)
    item = prepare_edit(registry, [change(old, new)]).files[0]
    assert item.after == expected and item.encoding == encoding
    assert bool(item.bom) == bom and item.eol_adjusted == adjusted


def test_cp1250_does_not_silently_convert_to_utf8(env):
    registry, app, _ = env
    path = put(app, "a.txt", "żółć".encode("cp1250"))
    with pytest.raises(ChangeError, match="dotychczasowym kodowaniu"):
        prepare_edit(registry, [change("żółć", "😀")])
    assert path.read_bytes() == "żółć".encode("cp1250")


@pytest.mark.parametrize("raw", [b"x\x00y", b"\xff\xfex\x00", codecs.BOM_UTF8 + b"\xff"])
def test_unsupported_input_rejected(env, raw):
    registry, app, _ = env
    put(app, "a.txt", raw)
    with pytest.raises(ChangeError):
        prepare_edit(registry, [change()])


@pytest.mark.parametrize("filename", ["a.pfx", "a.P12"])
def test_blocked_secrets_even_for_new_files(env, filename):
    registry, _, _ = env
    with pytest.raises(ChangeError, match="zablokowane"):
        prepare_write(registry, filename, "text")


def test_secret_preview_and_reprs_never_include_values_or_hashes(env):
    registry, app, _ = env
    put(app, ".env", "TOKEN=sentinel-old\nNAME=app\n")
    plan = prepare_edit(registry, [change("sentinel-old", "sentinel-new", filepath=".env")])
    public = plan.preview()
    entry = public["files"][0]
    encoded = json.dumps(public)
    for value in ("sentinel-old", "sentinel-new", plan.files[0].before.sha256, plan.files[0].after_hash):
        assert value not in encoded
        assert value not in repr(plan) + repr(plan.files[0]) + repr(plan.files[0].before)
    assert entry["hidden_change"] and entry["masked"] and plan.sensitive
    assert "ukrytą treść" in entry["diff"] and "before_hash" not in entry


def test_secret_hint_masks_full_text_before_window(env):
    registry, app, _ = env
    put(app, "a.pem", "-----BEGIN PRIVATE KEY-----\nSENTINEL1\nSENTINEL2\n-----END PRIVATE KEY-----\n")
    with pytest.raises(ChangeError) as error:
        prepare_edit(registry, [change("SENTINEL-MISSING", filepath="a.pem")])
    assert "SENTINEL" not in str(error.value)
    assert "ZAMASKOWANO" in str(error.value)
    assert "-----END PRIVATE KEY-----" in str(error.value)


@pytest.mark.parametrize("long_line", [False, True])
def test_secret_hint_respects_line_and_character_limits(env, long_line):
    registry, app, _ = env
    normal = "NORMAL=" + "x" * 3000 + "\n" if long_line else "".join(
        f"NORMAL_{index}=ok\n" for index in range(30))
    put(app, ".env", "TOKEN=SENTINEL-VALUE\n" + normal)
    with pytest.raises(ChangeError) as error:
        prepare_edit(registry, [change("missing", filepath=".env")])
    message = str(error.value)
    fragment = message.split("od linii 1:\n", 1)[1]
    assert "SENTINEL" not in message and "TOKEN=***" in fragment
    assert len(fragment) <= fc.HINT_CHARS
    assert len(fragment.splitlines()) <= fc.HINT_LINES
    if long_line:
        assert len(fragment) == fc.HINT_CHARS
    else:
        assert len(fragment.splitlines()) == fc.HINT_LINES
        assert "NORMAL_18=ok" in fragment and "NORMAL_19=ok" not in fragment


def test_secret_visible_diff_masks_both_states(env):
    registry, app, _ = env
    put(app, ".env", "TOKEN=sentinel\nNAME=old\n")
    public = prepare_edit(registry, [change(filepath=".env")]).preview()["files"][0]
    assert "sentinel" not in public["diff"] and "***" in public["diff"]
    assert "-NAME=old" in public["diff"] and "+NAME=new" in public["diff"]


@pytest.mark.parametrize("filename,sensitive", [
    ("panel/a.txt", True), ("nested/panel/a.txt", False), ("main.py", True),
    ("nested/main.py", False), ("tasks.db-wal", True), (".git/a", True),
    ("server/a.py", False), ("tasks/a.py", False), (".env", True),
    (".env.example", False),
])
def test_general_p10_is_anchored_not_server_extension_in_every_repo(env, filename, sensitive):
    registry, app, _ = env
    put(app, filename, "old")
    plan = prepare_edit(registry, [change(filepath=filename)])
    assert plan.sensitive is sensitive


def test_server_protection_with_parent_and_subdirectory_roots(env):
    registry, app, _ = env
    put(app, "repo/server/a.py", "old")
    policy = SensitivePolicy(server_dir=app / "repo")
    assert prepare_edit(registry, [change(filepath="repo/server/a.py")], policy=policy).sensitive
    registry.add(app / "repo" / "server", MODE_RW, alias="subserver")
    plan = prepare_edit(registry, [change(filepath="a.py", root="subserver")], policy=policy)
    assert "repo działającego serwera" in plan.files[0].sensitive_reasons


def test_configurable_patterns(env):
    registry, app, _ = env
    put(app, "custom/a.txt", "old")
    policy = SensitivePolicy(patterns=["custom/"], server_patterns=[])
    assert prepare_edit(registry, [change(filepath="custom/a.txt")], policy=policy).sensitive


@pytest.mark.parametrize("patterns", [["../x"], ["/x"], ["C:/x"], ["a//b"], [""], [1], "main.py"])
def test_invalid_patterns_rejected(patterns):
    with pytest.raises(ChangeError):
        SensitivePolicy(patterns=patterns)


def test_create_empty_new_and_overwrite_format(env):
    registry, app, _ = env
    new = prepare_write(registry, "new.txt", "")
    assert new.files[0].before is None and new.files[0].after == b""
    assert not (app / "new.txt").exists() and new.preview()["files"][0]["created"]
    put(app, "a.txt", codecs.BOM_UTF8 + b"old\r\n")
    with pytest.raises(ChangeError, match="już istnieje"):
        prepare_write(registry, "a.txt", "new")
    item = prepare_write(registry, "a.txt", "new\n", overwrite=True).files[0]
    assert item.after == codecs.BOM_UTF8 + b"new\r\n"
    with pytest.raises(ChangeError, match="nadrzędny"):
        prepare_write(registry, "missing/new.txt", "new")
    with pytest.raises(ChangeError):
        prepare_write(registry, ".", "new", overwrite=True)


@pytest.mark.parametrize("changes", [None, {}, [], [change()] * 51, [None],
    [{"filepath": "a.txt"}], [change(extra=True)], [change(old="")],
    [change(old=False)], [change(new=1)], [change(new="x" + chr(0))],
    [change(new=chr(0xD800))]])
def test_invalid_changes_rejected(env, changes):
    registry, app, _ = env
    put(app, "a.txt", "old")
    with pytest.raises((ChangeError, ValueError)):
        prepare_edit(registry, changes)


@pytest.mark.parametrize("overwrite", [1, "false", None])
def test_overwrite_must_be_bool(env, overwrite):
    registry, _, _ = env
    with pytest.raises(ChangeError, match="bool"):
        prepare_write(registry, "new.txt", "new", overwrite=overwrite)


def test_file_limit(env, monkeypatch):
    registry, app, _ = env
    monkeypatch.setattr(fc, "MAX_FILE_BYTES", 8)
    put(app, "a.txt", b"old" + b"x" * 6)
    with pytest.raises(ChangeError, match="5 MiB"):
        prepare_edit(registry, [change()])
    put(app, "a.txt", "old")
    with pytest.raises(ChangeError, match="5 MiB"):
        prepare_edit(registry, [change(new="x" * 9)])


def test_file_count_limit(env):
    registry, app, _ = env
    for index in range(11):
        put(app, f"{index}.txt", "old")
    with pytest.raises(ChangeError, match="10 różnych"):
        prepare_edit(registry, [change(filepath=f"{i}.txt") for i in range(11)])


def test_total_before_after_and_request_limits(env, monkeypatch):
    registry, app, _ = env
    monkeypatch.setattr(fc, "MAX_TOTAL_BYTES", 20)
    put(app, "a.txt", "old" + "x" * 8)
    put(app, "b.txt", "old" + "x" * 8)
    with pytest.raises(ChangeError, match="Stan początkowy"):
        prepare_edit(registry, [change(), change(filepath="b.txt")])
    put(app, "a.txt", "old" + "x" * 7)
    put(app, "b.txt", "old" + "x" * 7)
    with pytest.raises(ChangeError, match="Wynik przekracza łącznie"):
        prepare_edit(registry, [change(new="new!"), change(filepath="b.txt")])
    with pytest.raises(ChangeError, match="żądania"):
        prepare_edit(registry, [change(new="x" * 18)])


def test_diff_shared_utf8_budget(env, monkeypatch):
    registry, app, other = env
    put(app, "a.txt", "old")
    put(other, "a.txt", "old")
    monkeypatch.setattr(fc, "DIFF_LIMIT_BYTES", 90)
    public = prepare_edit(registry, [change(new="żółć"), change(root="other")]).preview()
    assert public["diff_bytes"] <= 90 and public["truncated"]
    assert sum(len(f["diff"].encode("utf-8")) for f in public["files"]) <= 90


def test_expensive_diff_has_explicit_partial_preview(env, monkeypatch):
    registry, app, _ = env
    put(app, "a.txt", "old\nx\ny\n")
    monkeypatch.setattr(fc, "DIFF_MAX_PRODUCT", 1)
    entry = prepare_edit(registry, [change()]).preview()["files"][0]
    assert entry["truncated"] and "Diff skrócony" in entry["diff"]


def test_error_io_does_not_echo_raw_exception(env, monkeypatch):
    registry, app, _ = env
    put(app, "a.txt", "old")
    def fail(*args, **kwargs):
        raise OSError("SENTINEL-IN-EXCEPTION")
    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(ChangeError) as error:
        prepare_edit(registry, [change()])
    assert "SENTINEL" not in str(error.value)


def test_symlink_target_grouping_keeps_unresolved_binding(env):
    registry, app, _ = env
    target = put(app, "a.txt", "old")
    try:
        os.symlink(target, app / "alias.txt")
    except (OSError, NotImplementedError):
        pytest.skip("brak uprawnień do symlinków")
    plan = prepare_edit(registry, [change(filepath="alias.txt"), change("new", "final")])
    assert len(plan.files) == 1 and plan.files[0].after == b"final"
    assert plan.files[0].bindings[0].filepath == "alias.txt"
    assert plan.files[0].path == target.resolve()
