"""Polityka dokumentacji: zgodność, wszystkie ścieżki zapisu i zmiana po zgodzie."""
import threading
import os
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from roots_registry import RootsRegistry
from server.file_changes import ChangeError, prepare_edit, prepare_exact_write, prepare_write, read_snapshot
from server.file_transaction import FileTransaction
from server.folder_tools import FolderError, _build_rename
from server.file_changes import SensitivePolicy
from server.tree_tools import prepare_tree, _executor
from okf_engine.write_policy import parse_policy, WritePolicyError

OKF = b"---\ntype: Reference\n---\nold\n"


@pytest.fixture
def env(tmp_path):
    base = tmp_path / "project"
    (base / "docs").mkdir(parents=True)
    store = ConfigStore({"roots": [{"alias": "work", "path": str(base.resolve()),
                                   "mode": "rw", "default": True}],
                         "knowledge": {"bundles": []}}, tmp_path / "config.json")
    registry = RootsRegistry(store)
    return SimpleNamespace(base=base, store=store, roots=registry,
                           tx=FileTransaction(registry, store.lock))


def policy(env, mode="require_okf", subpaths=None, exclusions=None):
    row = {"root_path": str(env.base.resolve()), "mode": mode,
           "subpaths": ["docs"] if subpaths is None else subpaths,
           "exclusions": ["docs/archive/"] if exclusions is None else exclusions}
    env.store.update(lambda config: config["knowledge"].update(project_write_policies=[row]))


def put(env, name="docs/note.md", raw=OKF):
    path = env.base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def test_old_project_without_conversion_remains_readable_and_writable(env):
    path = put(env, raw="zł stary markdown\r\n".encode("cp1250"))
    plan = prepare_edit(env.roots, [{"filepath": "docs/note.md", "old_text": "stary", "new_text": "nowy"}])
    assert env.tx.execute(plan)["success"]
    assert "nowy" in path.read_bytes().decode("cp1250")
    plan = prepare_write(env.roots, "docs/new.md", "# Plain Markdown")
    assert env.tx.execute(plan)["success"]
    assert env.store.snapshot()["knowledge"] == {"bundles": []}


@pytest.mark.parametrize("tool", ["edit", "overwrite", "exact"])
def test_existing_okf_cannot_be_downgraded_via_any_file_writer(env, tool):
    path = put(env)
    with pytest.raises(ChangeError, match="OKF_FORMAT_LOSS"):
        if tool == "edit":
            prepare_edit(env.roots, [{"filepath": "docs/note.md", "old_text": "---\ntype: Reference\n---\n", "new_text": ""}])
        elif tool == "overwrite":
            prepare_write(env.roots, "docs/note.md", "ordinary markdown", overwrite=True)
        else:
            prepare_exact_write(env.roots, "docs/note.md", b"ordinary markdown", before=read_snapshot(path))
    assert path.read_bytes() == OKF


def test_compatible_project_keeps_valid_okf_updates_and_plain_markdown(env):
    path = put(env)
    plan = prepare_edit(env.roots, [{"filepath": "docs/note.md", "old_text": "old", "new_text": "new"}])
    assert env.tx.execute(plan)["success"] and b"type: Reference" in path.read_bytes()
    assert prepare_write(env.roots, "docs/plain.md", "# Markdown").files


@pytest.mark.parametrize("content", ["# Plain", "---\ntype: []\n---\nbody", "---\ntype: [\n---\nbody"])
def test_strict_rejects_plain_markdown_and_invalid_yaml_before_writing(env, content):
    policy(env)
    with pytest.raises(ChangeError, match="OKF_REQUIRED"):
        prepare_write(env.roots, "docs/new.md", content)
    assert not (env.base / "docs/new.md").exists()


def test_strict_does_not_block_reads_or_noop_of_old_markdown(env):
    path = put(env, raw=b"old markdown\n")
    policy(env)
    snapshot = read_snapshot(path)
    assert snapshot.text == "old markdown\n"
    plan = prepare_write(env.roots, "docs/note.md", snapshot.text, overwrite=True)
    assert not plan.files[0].changed
    assert env.tx.execute(plan)["success"]


def test_strict_accepts_valid_okf_and_allows_conversion_of_old_markdown(env):
    path = put(env, raw=b"old markdown\n")
    policy(env)
    plan = prepare_exact_write(env.roots, "docs/note.md", OKF, before=read_snapshot(path))
    assert env.tx.execute(plan)["success"] and path.read_bytes() == OKF
    plan = prepare_write(env.roots, "docs/new.md", OKF.decode())
    assert env.tx.execute(plan)["success"]


def test_exceptions_allow_plain_files_but_do_not_allow_existing_okf_loss(env):
    policy(env, exclusions=["docs/specs/OKF/", "docs/example.md"])
    put(env, "docs/specs/OKF/README.md", b"plain")
    for name in ("docs/AGENTS.md", "docs/index.md", "docs/log.md", "docs/example.md", "docs/specs/OKF/new.md"):
        assert prepare_write(env.roots, name, "plain").files
    path = put(env, "docs/specs/OKF/note.md")
    with pytest.raises(ChangeError, match="OKF_FORMAT_LOSS"):
        prepare_write(env.roots, "docs/specs/OKF/note.md", "plain", overwrite=True)
    assert path.read_bytes() == OKF
    with pytest.raises(ChangeError, match="OKF_REQUIRED"):
        prepare_write(env.roots, "docs/example.md.other.md", "plain")


def test_explicit_subpath_does_not_enforce_all_docs_or_skill_formats(env):
    (env.base / "docs/active").mkdir()
    (env.base / "skills").mkdir()
    policy(env, subpaths=["docs/active"])
    assert prepare_write(env.roots, "docs/plain.md", "plain").files
    assert prepare_write(env.roots, "skills/SKILL.md", "---\nname: skill\n---\nbody").files
    with pytest.raises(ChangeError, match="OKF_REQUIRED"):
        prepare_write(env.roots, "docs/active/plain.md", "plain")


def test_disabled_custom_project_bundle_still_preserves_existing_okf(env):
    put(env, "doc/note.md")
    row = {"id": "project", "label": "Project", "scope": "project", "profile": "docs",
           "root": "work", "root_path": str(env.base.resolve()), "subpath": "doc", "enabled": False}
    env.store.update(lambda config: config["knowledge"]["bundles"].append(row))
    with pytest.raises(ChangeError, match="OKF_FORMAT_LOSS"):
        prepare_write(env.roots, "doc/note.md", "plain", overwrite=True)


def test_policy_change_while_waiting_prevents_transaction_and_leaves_no_staging(env):
    plan = prepare_write(env.roots, "docs/new.md", "plain")
    policy(env)
    assert env.tx.verify(plan) is not None
    result = env.tx.execute(plan)
    assert not result["data"]["committed"]
    assert not (env.base / "docs/new.md").exists()
    assert not list(env.base.rglob(".sklr-*.tmp"))


def test_direct_prepared_changes_cannot_bypass_execution_policy(env):
    # Stary plan z trybu zgodności jest też modelem bezpośredniego PreparedChanges.
    path = put(env, raw=b"old markdown")
    plan = prepare_write(env.roots, "docs/note.md", "changed markdown", overwrite=True)
    policy(env)
    assert not env.tx.execute(plan)["success"] and path.read_bytes() == b"old markdown"


def test_nested_root_cannot_bypass_parent_policy(env):
    policy(env)
    row = {"alias": "nested", "path": str(env.base / "docs"), "mode": "rw"}
    env.store.update(lambda config: config["roots"].append(row))
    with pytest.raises(ChangeError, match="OKF_REQUIRED"):
        prepare_write(env.roots, "note.md", "plain", root="nested")


def test_link_alias_and_physical_path_both_obey_document_policy(env):
    target = put(env, "docs/note.md")
    try:
        os.symlink(target, env.base / "alias.txt")
        os.symlink(env.base / "outside.txt", env.base / "docs/alias.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable")
    with pytest.raises(ChangeError, match="OKF_FORMAT_LOSS"):
        prepare_write(env.roots, "alias.txt", "plain", overwrite=True)
    policy(env)
    with pytest.raises(ChangeError, match="OKF_REQUIRED"):
        prepare_write(env.roots, "docs/alias.md", "plain")


def test_tree_creation_checked_at_preparation_and_after_policy_change(env):
    files = [{"filepath": "docs/new/note.md", "content": "plain"}]
    plan = prepare_tree(env.roots, [], files)
    policy(env)
    with pytest.raises(ChangeError, match="OKF_REQUIRED"):
        prepare_tree(env.roots, [], files)
    result = _executor(env.roots, env.store.lock, ([], files, None, None), plan)(threading.Event())
    assert result.get("started") is False and not (env.base / "docs/new").exists()
    valid = [{"filepath": "docs/new/note.md", "content": OKF.decode()}]
    plan = prepare_tree(env.roots, [], valid)
    result = _executor(env.roots, env.store.lock, ([], valid, None, None), plan)(threading.Event())
    assert result["exit_code"] == 0 and (env.base / "docs/new/note.md").read_bytes() == OKF


def test_rename_cannot_move_unconverted_directory_into_strict_scope(env):
    put(env, "staging/note.md", b"plain")
    (env.base / "docs").rmdir()
    policy(env)
    with pytest.raises(FolderError, match="OKF_REQUIRED"):
        _build_rename(env.roots, "staging", "docs", None, SensitivePolicy())
    assert (env.base / "staging/note.md").is_file()


def test_rollback_can_restore_old_markdown_after_conversion_under_strict_policy(env, monkeypatch):
    first = put(env, "docs/first.md", b"old first")
    second = put(env, "docs/second.md", b"old second")
    policy(env)
    a = prepare_write(env.roots, "docs/first.md", OKF.decode(), overwrite=True)
    b = prepare_write(env.roots, "docs/second.md", OKF.decode(), overwrite=True)
    from server.file_changes import PreparedChanges
    plan = PreparedChanges(a.files + b.files)
    publish = env.tx._publish
    calls = []

    def fail_second(staged):
        calls.append(True)
        if len(calls) == 2:
            raise OSError("failure")
        publish(staged)

    monkeypatch.setattr(env.tx, "_publish", fail_second)
    result = env.tx.execute(plan)
    assert not result["data"]["committed"] and result["data"]["rollback_complete"]
    assert first.read_bytes() == b"old first" and second.read_bytes() == b"old second"


@pytest.mark.parametrize("subpaths", [[], ["../docs"], ["/docs"], ["docs/*"], ["docs\\sub"], [False]])
def test_policy_rejects_ambiguous_or_unsafe_scope(env, subpaths):
    with pytest.raises(WritePolicyError):
        parse_policy({"root_path": str(env.base.resolve()), "mode": "require_okf", "subpaths": subpaths})
