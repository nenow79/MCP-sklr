"""C3b: zapis po zgodzie, rzeczywista kolejka i wyłącznie tymczasowe dane."""
import json
import os
import threading
from types import SimpleNamespace

import pytest

from config_store import ConfigStore
from server import knowledge_write_tools as tools
from server.file_transaction import FileTransaction
from server.knowledge_tools import create_service
from roots_registry import RootsRegistry
from tasks.approval import ApprovalService
from tasks.manager import QueueFullError, TaskManager
from tasks.store import TaskStore


@pytest.fixture
def env(tmp_path):
    base = tmp_path / "knowledge"
    base.mkdir()
    config = {"skills": {"central_dir": str(tmp_path / "skills")},
              "roots": [{"alias": "test", "path": str(base.resolve()), "mode": "rw"}],
              "knowledge": {"bundles": [{"id": "general", "label": "General", "scope": "central",
                                         "root": "test", "root_path": str(base.resolve()),
                                         "subpath": "", "enabled": True}]}}
    store = ConfigStore(config, tmp_path / "config.json")
    roots = RootsRegistry(store)
    task_store = TaskStore(str(tmp_path / "tasks.db"))
    requests = []
    approval = ApprovalService(notifier=requests.append)
    manager = TaskManager(task_store, approval, max_parallel=2)
    panel = {"available": True}
    service = create_service(store, roots, manager, lambda: panel["available"])
    env = SimpleNamespace(base=base, store=store, roots=roots, manager=manager,
                          task_store=task_store, approval=approval, requests=requests,
                          panel=panel, service=service, tx=FileTransaction(roots, store.lock))
    yield env
    # Oczekujące zgody anulujemy, nie zatwierdzamy w teardown.
    for task in manager.list(limit=100):
        if task.status.value == "awaiting_approval":
            manager.cancel(task.id)
    for task in manager.list(limit=100):
        if not task.is_terminal:
            manager.wait(task.id, 8)
    assert service.close()
    manager.shutdown()
    for worker in manager._workers:
        worker.join(5)
    assert not any(worker.is_alive() for worker in manager._workers)
    task_store.close()


def save(env, **kwargs):
    params = dict(bundle_id="general", concept_id="note", mode="create",
                  body="# Alpha\ntext\n", metadata_yaml="type: Reference\n")
    params.update(kwargs)
    return tools.knowledge_save_impl(env.service, env.roots, env.manager, env.tx,
                                     env.store.lock, lambda: env.panel["available"], **params)


def put(env, body="# Alpha\nold\n", metadata="type: Reference\n", raw=None):
    path = env.base / "note.md"
    path.write_bytes(raw if raw is not None else ("---\n" + metadata + "---\n" + body).encode())
    return path


def revision(env):
    return env.service.get("general", "note")["revision"]


def decide(env, result, decision="allow"):
    assert result["success"], result
    task_id = result["data"]["task_id"]
    env.approval.resolve(task_id, decision)
    task = env.manager.wait(task_id, 8)
    assert task.is_terminal
    return task


def update(env, expected=None, **kwargs):
    params = dict(mode="update", expected_revision=expected or revision(env),
                  metadata_yaml=None, body="# Alpha\nupdated\n")
    params.update(kwargs)
    return save(env, **params)


def test_create_approval_get_and_search(env):
    result = save(env)
    assert result["data"]["status"] == "awaiting_approval"
    assert not (env.base / "note.md").exists() and len(env.requests) == 1
    pending = env.manager.get(result["data"]["task_id"])
    assert pending.kind == "knowledge_write" and pending.sensitive and not pending.offer_trust
    assert "+type: Reference" in pending.payload["diff"]
    done = decide(env, result)
    assert done.status.value == "done"
    summary = json.loads(done.stdout)
    assert summary["committed"] and summary["cleanup_complete"] and summary["rollback_complete"]
    env.manager.wait(summary["refresh_task_id"], 8)
    assert env.service.get("general", "note", "document")["content"] == "# Alpha\ntext\n"
    hit = env.service.search(query="alpha", refresh="cached")["results"][0]
    assert hit["concept_id"] == "note"


def test_committed_write_uses_incremental_backend(env, monkeypatch):
    from okf_engine.cache_backend import CacheRefreshBackend
    put(env)
    (env.base / 'other.md').write_text('---\ntype: Reference\n---\nother\n', encoding='utf-8')
    cold = env.service.search(query='alpha')
    env.manager.wait(cold['refresh_tasks'][0]['task_id'], 8)
    backend = CacheRefreshBackend(env.base.parent / 'cache-test.sqlite3', b'k' * 32)
    env.service._refresh_backend = backend
    reads = []
    from okf_engine.documents import DocumentReader
    original = DocumentReader.read_bytes

    def counted(reader, bundle, concept):
        reads.append(concept)
        return original(reader, bundle, concept)

    try:
        request = update(env)
        monkeypatch.setattr(DocumentReader, 'read_bytes', counted)
        done = decide(env, request)
        summary = json.loads(done.stdout)
        assert summary['committed'] and summary['incremental']
        finished = env.manager.wait(summary['refresh_task_id'], 8)
        assert finished.status.value == 'done'
        assert reads == ['note']
        assert env.service.index.state('general').indexed_documents == 2
    finally:
        assert backend.close()
        env.service._refresh_backend = None


def test_update_uses_get_revision_and_exact_frontmatter_bytes(env):
    raw = b"\xef\xbb\xbf---\r\n# comment\r\ntype: 'Reference'\r\nx-ext: 7\r\n---\r\nold\r\n"
    path = put(env, raw=raw)
    old_revision = revision(env)
    done = decide(env, update(env, old_revision, body="new\n"))
    assert done.status.value == "done"
    assert path.read_bytes() == raw[:-5] + b"new\r\n"
    assert revision(env) != old_revision


@pytest.mark.parametrize("decision", ["deny", "cancel"])
def test_deny_or_cancel_does_not_write(env, decision):
    result = save(env)
    if decision == "cancel":
        done = env.manager.cancel(result["data"]["task_id"])
    else:
        done = decide(env, result, decision)
    assert done.status.value in ("rejected", "cancelled")
    assert not (env.base / "note.md").exists()


def test_trust_is_not_offered_and_is_downgraded_to_allow(env):
    task = decide(env, save(env), "trust")
    assert task.decision == "allow" and task.status.value == "done"


def test_noop_no_task_even_when_panel_down(env):
    put(env, body="# Alpha\ntext\n")
    env.panel["available"] = False
    result = update(env, body="# Alpha\ntext\n")
    assert result["success"] and result["data"]["changed"] is False
    assert env.manager.list() == []


def test_stale_revision_refused_before_task(env):
    path = put(env)
    old = revision(env)
    path.write_bytes(path.read_bytes() + b"external edit\n")
    result = update(env, old)
    assert result["error"] == "REVISION_CONFLICT" and env.manager.list() == []
    assert path.read_bytes().endswith(b"external edit\n")


@pytest.mark.parametrize("mutation", ["file", "disable", "root", "mode", "limits"])
def test_change_after_preparation_refuses_write(env, mutation):
    path = put(env)
    result = update(env)
    original = path.read_bytes()
    if mutation == "file":
        path.write_bytes(original + b"external\n")
        original = path.read_bytes()
    elif mutation == "disable":
        env.store.update(lambda config: config["knowledge"]["bundles"][0].update(enabled=False))
    elif mutation == "root":
        env.store.update(lambda config: config.update(roots=[]))
    elif mutation == "mode":
        env.store.update(lambda config: config["roots"][0].update(mode="ro"))
    else:
        env.store.update(lambda config: config["knowledge"].update(limits={"max_depth": 1}))
    done = decide(env, result)
    assert done.status.value == "failed" and path.read_bytes() == original


def test_disable_after_verify_is_rechecked_by_execute(env, monkeypatch):
    path = put(env)
    original = path.read_bytes()
    result = update(env)
    verify = env.tx.verify

    def disable(plan, **kwargs):
        problem = verify(plan, **kwargs)
        assert problem is None
        env.store.update(lambda config: config["knowledge"]["bundles"][0].update(enabled=False))
        return None

    monkeypatch.setattr(env.tx, "verify", disable)
    done = decide(env, result)
    assert done.status.value == "failed" and not json.loads(done.stdout)["committed"]
    assert path.read_bytes() == original


def test_disable_during_staging_is_rechecked_before_publish(env, monkeypatch):
    result = save(env)
    original = env.tx._stage

    def stage(*args, **kwargs):
        temp = original(*args, **kwargs)
        env.store.update(lambda config: config["knowledge"]["bundles"][0].update(enabled=False))
        return temp

    monkeypatch.setattr(env.tx, "_stage", stage)
    done = decide(env, result)
    assert done.status.value == "failed" and not json.loads(done.stdout)["committed"]
    assert not (env.base / "note.md").exists()
    assert not list(env.base.glob(".sklr-*.tmp"))


def test_snapshot_change_between_writer_and_transaction_is_refused(env, monkeypatch):
    path = put(env)
    original = tools.prepare_exact_write

    def change(*args, **kwargs):
        path.write_bytes(path.read_bytes() + b"external\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(tools, "prepare_exact_write", change)
    assert update(env)["error"] == "SNAPSHOT_CONFLICT"
    assert env.manager.list() == [] and path.read_bytes().endswith(b"external\n")


def test_committed_cleanup_failure_reports_actual_state(env, monkeypatch):
    cleanup = env.tx._cleanup

    def failure(temps):
        cleanup(temps)
        return False

    monkeypatch.setattr(env.tx, "_cleanup", failure)
    done = decide(env, save(env))
    summary = json.loads(done.stdout)
    assert done.status.value == "failed" and summary["committed"] and not summary["cleanup_complete"]
    assert (env.base / "note.md").exists()


def test_publish_failure_is_safe_and_leaves_no_file(env, monkeypatch, caplog):
    def fail(*args):
        raise RuntimeError("SECRET_SENTINEL")

    monkeypatch.setattr(env.tx, "_publish", fail)
    done = decide(env, save(env))
    assert done.status.value == "failed" and not json.loads(done.stdout)["committed"]
    assert "SECRET_SENTINEL" not in caplog.text + json.dumps(done.to_dict())
    assert not (env.base / "note.md").exists() and not list(env.base.glob(".sklr-*.tmp"))


def test_refresh_failure_does_not_fail_committed_write(env):
    result = save(env)
    env.panel["available"] = False  # brak odświeżenia, ale użytkownik nadal jawnie zatwierdza zapis
    done = decide(env, result)
    summary = json.loads(done.stdout)
    assert done.status.value == "done" and summary["committed"] and summary["index_error"]
    assert env.service.get("general", "note", "document")["content"] == "# Alpha\ntext\n"
    assert env.service.index.snapshot("general") is None


def test_unexpected_index_failure_after_commit_never_loses_committed(env, monkeypatch):
    def fail(bundle_id):
        raise RuntimeError("SECRET_SENTINEL")

    monkeypatch.setattr(env.service, "after_write", fail)
    done = decide(env, save(env))
    assert done.status.value == "done" and json.loads(done.stdout)["committed"]
    assert "SECRET_SENTINEL" not in json.dumps(done.to_dict())


def test_incomplete_rollback_invalidates_cached_generation(env, monkeypatch):
    path = put(env)
    search = env.service.search(query="alpha")
    env.manager.wait(search["refresh_tasks"][0]["task_id"], 8)
    assert env.service.index.snapshot("general") is not None
    result = update(env)

    def failed(plan, event, **kwargs):
        path.write_bytes(b"partial external result")
        return {"success": False, "data": {"committed": False, "cancelled": False,
                                            "rollback_complete": False, "cleanup_complete": True}}

    monkeypatch.setattr(env.tx, "execute", failed)
    done = decide(env, result)
    summary = json.loads(done.stdout)
    assert done.status.value == "failed" and not summary["rollback_complete"]
    assert summary["index_state"] == "empty" and env.service.index.snapshot("general") is None


def test_unexpected_verify_exception_does_not_leak_to_logger(env, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError("SECRET_SENTINEL")

    monkeypatch.setattr(env.tx, "verify", fail)
    done = decide(env, save(env))
    assert done.status.value == "failed"
    assert "SECRET_SENTINEL" not in caplog.text + json.dumps(done.to_dict())
    assert not (env.base / "note.md").exists()


def test_unexpected_executor_exception_reports_unknown_commit(env, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError("SECRET_SENTINEL")

    monkeypatch.setattr(env.tx, "execute", fail)
    done = decide(env, save(env))
    assert done.status.value == "failed" and json.loads(done.stdout)["committed"] is None
    assert "SECRET_SENTINEL" not in caplog.text + json.dumps(done.to_dict())


def test_old_scan_cannot_publish_over_generation_after_write(env, monkeypatch):
    from okf_engine import service as module
    put(env)
    entered, release = threading.Event(), threading.Event()
    original = module.scan_bundle
    calls = []

    def scan(*args, **kwargs):
        result = original(*args, **kwargs)
        if not calls:
            calls.append(True)
            entered.set()
            # Stary skan pozostaje wstrzymany do publikacji nowej generacji.
            # finally zwalnia barierę także po nieudanym zapisie/oczekiwaniu;
            # jej własny termin nie może zamienić anulowania w błąd atrapy.
            release.wait()
        return result

    monkeypatch.setattr(module, "scan_bundle", scan)
    try:
        old_scan = env.service.search(query="alpha")["refresh_tasks"][0]["task_id"]
        assert entered.wait(5)
        done = decide(env, update(env))
        assert done.status.value == "done"
        new_scan = json.loads(done.stdout)["refresh_task_id"]
        assert new_scan != old_scan
        assert env.manager.wait(new_scan, 8).status.value == "done"
        assert env.manager.get(old_scan).status.value == "running"
    finally:
        release.set()
    assert env.manager.wait(old_scan, 8).status.value == "cancelled"
    assert env.service.search(query="updated", refresh="cached")["count"] == 1
    assert env.service.search(query="old", refresh="cached")["count"] == 0


def test_secrets_never_persist_in_preview_or_task_output(env):
    result = save(env, body="# Alpha\npassword: BODY_SENTINEL\n",
                  metadata_yaml="type: Reference\nx-ext: {token: META_SENTINEL}\n",
                  description="password: DESCRIPTION_SENTINEL")
    task_id = result["data"]["task_id"]
    pending = env.manager.get(task_id)
    public = json.dumps(pending.to_dict())
    for text in ("BODY_SENTINEL", "META_SENTINEL", "DESCRIPTION_SENTINEL"):
        assert text not in public
    for key in ("body", "metadata_yaml", "expected_revision", "revision", "before_hash", "after_hash"):
        assert key not in pending.payload
    done = decide(env, result)
    for text in ("BODY_SENTINEL", "META_SENTINEL", "DESCRIPTION_SENTINEL", str(env.base)):
        assert text not in json.dumps(done.to_dict())
    assert b"BODY_SENTINEL" in (env.base / "note.md").read_bytes()


def test_revision_is_not_persisted_in_update_task(env):
    put(env)
    old = revision(env)
    result = update(env, old)
    pending = env.manager.get(result["data"]["task_id"])
    assert old not in json.dumps(pending.to_dict())
    assert old not in json.dumps(decide(env, result).to_dict())


def test_masked_update_and_key_loss_refused_without_task(env):
    put(env, body="password: BODY_SENTINEL\n", metadata="type: Reference\nx-ext: {name: one}\n")
    assert update(env)["error"] == "MASKED_UPDATE_UNSUPPORTED"
    put(env, metadata="type: Reference\nx-ext: {name: one}\n")
    assert update(env, metadata_yaml="type: Reference\n")["error"] == "METADATA_KEY_LOSS"
    assert env.manager.list() == []


@pytest.mark.parametrize("concept", ["note", "NOTE"])
def test_create_collision_is_checked_before_queue(env, concept):
    put(env)
    assert save(env, concept_id=concept)["error"] == "CREATE_CONFLICT"
    assert env.manager.list() == []


@pytest.mark.parametrize("concept", ["missing/note", "../note", "/note", "index", "log", "note.md"])
def test_invalid_targets_do_not_create_directories(env, concept):
    assert not save(env, concept_id=concept)["success"]
    assert env.manager.list() == [] and not (env.base / "missing").exists()


def test_parent_link_is_blocked(env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, env.base / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable")
    assert not save(env, concept_id="link/note")["success"]
    assert env.manager.list() == [] and not (outside / "note.md").exists()


def test_ro_nested_root_overrides_rw_bundle(env):
    nested = env.base / "nested"
    nested.mkdir()
    env.store.update(lambda config: config["roots"].append(
        {"alias": "nested", "path": str(nested.resolve()), "mode": "ro"}))
    assert save(env, concept_id="nested/note")["error"] == "WRITE_FORBIDDEN"
    assert env.manager.list() == []


def test_encoding_and_size_limits_apply_before_task(env):
    put(env, raw=b"---\ntype: Reference\n---\nz\xb3")
    result = save(env, mode="update", expected_revision="r1.old", metadata_yaml=None)
    assert result["error"] == "INVALID_UTF8"
    env.store.update(lambda config: config["knowledge"].update(limits={"max_document_bytes": 1024,
                                                                       "max_frontmatter_bytes": 256}))
    assert save(env, concept_id="large", body="x" * 1024)["error"] == "SAFETY_LIMIT"
    assert env.manager.list() == []


@pytest.mark.parametrize("kwargs", [
    {"mode": "upsert"}, {"expected_revision": "r1.old"}, {"metadata_yaml": None},
    {"mode": "update", "expected_revision": None}, {"description": "bad\nline"},
    {"body": None}, {"bundle_id": "../bad"},
])
def test_invalid_arguments_never_queue(env, kwargs):
    assert not save(env, **kwargs)["success"]
    assert env.manager.list() == []


def test_panel_and_queue_failure_never_write(env, monkeypatch):
    env.panel["available"] = False
    assert save(env)["error"] == "PANEL_UNAVAILABLE"
    env.panel["available"] = True

    def full(*args, **kwargs):
        raise QueueFullError("SECRET_SENTINEL")

    monkeypatch.setattr(env.manager, "submit", full)
    result = save(env)
    assert result["error"] == "QUEUE_FULL" and "SECRET_SENTINEL" not in json.dumps(result)
    assert not (env.base / "note.md").exists()


def test_restart_interrupts_pending_write_without_recovering_private_plan(env):
    result = save(env)
    task_id = result["data"]["task_id"]
    env.manager.shutdown()
    env.task_store.mark_interrupted()
    restarted = TaskManager(env.task_store, ApprovalService())
    try:
        assert restarted.get(task_id).status.value == "interrupted"
        assert not (env.base / "note.md").exists()
        assert "body" not in restarted.get(task_id).payload
        assert "metadata_yaml" not in restarted.get(task_id).payload
    finally:
        restarted.shutdown()
