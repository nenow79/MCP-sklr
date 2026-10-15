"""C2c: deterministyczny serwis, sztuczne dane i sterowana kolejka bez wątków."""
import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from okf_engine.bundles import evaluate_bundles
from okf_engine.documents import DocumentError
from okf_engine.limits import parse_limits
from okf_engine.revisions import RevisionSigner
from okf_engine.service import KnowledgeError, KnowledgeService
from roots import Root


class Queue:
    def __init__(self):
        self.jobs = {}
        self.failure = None

    def submit(self, bundle_id, run):
        if self.failure:
            raise KnowledgeError(self.failure)
        task_id = len(self.jobs) + 1
        self.jobs[task_id] = SimpleNamespace(run=run, status="queued", event=threading.Event())
        return task_id

    def status(self, task_id):
        return self.jobs[task_id].status

    def finish(self, task_id):
        job = self.jobs[task_id]
        job.status = "running"
        result = job.run(job.event)
        job.status = "failed" if result.get("error") else "done"
        return result


def write(base, content="# Alpha\nfirst\n## Detail\nsecond\n", metadata="type: Reference\n"):
    (base / "note.md").write_bytes(("---\n" + metadata + "---\n" + content).encode())


@pytest.fixture
def env(tmp_path):
    root = Root("test", str(tmp_path.resolve()), "ro")
    config = {"bundles": [{"id": "general", "label": "General", "scope": "central",
                           "root": root.alias, "root_path": root.path, "subpath": "", "enabled": True}]}
    controls = {"roots": [root], "allowed": True, "seconds": 100.0}
    queue = Queue()

    def source():
        limits, _ = parse_limits(config.get("limits", {}))
        version = json.dumps([config, [r.to_dict() for r in controls["roots"]]], sort_keys=True)
        return evaluate_bundles(config, controls["roots"]), limits, version

    def make(key=b"a" * 32):
        return KnowledgeService(source, lambda state, path: controls["allowed"],
                                queue.submit, queue.status,
                                clock=lambda: controls["seconds"],
                                now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc) +
                                timedelta(seconds=controls["seconds"]), signer=RevisionSigner(key))

    write(tmp_path)
    return SimpleNamespace(base=tmp_path, config=config, controls=controls, queue=queue,
                           service=make(), make=make)


def search(env, **kwargs):
    return env.service.search(**dict({"query": "alpha", "bundle_id": "general"}, **kwargs))


def ready(env):
    result = search(env)
    task_id = result["refresh_tasks"][0]["task_id"]
    assert env.queue.finish(task_id)["index_state"] == "ready"
    return search(env, refresh="cached")


def test_cold_search_task_outline_section(env):
    assert env.service.bundles()["bundles"][0]["index_state"] == "empty"
    assert env.queue.jobs == {}
    cold = search(env)
    assert cold["results"] == [] and cold["partial"]
    assert "index_not_ready" in cold["reasons"]
    assert cold["index"]["general"]["previous_state"] == "empty"
    assert env.queue.finish(cold["refresh_tasks"][0]["task_id"])["indexed_documents"] == 1
    hit = search(env, refresh="cached")["results"][0]
    outline = env.service.get("general", "note")
    assert hit["revision"] == outline["revision"]
    section = env.service.get("general", "note", "section", "s1", "direct", outline["revision"])
    assert section["content"] == "# Alpha\nfirst\n"
    assert section["start_line"] == 4 and section["end_line"] == 5
    subtree = env.service.get("general", "note", "section", "s1", "subtree", hit["revision"])
    assert "second" in subtree["content"]


def test_refresh_modes_deduplicate_and_age(env):
    assert search(env, refresh="cached")["refresh_tasks"] == []
    first = search(env)
    assert search(env, refresh="force")["refresh_tasks"] == first["refresh_tasks"]
    assert len(env.queue.jobs) == 1
    env.queue.finish(1)
    assert search(env)["refresh_tasks"] == []
    env.controls["seconds"] += 31
    assert search(env, refresh="cached")["refresh_tasks"] == []
    refresh = search(env)
    assert refresh["count"] == 1  # stara generacja jawna w trakcie skanu
    assert refresh["index"]["general"]["index_state"] == "refreshing"
    assert refresh["index"]["general"]["previous_state"] == "ready"
    assert len(env.queue.jobs) == 2


def test_manual_edit_revision_and_restart(env):
    old = ready(env)["results"][0]["revision"]
    write(env.base, "# Changed\nnew body\n")
    assert search(env, refresh="cached")["results"][0]["revision"] == old
    with pytest.raises(KnowledgeError, match="REVISION_CONFLICT"):
        env.service.get("general", "note", "section", "s1", expected_revision=old)
    fresh = env.service.get("general", "note", "document")
    assert fresh["content"] == "# Changed\nnew body\n" and fresh["revision"] != old
    restarted = env.make(b"b" * 32)
    assert restarted.bundles()["bundles"][0]["index_state"] == "empty"
    with pytest.raises(KnowledgeError, match="REVISION_CONFLICT"):
        restarted.get("general", "note", expected_revision=fresh["revision"])


@pytest.mark.parametrize("change", ["disable", "root", "mode", "limits", "binding"])
def test_access_changes_invalidate_generations_and_late_scans(env, change):
    ready(env)
    task_id = search(env, refresh="force")["refresh_tasks"][0]["task_id"]
    if change == "disable":
        env.config["bundles"][0]["enabled"] = False
    elif change == "root":
        env.controls["roots"] = []
    elif change == "mode":
        env.controls["roots"][0] = replace(env.controls["roots"][0], mode="rw")
    elif change == "limits":
        env.config["limits"] = {"max_documents_per_bundle": 1}
    else:
        env.config["bundles"][0]["root_path"] = str(env.base / "elsewhere")
    env.service.bundles()
    assert env.service.index.snapshot("general") is None
    assert env.queue.finish(task_id)["cancelled"]
    assert env.service.index.snapshot("general") is None
    if change in ("disable", "root", "binding"):
        with pytest.raises(KnowledgeError, match="BUNDLE_UNAVAILABLE"):
            search(env, refresh="cached")


def test_disable_during_scan_blocks_publication(env, monkeypatch):
    from okf_engine import service as module
    original = module.scan_bundle

    def scan(*args, **kwargs):
        result = original(*args, **kwargs)
        env.config["bundles"][0]["enabled"] = False
        return result

    monkeypatch.setattr(module, "scan_bundle", scan)
    search(env)
    assert env.queue.finish(1)["cancelled"]
    assert env.service.index.snapshot("general") is None


def test_document_access_revoked(env):
    ready(env)
    env.controls["allowed"] = False
    result = search(env, refresh="cached")
    assert result["results"] == [] and "document_unavailable" in result["reasons"]
    with pytest.raises(DocumentError, match="DOCUMENT_UNAVAILABLE"):
        env.service.get("general", "note")


def test_access_revoked_after_document_checks_before_atomic_commit(env):
    original = env.service.index._prepare

    def prepare(document):
        result = original(document)
        env.config["bundles"][0]["enabled"] = False
        return result

    env.service.index._prepare = prepare
    search(env)
    assert env.queue.finish(1)["cancelled"]
    assert env.service.index.snapshot("general") is None


def test_failure_backoff_force_and_previous_generation(env, monkeypatch):
    from okf_engine import service as module
    ready(env)

    def broken(*args, **kwargs):
        raise RuntimeError("SENTINEL secret traceback")

    monkeypatch.setattr(module, "scan_bundle", broken)
    search(env, refresh="force")
    assert env.queue.finish(2) == {"error": "REFRESH_FAILED", "cancelled": False}
    result = search(env)
    assert result["count"] == 1 and result["refresh_tasks"] == []
    assert result["index"]["general"]["index_state"] == "error"
    assert "SENTINEL" not in json.dumps(result)
    assert search(env, refresh="force")["refresh_tasks"][0]["task_id"] == 3
    env.queue.finish(3)
    env.controls["seconds"] += 5
    assert search(env)["refresh_tasks"][0]["task_id"] == 4


@pytest.mark.parametrize("error", ["QUEUE_FULL", "PANEL_UNAVAILABLE", "REFRESH_UNAVAILABLE"])
def test_submit_failure_is_reported_without_sync_scan(env, error):
    env.queue.failure = error
    result = search(env)
    assert result["partial"] and result["refresh_errors"] == {"general": error}
    assert env.queue.jobs == {} and env.service.index.snapshot("general") is None
    assert search(env)["refresh_tasks"] == []  # backoff


@pytest.mark.parametrize("running", [False, True])
def test_cancelled_refresh_does_not_stay_refreshing(env, running):
    search(env)
    if running:
        env.queue.jobs[1].event.set()
        assert env.queue.finish(1)["cancelled"]
    else:
        env.queue.jobs[1].status = "cancelled"
    assert env.service.bundles()["bundles"][0]["index_state"] == "error"
    assert search(env)["refresh_tasks"] == []
    assert search(env, refresh="force")["refresh_tasks"][0]["task_id"] == 2


def test_partial_scan_and_manual_removal(env):
    env.config["limits"] = {"max_documents_per_bundle": 1}
    (env.base / "other.md").write_text("---\ntype: Reference\n---\n# Alpha\n", encoding="utf-8")
    search(env)
    assert env.queue.finish(1)["partial"]
    result = search(env, refresh="cached")
    assert result["partial"] and "document_limit" in result["reasons"]
    (env.base / "note.md").unlink()
    result = search(env, refresh="cached")
    assert result["count"] == 0 and "document_unavailable" in result["reasons"]
    search(env, refresh="force")
    env.queue.finish(2)
    assert search(env, refresh="cached")["results"][0]["concept_id"] == "other"


def test_secrets_not_searchable_or_returned(env):
    write(env.base, "# Alpha\npassword: BODY_SENTINEL\n", "type: Reference\ntoken: META_SENTINEL\n")
    ready(env)
    for query in ("BODY_SENTINEL", "META_SENTINEL"):
        assert search(env, query=query, refresh="cached")["results"] == []
    result = env.service.get("general", "note", "document")
    assert result["masked"]
    assert "BODY_SENTINEL" not in json.dumps(result) and "META_SENTINEL" not in json.dumps(result)
    env.config["bundles"][0]["description"] = "password: DESCRIPTION_SENTINEL"
    assert "DESCRIPTION_SENTINEL" not in json.dumps(env.service.bundles())


@pytest.mark.parametrize("view", ["outline", "document", "section"])
def test_get_json_budget(env, view):
    write(env.base, "".join("# Heading %s\n" % i + 'żółć " \\ \n' * 25 for i in range(50)))
    revision = env.service.get("general", "note")["revision"]
    kwargs = {"section_id": "s1", "expected_revision": revision} if view == "section" else {}
    if view == "section":
        write(env.base, "# Alpha\n" + 'żółć " \\ \n' * 500)
        kwargs["expected_revision"] = env.service.get("general", "note")["revision"]
    result = env.service.get("general", "note", view, max_chars=1000, **kwargs)
    key = "entries" if view == "outline" else "content"
    assert len(json.dumps({"metadata": result["metadata"], key: result[key]}, ensure_ascii=False)) <= 1000
    assert result["truncated"]


def test_oversized_metadata_fails_without_leaking(env):
    write(env.base, metadata="type: Reference\ndescription: " + "x" * 2000 + "\n")
    with pytest.raises(KnowledgeError, match="RESULT_TOO_LARGE"):
        env.service.get("general", "note", max_chars=1000)


@pytest.mark.parametrize("kwargs", [
    {"view": "section"}, {"view": "section", "section_id": "s1"},
    {"section_id": "s1"}, {"section_mode": "direct"}, {"view": "invalid"},
    {"max_chars": True}, {"max_chars": 999}, {"max_chars": 80001},
    {"expected_revision": 123}, {"section_mode": "invalid"},
])
def test_invalid_get_arguments(env, kwargs):
    with pytest.raises(KnowledgeError, match="INVALID_ARGUMENT"):
        env.service.get("general", "note", **kwargs)
    assert env.queue.jobs == {}


def test_empty_catalog_unknown_bundle_and_scope(env):
    env.config["bundles"] = []
    result = env.service.search(query="alpha")
    assert result["results"] == [] and result["reasons"] == ["no_available_bundles"]
    assert env.queue.jobs == {}
    with pytest.raises(KnowledgeError, match="BUNDLE_UNAVAILABLE"):
        search(env)
    assert env.service.search(query="alpha", scope="project")["reasons"] == ["no_available_bundles"]


def test_browse_and_search_json_budget(env):
    write(env.base, "# Alpha\n" + "alpha " * 1000)
    ready(env)
    result = search(env, query="", browse=True, max_total_chars=1000, refresh="cached")
    assert result["count"] == 1
    assert len(json.dumps(result["results"], ensure_ascii=False)) <= 1000
    assert result["results"][0]["section_id"] is None  # browse nie wybiera sekcji na podstawie query
