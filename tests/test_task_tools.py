import time

from server.task_tools import (
    cancel_task_impl,
    get_task_impl,
    list_tasks_impl,
    wait_task_impl,
)
from skills_engine import SkillRegistry

SLOW = (
    "import time\n"
    "print('start', flush=True)\n"
    "open('started.flag', 'w').write('x')\n"
    "time.sleep(20)\n"
)


def _skill(tmp_path, script_body="print('ok')"):
    skill_dir = tmp_path / "central" / "demo"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nbody\n", encoding="utf-8")
    (skill_dir / "scripts" / "run.py").write_text(script_body, encoding="utf-8")
    return SkillRegistry(central_dir=tmp_path / "central")


def _wait_flag(path, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def _wait_status(env, task_id, status, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if env.manager.get(task_id).status.value == status:
            return True
        time.sleep(0.05)
    return False


# --- run_skill_script z wait ---
def test_fast_script_returns_result_immediately(tmp_path, make_env):
    env = make_env(_skill(tmp_path), "allow")
    result = env.call(args=[])
    assert result["success"] is True
    assert result["data"]["status"] == "done"
    assert result["data"]["task_id"] == 1
    assert result["data"]["stdout"] == "ok"


def test_slow_script_returns_task_id_then_wait_task(tmp_path, make_env):
    env = make_env(_skill(tmp_path, "import time\ntime.sleep(2)\nprint('late')\n"), "allow")
    begin = time.monotonic()
    result = env.call(args=[], wait=0.3)
    assert time.monotonic() - begin < 2
    assert result["success"] is True
    assert result["data"]["status"] in ("queued", "running")
    assert "wait_task" in result["data"]["hint"]
    task_id = result["data"]["task_id"]

    done = wait_task_impl(env.manager, task_id, 8)
    assert done["success"] is True
    assert done["data"]["status"] == "done"
    assert done["data"]["stdout"] == "late"


def test_wait_zero_returns_without_waiting(tmp_path, make_env):
    env = make_env(_skill(tmp_path, SLOW), "allow")
    begin = time.monotonic()
    result = env.call(args=[], wait=0)
    assert time.monotonic() - begin < 2
    assert result["success"] is True and "task_id" in result["data"]
    cancel_task_impl(env.manager, result["data"]["task_id"])


def test_invalid_wait(tmp_path, make_env):
    env = make_env(_skill(tmp_path), "allow")
    assert env.call(wait=-1)["success"] is False
    assert env.call(wait="x")["success"] is False
    assert env.manager.list() == []


def test_awaiting_approval_returns_immediately(tmp_path, make_env):
    env = make_env(_skill(tmp_path), answer=None)  # nikt nie odpowiada
    begin = time.monotonic()
    result = env.call(args=[], wait=8)
    assert time.monotonic() - begin < 2  # bez czekania (decyzja 1)
    assert result["success"] is True
    assert result["data"]["status"] == "awaiting_approval"
    assert "zatwierdzenie" in result["data"]["hint"]
    task_id = result["data"]["task_id"]

    # użytkownik zatwierdza przez ApprovalService (nie ma narzędzia MCP do tego)
    env.approval.resolve(task_id, "allow")
    done = wait_task_impl(env.manager, task_id, 8)
    assert done["data"]["status"] == "done"


def test_queue_full_returns_error(tmp_path, make_env):
    env = make_env(_skill(tmp_path), answer=None, max_queued=1)
    assert env.call(args=[])["success"] is True
    second = env.call(args=[])
    assert second["success"] is False
    assert "pełna" in second["error"]


# --- get_task / list_tasks ---
def test_get_task_full_data(tmp_path, make_env):
    env = make_env(_skill(tmp_path), "allow")
    env.call(args=["x"])
    result = get_task_impl(env.manager, 1)
    assert result["success"] is True
    data = result["data"]
    assert data["status"] == "done" and data["stdout"] == "ok"
    assert data["args"] == ["x"] and data["decision"] == "allow"
    assert data["script"] == "scripts/run.py" and data["command"]


def test_get_task_errors(tmp_path, make_env):
    env = make_env(_skill(tmp_path), "allow")
    assert get_task_impl(env.manager, 999)["success"] is False
    assert get_task_impl(env.manager, 0)["success"] is False
    assert get_task_impl(env.manager, "1")["success"] is False
    assert get_task_impl(env.manager, True)["success"] is False


def test_list_tasks_filter_limit_and_summary(tmp_path, make_env):
    registry = _skill(tmp_path)
    ok_env = make_env(registry, "allow")
    ok_env.call(args=[])
    ok_env.call(args=[])
    denied = make_env(registry, "deny")
    denied.call(args=[])

    listed = list_tasks_impl(ok_env.manager)
    assert listed["data"]["count"] == 2
    assert [t["id"] for t in listed["data"]["tasks"]] == [2, 1]
    assert "stdout" not in listed["data"]["tasks"][0]

    only_done = list_tasks_impl(ok_env.manager, status="done", limit=1)
    assert only_done["data"]["count"] == 1 and only_done["data"]["tasks"][0]["id"] == 2

    rejected = list_tasks_impl(denied.manager, status="rejected")
    assert rejected["data"]["count"] == 1


def test_list_tasks_validation(tmp_path, make_env):
    env = make_env(_skill(tmp_path), "allow")
    assert list_tasks_impl(env.manager, status="bogus")["success"] is False
    assert list_tasks_impl(env.manager, limit=0)["success"] is False
    assert list_tasks_impl(env.manager, limit=101)["success"] is False
    assert list_tasks_impl(env.manager, limit="5")["success"] is False


# --- wait_task / cancel_task ---
def test_wait_task_running_returns_hint(tmp_path, make_env):
    env = make_env(_skill(tmp_path, SLOW), "allow")
    task_id = env.call(args=[], wait=0)["data"]["task_id"]
    assert _wait_status(env, task_id, "running")
    result = wait_task_impl(env.manager, task_id, 1)
    assert result["success"] is True
    assert result["data"]["status"] == "running"
    assert "hint" in result["data"]
    cancel_task_impl(env.manager, task_id)


def test_wait_task_errors(tmp_path, make_env):
    env = make_env(_skill(tmp_path), "allow")
    assert wait_task_impl(env.manager, 999, 1)["success"] is False
    assert wait_task_impl(env.manager, 0, 1)["success"] is False
    task_id = env.call(args=[])["data"]["task_id"]
    assert wait_task_impl(env.manager, task_id, -5)["success"] is False


def test_cancel_running_task(tmp_path, make_env):
    env = make_env(_skill(tmp_path, SLOW), "allow")
    task_id = env.call(args=[], wait=0)["data"]["task_id"]
    assert _wait_status(env, task_id, "running")
    # proces musi faktycznie wystartować i wypisać "start", zanim anulujemy
    assert _wait_flag(tmp_path / "central" / "demo" / "started.flag")
    result = cancel_task_impl(env.manager, task_id)
    assert result["success"] is True
    assert result["data"]["status"] == "cancelled"
    assert "start" in result["data"]["stdout"]  # częściowy output


def test_cancel_awaiting_task(tmp_path, make_env):
    env = make_env(_skill(tmp_path), answer=None)
    task_id = env.call(args=[])["data"]["task_id"]
    result = cancel_task_impl(env.manager, task_id)
    assert result["data"]["status"] == "cancelled"


def test_cancel_finished_task_is_noop(tmp_path, make_env):
    env = make_env(_skill(tmp_path), "allow")
    task_id = env.call(args=[])["data"]["task_id"]
    result = cancel_task_impl(env.manager, task_id)
    assert result["success"] is True
    assert result["data"]["status"] == "done"
    assert "już zakończone" in result["data"]["note"]


def test_cancel_errors(tmp_path, make_env):
    env = make_env(_skill(tmp_path), "allow")
    assert cancel_task_impl(env.manager, 999)["success"] is False
    assert cancel_task_impl(env.manager, -1)["success"] is False


# P8: adapter/policy tests, without real sleeps, approval grants or processes.
def test_p8_initial_hint_has_one_manual_wait_per_task_id():
    from types import SimpleNamespace
    from server.task_tools import _pending_response
    from tasks.models import TaskStatus
    for task_id in (7, 8):
        hint = _pending_response(SimpleNamespace(id=task_id, status=TaskStatus.AWAITING_APPROVAL))['data']['hint']
        assert 'jedno wait_task(seconds=6)' in hint
        assert 'dla tego task_id' in hint and 'Każde osobne zadanie' in hint
        assert 'własne oczekiwanie do 6 s' in hint and 'Nie powtarzaj oczekiwania' in hint
        assert 'wspólny budżet' not in hint
        assert 'wiele plików ma jedno oczekiwanie' in hint
        assert 'STOP' in hint and 'bez kolejnych wait' in hint
        assert 'Allow nie dowodzi sukcesu' in hint
    # Each distinct task gets its own wait; model policy, no server-side counter.


def test_p8_three_distinct_tasks_can_each_wait_once():
    from types import SimpleNamespace
    from tasks.models import TaskStatus
    from server.task_tools import _pending_response
    calls = []
    for task_id in (7, 8, 9):
        task = SimpleNamespace(id=task_id, status=TaskStatus.AWAITING_APPROVAL,
                               is_terminal=False,
                               to_dict=lambda task_id=task_id: {'id': task_id, 'status': 'awaiting_approval'})
        assert 'własne oczekiwanie do 6 s' in _pending_response(task)['data']['hint']
        manager = SimpleNamespace(wait=lambda task_id, seconds: calls.append((task_id, seconds)) or task)
        result = wait_task_impl(manager, task_id, 6)
        assert 'STOP' in result['data']['hint']
        assert 'nie wykonuj kolejnego wait' in result['data']['hint']
    assert calls == [(7, 6), (8, 6), (9, 6)]


def test_p8_wait_without_decision_stops_without_second_wait():
    from types import SimpleNamespace
    from tasks.models import TaskStatus
    calls = []
    task = SimpleNamespace(status=TaskStatus.AWAITING_APPROVAL, is_terminal=False,
                           to_dict=lambda: {'id': 7, 'status': 'awaiting_approval'})
    manager = SimpleNamespace(wait=lambda task_id, seconds: calls.append((task_id, seconds)) or task)
    response = wait_task_impl(manager, 7, 6)
    assert calls == [(7, 6)]
    assert response['data']['status'] == 'awaiting_approval'
    hint = response['data']['hint']
    assert 'STOP' in hint and 'nie wykonuj kolejnego wait' in hint
    assert 'nie anuluj' in hint and 'nie zgłaszaj zadania ponownie' in hint
    assert 'wait_task(seconds=6)' not in hint


def test_p8_quick_decision_and_long_execution_keep_same_task():
    from types import SimpleNamespace
    from tasks.models import TaskStatus
    calls = []
    for status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.DONE):
        task = SimpleNamespace(status=status, is_terminal=status == TaskStatus.DONE,
                               to_dict=lambda status=status: {'id': 7, 'status': status.value})
        manager = SimpleNamespace(wait=lambda task_id, seconds: calls.append((task_id, seconds)) or task)
        response = wait_task_impl(manager, 7, 6)
        assert response['data']['id'] == 7
        if status == TaskStatus.DONE:
            assert 'hint' not in response['data']
        else:
            assert 'wait_task' in response['data']['hint']
            assert 'STOP' not in response['data']['hint']
    assert calls == [(7, 6)] * 3


def test_p8_wait_boundaries_preserve_requested_execution_wait():
    from types import SimpleNamespace
    from tasks.models import TaskStatus
    calls = []
    task = SimpleNamespace(status=TaskStatus.RUNNING, is_terminal=False,
                           to_dict=lambda: {'id': 7, 'status': 'running'})
    manager = SimpleNamespace(wait=lambda task_id, seconds: calls.append((task_id, seconds)) or task)
    for seconds in (0, 6, 8):
        assert wait_task_impl(manager, 7, seconds)['success']
    assert calls == [(7, 0), (7, 6), (7, 8)]
    for seconds in (-1, True, '6'):
        assert not wait_task_impl(manager, 7, seconds)['success']
    assert len(calls) == 3


def test_p8_submission_hints_share_policy_and_confidential_channels():
    from server import edit_tools, roots_tools, secret_tools, tree_tools, folder_tools
    from server import knowledge_write_tools, project_tools
    from server.task_tools import MANUAL_APPROVAL_HINT
    for module in (edit_tools, roots_tools, secret_tools, tree_tools, folder_tools,
                   knowledge_write_tools, project_tools):
        assert module.WAIT_HINT.startswith(MANUAL_APPROVAL_HINT)
    assert 'get_task.secret_result' in secret_tools.WAIT_HINT
    assert 'nie przedłuża TTL' in secret_tools.WAIT_HINT


def test_p8_external_hint_only_adds_manual_wait_when_approval_is_needed():
    from types import SimpleNamespace
    from tasks.models import TaskStatus
    from server.external_mcp_tools import ExternalMCPTools
    for status in (TaskStatus.AWAITING_APPROVAL, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.DONE):
        response = ExternalMCPTools._response(SimpleNamespace(id=7, status=status))
        hint = response['data']['hint']
        assert 'external_result' in hint
        assert ('wait_task(seconds=6)' in hint) == (status == TaskStatus.AWAITING_APPROVAL)


def test_p8_tool_docs_and_project_contract_use_six_second_manual_policy():
    import ast
    from pathlib import Path
    base = Path(__file__).resolve().parents[1]
    tree = ast.parse((base / 'main.py').read_text(encoding='utf-8'))
    names = {'wait_task', 'run_skill_script', 'edit_file', 'create_file', 'create_tree',
             'manage_folder', 'request_root', 'secret_reveal', 'knowledge_save',
             'knowledge_convert', 'prepare_project', 'call_external_mcp_tool'}
    docs = {node.name: ast.get_docstring(node) for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names}
    assert set(docs) == names
    for name, doc in docs.items():
        assert '6 s' in doc, name
        assert 'STOP' in doc and 'na osobny task_id' in doc, name
        assert 'wspólny budżet' not in doc and 'wspólne dla paczki' not in doc, name
    for relative in ('AGENTS.md', 'docs/specs/files-and-approvals.md'):
        text = (base / relative).read_text(encoding='utf-8')
        assert 'wait_task(seconds=6)' in text and 'STOP' in text
        assert 'wait_task(seconds=8); jeśli nadal awaiting_approval' not in text


# C3a: atrapy sekretów, bez procesów i produkcyjnej telemetrii.
import pytest
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.models import TaskStatus
from tasks.store import TaskStore
from tasks.volatile_results import VolatileResult, VolatileResultStore

C3_PRIVATE = "SENTINEL-C3A-PRIVATE"


@pytest.fixture
def secret_env(tmp_path):
    now = [100.0]
    store = TaskStore(str(tmp_path / "secret.db"))
    approval = ApprovalService()
    volatile = VolatileResultStore(ttl_seconds=5, clock=lambda: now[0])
    manager = TaskManager(store, approval, volatile_store=volatile)
    yield manager, approval, now, store
    manager.shutdown()
    store.close()


def submit_secret(manager, check=None):
    return manager.submit(
        dict(kind="secret_reveal", sensitive=True, offer_trust=False,
             payload={"selector": {"key": "TOKEN"}}),
        lambda event: VolatileResult({"content": C3_PRIVATE}, check), True)


def approve_secret(manager, approval, task):
    approval.resolve(task.id, "allow")
    assert manager.wait(task.id, 5).status == TaskStatus.DONE


def test_secret_get_only_separate_channel_and_no_task_mutation(secret_env, monkeypatch):
    manager, approval, now, store = secret_env
    task = submit_secret(manager)
    assert get_task_impl(manager, task.id)["data"]["secret_result"] == {"state": "unavailable"}
    approve_secret(manager, approval, task)
    before = manager.get(task.id).to_dict()
    for _ in range(2):
        response = get_task_impl(manager, task.id)
        assert response["data"]["secret_result"] == {
            "state": "available", "data": {"content": C3_PRIVATE}}
    assert manager.get(task.id).to_dict() == before
    assert "secret_result" not in before and C3_PRIVATE not in repr(before)
    with store._lock:
        assert C3_PRIVATE not in "\n".join(store._conn.iterdump())
    def forbidden(*args):
        raise AssertionError("Other adapters must not fetch volatile results")
    monkeypatch.setattr(manager, "get_volatile_result", forbidden)
    for response in (list_tasks_impl(manager), wait_task_impl(manager, task.id, 0),
                     cancel_task_impl(manager, task.id)):
        assert response["success"]
        assert C3_PRIVATE not in repr(response) and "secret_result" not in repr(response)


def test_secret_get_ttl_without_extension_and_restart(secret_env):
    manager, approval, now, store = secret_env
    task = submit_secret(manager)
    approve_secret(manager, approval, task)
    for elapsed in (1, 2, 3, 4):
        now[0] = 100.0 + elapsed
        assert get_task_impl(manager, task.id)["data"]["secret_result"]["state"] == "available"
    now[0] = 105.0
    assert get_task_impl(manager, task.id)["data"]["secret_result"] == {"state": "unavailable"}
    second = submit_secret(manager)
    approve_secret(manager, approval, second)
    manager.shutdown()
    fresh = TaskManager(store, ApprovalService())
    try:
        response = get_task_impl(fresh, second.id)
        assert response["data"]["status"] == "done"
        assert response["data"]["secret_result"] == {"state": "unavailable"}
    finally:
        fresh.shutdown()


@pytest.mark.parametrize("decision", ["deny", "cancel", "timeout"])
def test_secret_get_rejected_cancelled_unavailable(secret_env, decision):
    manager, approval, _, _ = secret_env
    task = submit_secret(manager)
    if decision == "cancel":
        manager.cancel(task.id)
    else:
        approval.resolve(task.id, decision)
    response = get_task_impl(manager, task.id)
    assert response["success"]
    assert response["data"]["secret_result"] == {"state": "unavailable"}
    assert C3_PRIVATE not in repr(response)


def test_secret_get_access_revocation(secret_env):
    manager, approval, _, _ = secret_env
    allowed = [True]
    task = submit_secret(manager, lambda: None if allowed[0] else "denied")
    approve_secret(manager, approval, task)
    allowed[0] = False
    assert get_task_impl(manager, task.id)["data"]["secret_result"] == {"state": "denied"}
    assert get_task_impl(manager, task.id)["data"]["secret_result"] == {"state": "unavailable"}


def test_ordinary_get_does_not_fetch_secret_channel(secret_env, monkeypatch):
    manager, _, _, _ = secret_env
    task = manager.submit({}, lambda event: {"exit_code": 0}, False)
    manager.wait(task.id, 5)
    def forbidden(*args):
        raise AssertionError(C3_PRIVATE)
    monkeypatch.setattr(manager, "get_volatile_result", forbidden)
    response = get_task_impl(manager, task.id)
    assert response == {"success": True, "data": manager.get(task.id).to_dict()}
    assert "secret_result" not in response["data"]


def test_secret_channel_exception_safe_no_partial_response(secret_env, monkeypatch, caplog):
    manager, approval, _, _ = secret_env
    task = submit_secret(manager)
    approve_secret(manager, approval, task)
    def fail(*args):
        raise RuntimeError(C3_PRIVATE)
    monkeypatch.setattr(manager, "get_volatile_result", fail)
    response = get_task_impl(manager, task.id)
    assert response == {"success": False,
                        "error": "Nie można pobrać danych zadania (błąd wewnętrzny)."}
    assert C3_PRIVATE not in repr(response) + caplog.text
