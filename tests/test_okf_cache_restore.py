"""Odtworzenie podpisanego cache bez źródeł; brak runtime i produkcyjnej bazy."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from okf_engine.bundles import BundleEntry, BundleState
from okf_engine.cache_hit import record_tag
from okf_engine.cache_restore import restore_cached_document
from okf_engine.cache_store import CacheError, fingerprint, record_from_public
from okf_engine.models import DocumentIdentity
from okf_engine.parser import parse_document
from okf_engine.public_view import build_public_view

KEY = b'k' * 32
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def setup(tmp_path, concept='note'):
    # Celowo nieistniejący katalog: odtworzenie nie wykonuje filesystem I/O.
    path = tmp_path / 'missing-docs'
    entry = BundleEntry('test', 'Test', 'project', 'work', 'docs', str(tmp_path),
                        enabled=True, profile='docs')
    state = BundleState('test', entry, True, False, '', '', path)
    raw = b'---\ntype: Specification\n---\n# Contract\ntext\n'
    view = build_public_view(parse_document(raw, DocumentIdentity('test', concept)), NOW)
    record = record_from_public(view, 'binding', 'v1', (1, 2, len(raw), 100), fingerprint(raw, KEY))
    return state, replace(record, auth_tag=record_tag(record, KEY))


def restore(state, record):
    return restore_cached_document(record, KEY, state, binding='binding', versions='v1', now=NOW)


def test_restore_without_source_or_fake_revision(tmp_path, monkeypatch):
    state, record = setup(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError('filesystem accessed while restoring cache')

    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(Path, 'lstat', forbidden)
    document = restore(state, record)
    assert document.revision == ''
    assert document.view.body == record.body
    assert 'cache_unchecked' in document.view.warnings
    assert document.outline.get_section('s1').content == record.body


def test_unsigned_or_tampered_record_rejected(tmp_path):
    state, record = setup(tmp_path)
    for bad in (replace(record, auth_tag=None), replace(record, body='forged')):
        with pytest.raises(CacheError, match='CACHE_AUTH_INVALID'):
            restore(state, bad)


def test_skill_outside_project_scope(tmp_path):
    state, record = setup(tmp_path, 'examples/SKILL')
    with pytest.raises(CacheError, match='CACHE_SCOPE_INVALID'):
        restore(state, record)


def test_changed_versions_rejected(tmp_path):
    state, record = setup(tmp_path)
    with pytest.raises(CacheError, match='CACHE_CONTEXT_INVALID'):
        restore(state, replace(record, versions='v2'))
