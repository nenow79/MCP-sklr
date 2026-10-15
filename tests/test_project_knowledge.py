"""Panel, profile projektowe i migracja: tymczasowe dane, brak sieci/serwera."""
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from config_store import ConfigStore
from okf_engine.conversion import prepare_conversion
from okf_engine.models import DocumentIdentity
from okf_engine.parser import parse_document
from okf_engine.preview import build_preview
from okf_engine.writer import WriteError
from panel.app import create_panel_app
from panel.auth import COOKIE_NAME, PanelAuth
from roots_registry import RootsRegistry
from server.file_transaction import FileTransaction
from server.knowledge_admin import KnowledgeAdmin, KnowledgeConflict
from server.knowledge_tools import create_service
from server.project_tools import ProjectTools, call_project
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
ORIGIN = "http://127.0.0.1:8102"


@pytest.fixture
def env(tmp_path):
    work, central = tmp_path / "work", tmp_path / "central"
    (work / "docs").mkdir(parents=True)
    central.mkdir()
    config = {"skills": {"central_dir": str(tmp_path / "skills")},
              "roots": [{"alias": "work", "path": str(work.resolve()), "mode": "rw", "default": True},
                        {"alias": "central", "path": str(central.resolve()), "mode": "rw"}],
              "knowledge": {"bundles": []}}
    store = ConfigStore(config, tmp_path / "config.json")
    roots = RootsRegistry(store)
    task_store = TaskStore(str(tmp_path / "tasks.db"))
    requests = []
    approval = ApprovalService(notifier=requests.append)
    manager = TaskManager(task_store, approval)
    service = create_service(store, roots, manager, lambda: True)
    tx = FileTransaction(roots, store.lock)
    projects = ProjectTools(store, roots, manager, tx, service, lambda: True)
    admin = KnowledgeAdmin(store, roots, service)
    auth = PanelAuth(port=8102)
    client = TestClient(create_panel_app(manager, approval, auth, knowledge=admin), base_url=ORIGIN)
    session = auth.exchange_token(auth.token)
    headers = {"Cookie": f"{COOKIE_NAME}={session}", "Origin": ORIGIN}
    yield SimpleNamespace(work=work, central=central, store=store, roots=roots, manager=manager,
                          approval=approval, service=service, projects=projects, admin=admin,
                          client=client, headers=headers, requests=requests)
    for task in manager.list(limit=100):
        if task.status.value == "awaiting_approval":
            manager.cancel(task.id)
    for task in manager.list(limit=100):
        if not task.is_terminal:
            manager.wait(task.id, 8)
    manager.shutdown()
    task_store.close()


def add(env, scope="project", profile="mixed", subpath="", root="work", id="project"):
    return env.admin.add({"id": id, "label": "Test", "scope": scope, "profile": profile,
                          "subpath": subpath, "root": root, "enabled": True}, env.admin.list()["revision"])


def decide(env, result, decision="allow"):
    env.approval.resolve(result["task_id"], decision)
    task = env.manager.wait(result["task_id"], 8)
    assert task.is_terminal
    return task


def test_admin_add_toggle_remove_preserves_files_and_invalidates_index(env):
    path = env.central / "note.md"
    path.write_text("---\ntype: Reference\n---\nalpha\n", encoding="utf-8")
    result = add(env, scope="central", profile="all", root="central", id="general")
    assert result["bundles"][0]["available"]
    search = env.service.search(query="alpha")
    env.manager.wait(search["refresh_tasks"][0]["task_id"], 8)
    assert env.service.index.snapshot("general") is not None
    result = env.admin.change("0", "enabled", False, result["revision"])
    assert not result["bundles"][0]["available"] and env.service.index.snapshot("general") is None
    env.admin.change("0", "remove", None, result["revision"])
    assert path.is_file() and env.admin.list()["count"] == 0


def test_admin_stale_revision_overlap_and_unapproved_root_are_rejected(env):
    revision = env.admin.list()["revision"]
    add(env)
    with pytest.raises(KnowledgeConflict):
        env.admin.change("0", "remove", None, revision)
    with pytest.raises(ValueError):
        add(env, id="other")
    assert env.admin.list()["count"] == 1
    with pytest.raises(ValueError):
        add(env, root="missing", id="other")


def test_panel_knowledge_security_and_configuration_api(env):
    assert env.client.get("/api/knowledge").status_code == 401
    result = env.client.get("/api/knowledge", headers=env.headers).json()["data"]
    body = {"revision": result["revision"], "bundle": {"id": "general", "label": "General",
            "scope": "central", "root": "central", "subpath": "", "enabled": True}}
    assert env.client.post("/api/knowledge", json=body, headers={"Cookie": env.headers["Cookie"]}).status_code == 403
    response = env.client.post("/api/knowledge", json=body, headers=env.headers)
    assert response.status_code == 200 and response.json()["data"]["count"] == 1
    assert env.client.post("/api/knowledge", json=body, headers=env.headers).status_code == 409
    assert env.client.get("/api/knowledge", headers={**env.headers, "Origin": "https://outside.invalid"}).status_code == 403


@pytest.mark.parametrize("profile,subpath,expected", [
    ("mixed", "", {"docs/plain", ".vibecoder_context", ".vibecoder_checkpoints"}),
    ("legacy", "", {".vibecoder_context", ".vibecoder_checkpoints"}),
    ("docs", "docs", {"plain"}),
])
def test_project_profiles_plain_markdown_search_get_and_exclusions(env, profile, subpath, expected):
    (env.work / "docs/plain.md").write_text("# Alpha\nplain\npassword: SENTINEL\n", encoding="utf-8")
    (env.work / "docs/archive").mkdir()
    (env.work / "docs/archive/old.md").write_text("alpha old", encoding="utf-8")
    (env.work / "docs/nested/archive").mkdir(parents=True)
    (env.work / "docs/nested/archive/old.md").write_text("alpha old", encoding="utf-8")
    for name in (".vibecoder_context.md", ".vibecoder_checkpoints.md", "README.md", "AGENTS.md", "docs/AGENTS.md"):
        (env.work / name).write_text("# Alpha\nlegacy or instructions\n", encoding="utf-8")
    add(env, profile=profile, subpath=subpath)
    cold = env.service.search(query="alpha", scope="project")
    env.manager.wait(cold["refresh_tasks"][0]["task_id"], 8)
    hits = env.service.search(query="alpha", scope="project", refresh="cached")["results"]
    assert {hit["concept_id"] for hit in hits} == expected
    assert all(hit["scope"] == "project" and hit["format"] == "markdown" for hit in hits)
    result = env.service.get("project", sorted(expected)[0], "document")
    assert result["format"] == "markdown" and result["metadata"] == {} and result["signals"]["status"] is None
    assert "SENTINEL" not in json.dumps(result)


def test_project_switch_does_not_include_old_project_in_aggregate_search(env):
    (env.work / "docs/note.md").write_text("alpha", encoding="utf-8")
    add(env)
    result = env.service.search(query="alpha")
    env.manager.wait(result["refresh_tasks"][0]["task_id"], 8)
    assert env.service.search(query="alpha", refresh="cached")["count"] == 1
    env.store.update(lambda config: [root.update(default=root["alias"] == "central") for root in config["roots"]])
    assert env.service.search(query="alpha", refresh="cached")["count"] == 0
    explicit = env.service.search(query="alpha", bundle_id="project")
    assert explicit["refresh_tasks"]  # jawny wybór innego projektu jest dozwolony


def test_prepare_missing_agents_offers_template_and_does_not_block_work(env):
    result = env.projects.prepare()
    assert not result["instructions_complete"] and result["migration_policy"] == "advisory"
    assert result["agents_template"] and result["actions"]
    assert not (env.work / "AGENTS.md").exists() and not env.requests
    task = env.projects.prepare(create_agents=True)
    assert not (env.work / "AGENTS.md").exists()
    assert decide(env, task).status.value == "done"
    assert env.projects.prepare()["instructions_complete"]


def test_prepare_nested_agents_and_truncation_are_explicit(env):
    (env.work / "AGENTS.md").write_text("Root rules\n" + "a" * 15000, encoding="utf-8")
    (env.work / "docs/AGENTS.md").write_text("Docs rules\n", encoding="utf-8")
    result = env.projects.prepare(subpath="docs", max_chars=4000)
    assert result["truncated"] and not result["instructions_complete"]
    assert [item["filepath"] for item in result["instructions"]] == ["AGENTS.md", "docs/AGENTS.md"]
    assert len(json.dumps(result, ensure_ascii=False)) <= 4000


@pytest.mark.parametrize("relative", ["docs/a.md", ".vibecoder_context.md", ".vibecoder_checkpoints.md"])
def test_audit_convert_get_body_unchanged_and_idempotent(env, relative):
    raw = b"\xef\xbb\xbf# Alpha\r\npassword: BODY_SENTINEL\r\n"
    path = env.work / relative
    path.write_bytes(raw)
    row = next(row for row in env.projects.audit()["documents"] if row["filepath"] == relative)
    assert row["format"] == "markdown"
    plan = env.projects.convert([{"filepath": relative, "expected_revision": row["revision"],
                                  "metadata_yaml": "type: Reference\n"}])
    assert path.read_bytes() == raw
    assert "BODY_SENTINEL" not in json.dumps(env.manager.get(plan["task_id"]).to_dict())
    assert decide(env, plan).status.value == "done"
    parsed = parse_document(path.read_bytes(), DocumentIdentity("project", relative[:-3]))
    assert parsed.ok and parsed.body.encode() == raw[3:]
    again = next(row for row in env.projects.audit()["documents"] if row["filepath"] == relative)
    result = env.projects.convert([{"filepath": relative, "expected_revision": again["revision"], "metadata_yaml": None}])
    assert result["changed"] is False


def test_conversion_conflict_and_denial_preserve_original(env):
    path = env.work / "docs/a.md"
    path.write_text("old", encoding="utf-8")
    row = env.projects.audit()["documents"][0]
    task = env.projects.convert([{"filepath": row["filepath"], "expected_revision": row["revision"],
                                  "metadata_yaml": "type: Reference"}])
    assert decide(env, task, "deny").status.value == "rejected" and path.read_bytes() == b"old"
    path.write_text("external", encoding="utf-8")
    with pytest.raises(WriteError, match="REVISION_CONFLICT"):
        env.projects.convert([{"filepath": row["filepath"], "expected_revision": row["revision"], "metadata_yaml": "type: Reference"}])


def test_conversion_preserves_existing_yaml_comments_and_masked_metadata():
    raw = b"---\r\n# COMMENT_SENTINEL\nx-ext: {token: META_SENTINEL}\r\n---\r\nbody\r\n"
    # Rzeczywiste mieszane EOL odmawiane zamiast cichej normalizacji.
    with pytest.raises(WriteError, match="MIXED_EOL_UNSUPPORTED"):
        prepare_conversion(raw, DocumentIdentity("p", "note"), "type: Reference", now=NOW)
    raw = raw.replace(b"# COMMENT_SENTINEL\n", b"# COMMENT_SENTINEL\r\n")
    plan = prepare_conversion(raw, DocumentIdentity("p", "note"), "type: Reference", now=NOW)
    assert b"# COMMENT_SENTINEL\r\nx-ext: {token: META_SENTINEL}\r\n" in plan.after
    public = json.dumps(build_preview(plan))
    assert "META_SENTINEL" not in public and "COMMENT_SENTINEL" not in public
    assert parse_document(plan.after, plan.identity).body == "body\r\n"


def test_conversion_does_not_overwrite_existing_fields():
    raw = b"---\ntype: Reference\nx-ext: one\n---\nbody"
    with pytest.raises(WriteError, match="CONVERSION_EXISTING_FIELD"):
        prepare_conversion(raw, DocumentIdentity("p", "note"), "type: Reference\nx-ext: two", now=NOW)


def test_audit_does_not_offer_conversion_of_mixed_eol(env):
    (env.work / "docs/a.md").write_bytes(b"# Alpha\r\nbody\n")
    row = env.projects.audit()["documents"][0]
    assert not row["convertible"] and "mixed_eol" in row["warnings"]
    assert "suggested_metadata_yaml" not in row


def test_audit_archive_and_forbidden_conversion_scope(env):
    (env.work / "docs/archive").mkdir()
    (env.work / "docs/archive/old.md").write_text("history", encoding="utf-8")
    assert env.projects.audit()["documents"][0]["archive"]
    assert env.projects.audit(include_archive=False)["documents"] == []
    assert not call_project(env.projects.convert, [{"filepath": "README.md", "expected_revision": "r1.invalid"}])["success"]
    assert not call_project(env.projects.convert, [{"filepath": "docs/AGENTS.md", "expected_revision": "r1.invalid"}])["success"]


def test_symlink_docs_is_not_followed(env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_text("private", encoding="utf-8")
    (env.work / "docs").rmdir()
    try:
        os.symlink(outside, env.work / "docs", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable")
    assert env.projects.audit()["documents"] == []
