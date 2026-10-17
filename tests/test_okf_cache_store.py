"""Etap 1 cache: tylko tmp_path, brak produkcyjnej bazy i serwera."""
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from okf_engine.cache_store import (
    CacheError, CacheStore, default_cache_paths, fingerprint, record_from_public,
)
from okf_engine.models import DocumentIdentity
from okf_engine.parser import parse_document
from okf_engine.public_view import build_public_view

IDENTITY = DocumentIdentity('general', 'note')
KEY = b'k' * 32
RAW = b'---\ntype: Reference\npassword: META_SENTINEL\n---\n# Note\ntoken: BODY_SENTINEL\n'


def record():
    view = build_public_view(parse_document(RAW, IDENTITY), datetime.now(timezone.utc))
    return record_from_public(view, 'binding-v1', 'parser1-mask1',
                              (1, 2, len(RAW), 100), fingerprint(RAW, KEY))


def test_default_paths_do_not_create_files(tmp_path):
    db, key = default_cache_paths(tmp_path)
    assert db == tmp_path / 'knowledge_index.sqlite3'
    assert key.name == 'knowledge_cache.key'
    assert not db.exists() and not key.exists()
    with pytest.raises(CacheError):
        default_cache_paths('relative')


def test_restart_binding_versions_and_masking(tmp_path):
    path = tmp_path / 'cache.sqlite3'
    store = CacheStore(path)
    store.apply([record()])
    store.close()
    reopened = CacheStore(path)
    try:
        found = reopened.load(IDENTITY, 'binding-v1', 'parser1-mask1')
        assert found is not None and found.digest == fingerprint(RAW, KEY)
        assert 'SENTINEL' not in json.dumps(found.metadata) + found.body + repr(found)
        assert reopened.load(IDENTITY, 'changed-binding', 'parser1-mask1') is None
        assert reopened.load(IDENTITY, 'binding-v1', 'parser2-mask1') is None
    finally:
        reopened.close()
    assert b'META_SENTINEL' not in path.read_bytes()
    assert b'BODY_SENTINEL' not in path.read_bytes()


def test_upsert_delete_and_no_api_revision_columns(tmp_path):
    path = tmp_path / 'cache.sqlite3'
    store = CacheStore(path)
    try:
        store.apply([record()])
        store.apply([replace(record(), body='# Changed\n')])
        assert store.load(IDENTITY, 'binding-v1', 'parser1-mask1').body == '# Changed\n'
        store.apply(deletes=[IDENTITY])
        assert store.load(IDENTITY, 'binding-v1', 'parser1-mask1') is None
    finally:
        store.close()
    with sqlite3.connect(path) as db:
        names = [row[1] for row in db.execute('PRAGMA table_info(documents)')]
        assert 'revision' not in names and 'key' not in names


def test_rejects_invalid_batch_without_partial_write(tmp_path):
    store = CacheStore(tmp_path / 'cache.sqlite3')
    try:
        other = replace(record(), identity=DocumentIdentity('general', 'other'))
        with pytest.raises(CacheError):
            store.apply([other, replace(record(), digest='invalid')])
        assert store.load(other.identity, 'binding-v1', 'parser1-mask1') is None
        with pytest.raises(CacheError, match='CACHE_DUPLICATE_TARGET'):
            store.apply([record()], deletes=[IDENTITY])
    finally:
        store.close()


def test_sql_error_rolls_back_batch_without_secret_error(tmp_path):
    path = tmp_path / 'cache.sqlite3'
    store = CacheStore(path)
    try:
        store.apply([record()])
        with sqlite3.connect(path) as db:
            db.execute("CREATE TRIGGER reject_other BEFORE INSERT ON documents "
                       "WHEN NEW.concept='other' BEGIN SELECT RAISE(ABORT,'ERROR_SENTINEL'); END")
        other = replace(record(), identity=DocumentIdentity('general', 'other'))
        with pytest.raises(CacheError, match='^CACHE_WRITE_FAILED$'):
            store.apply([other], deletes=[IDENTITY])
        assert store.load(IDENTITY, 'binding-v1', 'parser1-mask1') is not None
    finally:
        store.close()


def test_unknown_schema_not_replaced(tmp_path):
    path = tmp_path / 'cache.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE unrelated (value TEXT)')
        db.execute("INSERT INTO unrelated VALUES ('keep')")
    with pytest.raises(CacheError, match='CACHE_SCHEMA_UNSUPPORTED'):
        CacheStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT value FROM unrelated').fetchone() == ('keep',)


def test_corrupt_payload_safe_error(tmp_path):
    path = tmp_path / 'cache.sqlite3'
    store = CacheStore(path)
    try:
        store.apply([record()])
        with sqlite3.connect(path) as db:
            db.execute("UPDATE documents SET payload='INVALID_SENTINEL'")
        with pytest.raises(CacheError, match='^CACHE_READ_FAILED$'):
            store.load(IDENTITY, 'binding-v1', 'parser1-mask1')
    finally:
        store.close()


def test_fingerprint_is_keyed_and_not_api_revision():
    assert fingerprint(RAW, KEY) == fingerprint(RAW, KEY)
    assert fingerprint(RAW, KEY) != fingerprint(RAW, b'x' * 32)
    assert fingerprint(RAW, KEY) != fingerprint(RAW + b'changed', KEY)
    with pytest.raises(CacheError):
        fingerprint(RAW, b'short')


def test_public_view_required_and_re_masking(tmp_path):
    with pytest.raises(CacheError, match='CACHE_PUBLIC_VIEW_REQUIRED'):
        record_from_public(parse_document(RAW, IDENTITY), 'b', 'v', (1, 2, 3, 4), fingerprint(RAW, KEY))
    store = CacheStore(tmp_path / 'cache.sqlite3')
    try:
        store.apply([replace(record(), metadata={'type': 'Reference', 'password': 'NEW_SENTINEL'},
                             body='token: ANOTHER_SENTINEL\n')])
        found = store.load(IDENTITY, 'binding-v1', 'parser1-mask1')
        assert 'SENTINEL' not in found.body + json.dumps(found.metadata)
    finally:
        store.close()


def test_pages_filter_binding_versions_and_do_not_skip(tmp_path):
    store = CacheStore(tmp_path / 'cache.sqlite3')
    try:
        records = [replace(record(), identity=DocumentIdentity('general', name))
                   for name in ('a', 'b', 'c')]
        store.apply(records)
        first, cursor = store.load_page('general', 'binding-v1', 'parser1-mask1', limit=2)
        assert [r.identity.concept_id for r in first] == ['a', 'b'] and cursor == 'b'
        second, cursor = store.load_page('general', 'binding-v1', 'parser1-mask1',
                                       after=cursor, limit=2)
        assert [r.identity.concept_id for r in second] == ['c'] and cursor == 'c'
        assert store.load_page('general', 'binding-v1', 'parser1-mask1', after=cursor) == ((), None)
        assert store.load_page('general', 'other-binding', 'parser1-mask1') == ((), None)
        assert store.load_page('general', 'binding-v1', 'other-version') == ((), None)
        for limit in (True, 0, 101):
            with pytest.raises(CacheError, match='CACHE_PAGE_INVALID'):
                store.load_page('general', 'binding-v1', 'parser1-mask1', limit=limit)
    finally:
        store.close()


def test_purge_bundle_keeps_other_bundles(tmp_path):
    store = CacheStore(tmp_path / 'cache.sqlite3')
    try:
        other = replace(record(), identity=DocumentIdentity('other', 'note'))
        store.apply([record(), other])
        assert store.purge_bundle('general') == 1
        assert store.purge_bundle('general') == 0
        assert store.load(IDENTITY, 'binding-v1', 'parser1-mask1') is None
        assert store.load(other.identity, 'binding-v1', 'parser1-mask1') is not None
    finally:
        store.close()


@pytest.mark.parametrize('kind', ['bytes', 'bundles', 'records'])
def test_total_limits_roll_back_changes(tmp_path, monkeypatch, kind):
    from okf_engine import cache_store as module
    store = CacheStore(tmp_path / 'cache.sqlite3')
    try:
        store.apply([record()])
        if kind == 'bytes':
            monkeypatch.setattr(module, 'MAX_CACHE_PAYLOAD_BYTES', 1)
        elif kind == 'bundles':
            monkeypatch.setattr(module, 'MAX_BUNDLES', 1)
        else:
            monkeypatch.setattr(module, 'MAX_RECORDS', 1)
        identity = DocumentIdentity('other' if kind == 'bundles' else 'general', 'other')
        other = replace(record(), identity=identity)
        with pytest.raises(CacheError, match='CACHE_(TOTAL|RECORD)_LIMIT'):
            store.apply([other])
        assert store.load(IDENTITY, 'binding-v1', 'parser1-mask1') is not None
        assert store.load(identity, 'binding-v1', 'parser1-mask1') is None
    finally:
        store.close()


def test_over_limit_existing_cache_is_not_silently_replaced(tmp_path, monkeypatch):
    from okf_engine import cache_store as module
    path = tmp_path / 'cache.sqlite3'
    store = CacheStore(path)
    store.apply([record()])
    store.close()
    monkeypatch.setattr(module, 'MAX_CACHE_PAYLOAD_BYTES', 1)
    with pytest.raises(CacheError, match='CACHE_TOTAL_LIMIT'):
        CacheStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 1


def test_signed_record_survives_restart_and_tampering_is_detected(tmp_path):
    from okf_engine.cache_hit import record_tag
    import hmac
    path = tmp_path / 'signed.sqlite3'
    original = record()
    signed = replace(original, auth_tag=record_tag(original, KEY))
    store = CacheStore(path)
    store.apply([signed])
    store.close()
    store = CacheStore(path)
    try:
        found = store.load(IDENTITY, 'binding-v1', 'parser1-mask1')
        assert found.auth_tag == signed.auth_tag
        assert hmac.compare_digest(found.auth_tag, record_tag(found, KEY))
        with sqlite3.connect(path) as db:
            payload = json.loads(db.execute('SELECT payload FROM documents').fetchone()[0])
            payload['body'] = '# Forged\n'
            db.execute('UPDATE documents SET payload=?', (json.dumps(payload),))
        found = store.load(IDENTITY, 'binding-v1', 'parser1-mask1')
        assert not hmac.compare_digest(found.auth_tag, record_tag(found, KEY))
    finally:
        store.close()


def test_v1_migration_preserves_unsigned_records(tmp_path):
    from okf_engine.cache_store import _payload
    path = tmp_path / 'old.sqlite3'
    original = record()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE documents (bundle TEXT NOT NULL, concept TEXT NOT NULL, '
                   'binding TEXT NOT NULL, versions TEXT NOT NULL, snapshot TEXT NOT NULL, '
                   'digest TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(bundle,concept))')
        db.execute('PRAGMA user_version=1')
        db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?)',
                   ('general', 'note', original.binding, original.versions,
                    json.dumps(original.snapshot), original.digest, _payload(original)))
    store = CacheStore(path)
    try:
        found = store.load(IDENTITY, 'binding-v1', 'parser1-mask1')
        assert found.body == original.body and found.auth_tag is None
    finally:
        store.close()
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2


def test_invalid_tag_rejected_before_write(tmp_path):
    store = CacheStore(tmp_path / 'cache.sqlite3')
    try:
        with pytest.raises(CacheError, match='CACHE_AUTH_INVALID'):
            store.apply([replace(record(), auth_tag='invalid')])
        assert store.load(IDENTITY, 'binding-v1', 'parser1-mask1') is None
    finally:
        store.close()


def test_link_database_is_rejected(tmp_path):
    target = tmp_path / 'target.sqlite3'
    target.write_bytes(b'')
    link = tmp_path / 'link.sqlite3'
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip('symlink unavailable')
    with pytest.raises(CacheError, match='CACHE_PATH_INVALID'):
        CacheStore(link)
