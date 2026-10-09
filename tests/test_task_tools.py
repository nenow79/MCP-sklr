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
