"""P1 foundation tests. No harness, dispatch, server or checkpoint contents."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from tasks.session_models import (
    CheckpointState, SessionConflict, SessionNotFound, SessionState,
    SessionTaskLink, root_aliases,
)
from tasks.session_store import SessionStore
from tasks.store import TaskStore


@pytest.fixture
def store(tmp_path):
    item = SessionStore(tmp_path / 'sessions.db')
    yield item
    item.close()


def test_roundtrip_and_immutable_roots(store):
    roots = ['sklr', 'dev-skills']
    item = store.create(roots)
    roots.append('other')
    assert item.roots == ('sklr', 'dev-skills')
    assert item.state == SessionState.ACTIVE
    assert item.checkpoint_state == CheckpointState.UNAVAILABLE
    assert item.revision == 1 and item.reason == ''
    assert store.get(item.id) == item
    assert store.get('0' * 32) is None


@pytest.mark.parametrize('roots', [[], ['a', 'a'], ['../a'], ['A'], ['a b'],
                                    [1], 'sklr', ['a'] * 33])
def test_invalid_roots(roots):
    with pytest.raises(ValueError):
        root_aliases(roots)


@pytest.mark.parametrize('value', [True, False, 0, -1, 1.5, '1', None])
def test_invalid_task_ids(store, value):
    item = store.create(['sklr'])
    with pytest.raises(ValueError):
        store.link_task(item.id, value, 1)
    with pytest.raises(ValueError):
        store.get_task_link(value)


@pytest.mark.parametrize('value', [True, 0, -1, '1', None])
def test_invalid_revisions(store, value):
    item = store.create(['sklr'])
    with pytest.raises(ValueError):
        store.request_pause(item.id, value)


@pytest.mark.parametrize('value', ['', '../file', '0' * 31, None, 1])
def test_invalid_session_ids(store, value):
    with pytest.raises(ValueError):
        store.get(value)


def test_pause_and_checkpoint_are_independent(store):
    item = store.create(['sklr'])
    requested = store.request_pause(item.id, 1)
    assert requested.state == SessionState.PAUSE_REQUESTED
    assert requested.checkpoint_state == CheckpointState.PENDING
    assert requested.revision == 2
    assert requested.reason == 'user_requested_pause'
    assert store.request_pause(item.id, 2) == requested
    paused = store.mark_paused(item.id, 2)
    assert paused.state == SessionState.PAUSED and paused.revision == 3
    assert paused.checkpoint_state == CheckpointState.PENDING
    assert store.mark_paused(item.id, 3) == paused
    assert store.request_pause(item.id, 3) == paused


def test_stale_revision_and_invalid_transition_roll_back(store):
    item = store.create(['sklr'])
    with pytest.raises(SessionConflict):
        store.mark_paused(item.id, 1)
    assert store.get(item.id) == item
    current = store.request_pause(item.id, 1)
    with pytest.raises(SessionConflict):
        store.request_pause(item.id, 1)
    with pytest.raises(SessionConflict):
        store.link_task(item.id, 5, current.revision)
    assert store.get(item.id) == current
    assert store.get_task_link(5) is None


def test_missing_session(store):
    with pytest.raises(SessionNotFound):
        store.request_pause('0' * 32, 1)
    # Previous failed transaction must not poison subsequent writes.
    assert store.create(['sklr']).revision == 1


def test_immutable_task_links_and_isolation(store):
    a, b = store.create(['sklr']), store.create(['dev-skills'])
    link = store.link_task(a.id, 10, 1)
    assert link == SessionTaskLink(a.id, 10, 1)
    assert store.link_task(a.id, 10, 1) == link
    assert store.get_task_link(10) == link
    with pytest.raises(SessionConflict):
        store.link_task(b.id, 10, 1)
    store.request_pause(a.id, 1)
    assert store.get(b.id) == b
    assert store.link_task(b.id, 11, 1).session_id == b.id


def test_reopen_does_not_recover_or_resume(tmp_path):
    path = tmp_path / 'sessions.db'
    first = SessionStore(path)
    active = first.create(['sklr'])
    requested = first.request_pause(first.create(['a']).id, 1)
    paused = first.mark_paused(requested.id, requested.revision)
    first.link_task(active.id, 50, 1)
    first.close()
    second = SessionStore(path)
    try:
        assert second.get(active.id) == active
        assert second.get(paused.id) == paused
        assert second.recover_after_restart() == 2
        for old in (active, paused):
            current = second.get(old.id)
            assert current.state == SessionState.PAUSED
            assert current.checkpoint_state == CheckpointState.UNAVAILABLE
            assert current.reason == 'server_restarted'
            assert current.revision == old.revision + 1
            with pytest.raises(SessionConflict):
                second.request_pause(old.id, old.revision)
        assert second.get_task_link(50).session_revision == 1
    finally:
        second.close()


def test_two_connections_compare_and_swap(tmp_path):
    path = tmp_path / 'sessions.db'
    a, b = SessionStore(path), SessionStore(path)
    try:
        item = a.create(['sklr'])
        def attempt(store):
            try:
                return store.request_pause(item.id, 1).revision
            except SessionConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (a, b)))
        assert sorted(results, key=str) == [2, 'conflict']
        assert a.get(item.id).revision == 2
    finally:
        a.close()
        b.close()


def test_future_schema_rejected_without_downgrade(tmp_path):
    path = tmp_path / 'future.db'
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError):
        SessionStore(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 99


def test_existing_task_database_not_modified(tmp_path):
    path = tmp_path / 'tasks.db'
    tasks = TaskStore(str(path))
    old = tasks.create(skill='demo')
    tasks.close()
    with pytest.raises(ValueError):
        SessionStore(path)
    tasks = TaskStore(str(path))
    try:
        assert tasks.get(old.id) == old
        assert tasks.create(skill='next').id == old.id + 1
    finally:
        tasks.close()


def test_only_structured_metadata_is_accepted(store):
    with pytest.raises(TypeError):
        store.create(['sklr'], summary='foreign result')
    with pytest.raises(TypeError):
        store.create(['sklr'], checkpoint='secret')
    with store._lock:
        tables = {r[0] for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {r[1] for r in store._conn.execute('PRAGMA table_info(work_sessions)')}
    assert tables == {'work_sessions', 'session_task_links'}
    assert not {'summary', 'checkpoint', 'result', 'payload'} & columns


def test_memory_database():
    store = SessionStore(':memory:')
    try:
        assert store.recover_after_restart() == 0
        assert store.create(['sklr']).revision == 1
    finally:
        store.close()
