"""L2c shutdown ordering via the existing WorkSession coordinator.

Proposed seam: stop_work_session(..., external_mcp=tools), tools.close_persistent,
and bounded supervisor.shutdown(timeout=...). No production main is imported.
Missing seam is API RED, not execution of the shutdown assertions.
"""
from types import SimpleNamespace

import pytest

from external_mcp.limits import ExternalError
from external_mcp_persistent_admission_helpers import until, wait
from external_mcp_persistent_fakes import Gate
from external_mcp_persistent_manual_lifecycle_helpers import LifecycleRig
from server.session_activation import stop_work_session


@pytest.fixture
def rig(tmp_path):
    instance = LifecycleRig(tmp_path / 'shutdown')
    try:
        yield instance
    finally:
        instance.close()


def runtime_probe(rig):
    closed = []
    original = rig.sessions.close
    def close():
        assert not any(t.is_alive() for t in rig.manager._workers)
        assert rig.tools._lifecycle.thread is None or not rig.tools._lifecycle.thread.is_alive()
        assert rig.supervisor.thread is None or not rig.supervisor.thread.is_alive()
        assert all(b.closed.is_set() for b in rig.hub.backends)
        closed.append(True)
        original()
    return SimpleNamespace(controller=rig.controller, close=close), closed


def test_idle_shutdown_closes_runtime_and_threads_before_session_store(rig, monkeypatch):
    rig.discover()
    runtime, closed = runtime_probe(rig)
    original = rig.supervisor.shutdown
    def shutdown(*args, **kwargs):
        assert not rig.manager._cond._is_owned()
        assert not rig.controller._lock._is_owned()
        assert not rig.config.lock._is_owned()
        return original(*args, **kwargs)
    monkeypatch.setattr(rig.supervisor, 'shutdown', shutdown)
    assert stop_work_session(rig.manager, runtime, budget=2, external_mcp=rig.tools)
    assert closed == [True]
    assert rig.hub.backends[0].cleanup_attempts == 1
    assert rig.tools.close_persistent(timeout=0) is True


def test_active_call_withholds_store_close_until_drain_finishes(rig):
    rig.discover()
    backend = rig.hub.backends[0]
    backend.call = Gate(blocked=True)
    response = rig.call()
    tid = response['data']['task_id']
    rig.manager.resolve_approval(tid, 'allow')
    runtime, closed = runtime_probe(rig)
    try:
        wait(backend.call.entered, 'call before shutdown')
        assert stop_work_session(rig.manager, runtime, budget=0,
                                 external_mcp=rig.tools) is False
        assert not closed and not backend.closed.is_set()
        assert rig.sessions.get(rig.session.id) is not None
        assert rig.controller.snapshot()['execution_blocked']
        assert rig.tools.list_tools('fake')['success'] is False
        assert len(backend.calls) == 1
    finally:
        backend.call.release.set()
        assert rig.manager.wait(tid, 5).is_terminal
    assert stop_work_session(rig.manager, runtime, budget=2, external_mcp=rig.tools)
    assert closed == [True]
    assert len(backend.calls) == backend.cleanup_attempts == 1


def test_cleanup_timeout_keeps_stores_and_reuses_same_shutdown_until_done(rig, monkeypatch):
    rig.discover()
    backend = rig.hub.backends[0]
    backend.cleanup = Gate(blocked=True)
    runtime, closed = runtime_probe(rig)
    original = rig.supervisor._shutdown
    starts = []
    async def shutdown():
        starts.append(True)
        await original()
    monkeypatch.setattr(rig.supervisor, '_shutdown', shutdown)
    try:
        for _ in range(2):
            assert stop_work_session(rig.manager, runtime, budget=0.1,
                                     external_mcp=rig.tools) is False
            assert closed == []
            assert rig.sessions.get(rig.session.id) is not None
        wait(backend.cleanup.entered, 'bounded cleanup')
        assert starts == [True]
    finally:
        backend.cleanup.release.set()
    assert stop_work_session(rig.manager, runtime, budget=2, external_mcp=rig.tools)
    assert starts == closed == [True]
    assert backend.cleanup_attempts == 1


def test_cleanup_failure_remains_unconfirmed_and_withholds_store_close(rig):
    rig.discover()
    rig.hub.backends[0].cleanup_error = ExternalError('stdio_cleanup_failed')
    runtime, closed = runtime_probe(rig)
    assert stop_work_session(rig.manager, runtime, budget=2, external_mcp=rig.tools) is False
    assert closed == []
    assert rig.sessions.get(rig.session.id) is not None
    assert rig.supervisor.snapshot(owner=rig.session.id,
        profile_id=rig.profile['id'])['state'] == 'broken'
    assert rig.tools.close_persistent(timeout=0) is False
    assert rig.hub.backends[0].cleanup_attempts == 1


def test_unclosed_lifecycle_worker_withholds_store_close(rig, monkeypatch):
    rig.discover()
    runtime, closed = runtime_probe(rig)
    with monkeypatch.context() as patch:
        patch.setattr(rig.tools, 'close_lifecycle', lambda timeout=5: False)
        assert stop_work_session(rig.manager, runtime, budget=0.1,
                                 external_mcp=rig.tools) is False
        assert closed == []
        assert rig.sessions.get(rig.session.id) is not None
    assert stop_work_session(rig.manager, runtime, budget=2, external_mcp=rig.tools)
    assert closed == [True]


def test_optional_external_shutdown_also_runs_without_work_session():
    events = []
    manager = SimpleNamespace(shutdown=lambda: events.append('manager'), _workers=[])
    external = SimpleNamespace(close_persistent=lambda timeout: events.append('external') or True)
    assert stop_work_session(manager, None, budget=0, external_mcp=external) is True
    assert events == ['manager', 'external']


def test_external_shutdown_exception_is_unconfirmed_without_closing_store():
    import threading
    events = []
    manager = SimpleNamespace(shutdown=lambda: events.append('manager'), _workers=[])
    controller = SimpleNamespace(_lock=threading.RLock(), _tokens=set(), _blocked=False)
    runtime = SimpleNamespace(controller=controller, close=lambda: events.append('store'))
    def close(timeout):
        raise RuntimeError('PRIVATE_SHUTDOWN_FAILURE')
    external = SimpleNamespace(close_persistent=close)
    assert stop_work_session(manager, runtime, budget=0, external_mcp=external) is False
    assert events == ['manager']


def test_lifecycle_close_exception_is_unconfirmed(rig, monkeypatch):
    rig.discover()
    def close(timeout):
        raise RuntimeError('PRIVATE_LIFECYCLE_FAILURE')
    with monkeypatch.context() as patch:
        patch.setattr(rig.tools, 'close_lifecycle', close)
        assert rig.tools.close_persistent(timeout=0) is False
