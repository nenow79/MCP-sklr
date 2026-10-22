"""P3b1 durable resume transitions only; these APIs do not authorize dispatch."""
import inspect
from concurrent.futures import ThreadPoolExecutor

import pytest

from tasks.session_models import SessionConflict, SessionState
from tasks.session_store import SessionStore


@pytest.fixture
def store(tmp_path):
    instance = SessionStore(tmp_path / 'work_sessions.db')
    yield instance
    instance.close()


def paused(store):
    active = store.create(['sklr'])
    requested = store.request_pause(active.id, active.revision)
    return store.mark_paused(requested.id, requested.revision)


def test_prepare_stays_paused_and_confirm_advances_revision(store):
    before = paused(store)
    prepared = store.prepare_resume(before.id, before.revision)
    assert prepared.state == SessionState.PAUSED
    assert prepared.revision == before.revision + 1
    assert prepared.reason == before.reason
    assert prepared.checkpoint_state == before.checkpoint_state
    confirmed = store.confirm_resume(prepared.id, prepared.revision)
    assert confirmed.state == SessionState.ACTIVE and confirmed.reason == ''
    assert confirmed.revision == prepared.revision + 1
    assert confirmed.roots == before.roots


@pytest.mark.parametrize('method', ['prepare_resume', 'confirm_resume'])
def test_active_and_pause_requested_are_rejected(store, method):
    active = store.create(['sklr'])
    with pytest.raises(SessionConflict):
        getattr(store, method)(active.id, active.revision)
    assert store.get(active.id) == active
    requested = store.request_pause(active.id, active.revision)
    with pytest.raises(SessionConflict):
        getattr(store, method)(requested.id, requested.revision)
    assert store.get(active.id) == requested


@pytest.mark.parametrize('method', ['prepare_resume', 'confirm_resume'])
def test_stale_revision_does_not_mutate(store, method):
    item = paused(store)
    with pytest.raises(SessionConflict):
        getattr(store, method)(item.id, item.revision - 1)
    assert store.get(item.id) == item


@pytest.mark.parametrize('bad', [0, -1, True, '1', None])
def test_invalid_revision_is_rejected(store, bad):
    item = paused(store)
    for method in ('prepare_resume', 'confirm_resume'):
        with pytest.raises(ValueError):
            getattr(store, method)(item.id, bad)
        assert store.get(item.id) == item


def test_old_preparation_cannot_confirm_after_new_preparation(store):
    item = paused(store)
    first = store.prepare_resume(item.id, item.revision)
    second = store.prepare_resume(first.id, first.revision)
    with pytest.raises(SessionConflict):
        store.confirm_resume(first.id, first.revision)
    assert store.get(item.id) == second
    store.confirm_resume(second.id, second.revision)


def test_restart_invalidates_prepared_generation(store):
    item = paused(store)
    prepared = store.prepare_resume(item.id, item.revision)
    store.recover_after_restart()
    current = store.get(item.id)
    assert current.state == SessionState.PAUSED and current.reason == 'server_restarted'
    assert current.revision == prepared.revision + 1
    with pytest.raises(SessionConflict):
        store.confirm_resume(prepared.id, prepared.revision)


def test_restart_after_confirmation_never_resumes(store):
    item = paused(store)
    prepared = store.prepare_resume(item.id, item.revision)
    confirmed = store.confirm_resume(item.id, prepared.revision)
    store.recover_after_restart()
    current = store.get(item.id)
    assert current.state == SessionState.PAUSED and current.revision == confirmed.revision + 1
    assert current.checkpoint_state.value == 'unavailable'


def test_concurrent_confirmation_has_single_commit(store):
    item = paused(store)
    prepared = store.prepare_resume(item.id, item.revision)
    def confirm(_):
        try:
            store.confirm_resume(item.id, prepared.revision)
            return True
        except SessionConflict:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(confirm, range(8)))
    assert sum(results) == 1
    assert store.get(item.id).revision == prepared.revision + 1


def test_api_accepts_metadata_only_and_schema_has_no_resume_text(store):
    assert list(inspect.signature(store.prepare_resume).parameters) == ['sid', 'expected_revision']
    assert list(inspect.signature(store.confirm_resume).parameters) == ['sid', 'expected_revision']
    item = paused(store)
    prepared = store.prepare_resume(item.id, item.revision)
    store.confirm_resume(item.id, prepared.revision)
    columns = {row[1] for row in store._conn.execute('PRAGMA table_info(work_sessions)')}
    assert columns == {'id', 'roots', 'state', 'checkpoint_state', 'revision', 'reason',
                       'created_at', 'updated_at', 'lifecycle'}
    assert store._conn.execute('PRAGMA user_version').fetchone()[0] == 2
