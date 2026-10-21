"""Metadata journal: fail-closed transactions, restart and protected retention."""
from dataclasses import replace
import json
import sqlite3

import pytest

from autopilot.audit_models import AuditEvent
from autopilot.audit_store import AuditError, AuditStore
from autopilot.models import ContractBinding

BINDING = ContractBinding('a' * 32, 'b' * 32, 1, 1, 'c' * 64)


def event(**fields):
    return AuditEvent(BINDING, 'started', 'active', operation_id='d' * 32,
                      tool='read_file', used=1, running=1, **fields)


def test_start_is_protected_until_finished_and_history_is_metadata_only(tmp_path):
    store = AuditStore(tmp_path / 'audit.db')
    start = event()
    store.append(start)
    assert len(store.unresolved()) == 1
    store.append(replace(start, event='finished', code='ok', running=0))
    assert store.unresolved() == ()
    metadata = json.dumps(store.history())
    for forbidden in ('filepath', 'arguments', 'stdout', 'checkpoint', 'headers', 'url'):
        assert forbidden not in metadata
    store.close()


@pytest.mark.parametrize('changes', [
    {'event': 'arbitrary'}, {'state': 'unknown'}, {'code': 'PRIVATE_ARGUMENT'},
    {'operation_id': 'url-secret'}, {'task_id': True}, {'tool': 'execute_command'},
    {'rule_ids': ['rule']}, {'rule_ids': ('../../secret',)}, {'used': True}, {'running': 2},
])
def test_closed_record_rejects_unbounded_or_content_fields(changes):
    with pytest.raises((ValueError, TypeError)):
        replace(event(), **changes)


def test_duplicate_start_never_authorizes_a_second_effect(tmp_path):
    store = AuditStore(tmp_path / 'audit.db')
    store.append(event())
    with pytest.raises(AuditError, match='^audit_commit_uncertain$'):
        store.append(event())
    assert len(store.unresolved()) == 1
    assert len(store.history()) == 1
    store.close()


def test_unknown_completion_does_not_invent_a_started_operation(tmp_path):
    store = AuditStore(tmp_path / 'audit.db')
    with pytest.raises(AuditError):
        store.append(replace(event(), event='finished', code='ok', running=0))
    assert store.history() == ()
    store.close()


def test_restart_preserves_uncertain_metadata_without_authority(tmp_path):
    path = tmp_path / 'audit.db'
    store = AuditStore(path)
    store.append(event())
    store.close()
    restarted = AuditStore(path)
    assert len(restarted.unresolved()) == 1
    restarted.append(replace(event(), event='finished', code='uncertain', running=0))
    assert len(restarted.unresolved()) == 1
    restarted.append(replace(event(), event='resolved', code='ok', running=0))
    assert restarted.unresolved() == ()
    restarted.close()


def test_retention_caps_history_but_keeps_old_unresolved_operation(tmp_path):
    now = [0.0]
    store = AuditStore(tmp_path / 'audit.db', clock=lambda: now[0])
    store.append(event())
    now[0] = 31 * 86400
    store.purge()
    assert store.history() == () and len(store.unresolved()) == 1
    for _ in range(1005):
        store.append(AuditEvent(BINDING, 'reserved', 'active'))
    assert len(store.history()) == 1000 and len(store.unresolved()) == 1
    store.close()


def test_sqlite_failure_leaves_no_partial_start(tmp_path):
    store = AuditStore(tmp_path / 'audit.db')
    store._db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON autopilot_events BEGIN SELECT RAISE(ABORT, 'x'); END")
    with pytest.raises(AuditError):
        store.append(event())
    assert store.unresolved() == () and store.history() == ()
    store.close()


def test_wal_and_database_never_receive_rejected_content(tmp_path):
    path = tmp_path / 'audit.db'
    store = AuditStore(path)
    store._db.execute('PRAGMA journal_mode=WAL')
    with pytest.raises(ValueError):
        replace(event(), code='PRIVATE_AUDIT_SENTINEL')
    store.append(event())
    for file in tmp_path.glob('audit.db*'):
        assert b'PRIVATE_AUDIT_SENTINEL' not in file.read_bytes()
    store.close()
