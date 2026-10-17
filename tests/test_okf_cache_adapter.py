"""Adapter do prawdziwego indeksu RAM; tylko sztuczne dokumenty."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from okf_engine.cache_adapter import restore_document, publish_records
from okf_engine.cache_store import CacheError, fingerprint, record_from_public
from okf_engine.documents import PublicDocument
from okf_engine.index import KnowledgeIndex
from okf_engine.limits import KnowledgeLimits
from okf_engine.models import DocumentIdentity
from okf_engine.parser import parse_document
from okf_engine.public_view import build_public_view
from okf_engine.revisions import RevisionBinding, RevisionSigner
from okf_engine.search import prepare_document
from okf_engine.sections import build_outline

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
IDENTITY = DocumentIdentity('test', 'note')
BINDING = RevisionBinding('test', '/root', '/root/docs', '/root/docs/note.md')


def document(text='old', key=b'a' * 32):
    raw = ('---\ntype: Reference\n---\n# Title\n## Detail\n' + text).encode()
    view = build_public_view(parse_document(raw, IDENTITY), NOW)
    result = PublicDocument(view, build_outline(view.body, view.body_start_line),
                            RevisionSigner(key).sign(BINDING, raw), BINDING, None)
    record = record_from_public(view, 'binding', 'v1', (1, 2, len(raw), 100),
                                fingerprint(raw, b'k' * 32))
    return result, record


def test_restored_sections_use_fresh_restart_revision():
    old, record = document()
    restarted, _ = document(key=b'b' * 32)
    restored = restore_document(record, lambda identity: restarted)
    assert restored.revision == restarted.revision != old.revision
    assert restored.binding == BINDING
    assert restored.outline.get_section('s2', 'direct').content.endswith('old')


def test_changed_source_cannot_sign_cached_sections():
    _, record = document()
    changed, _ = document('new')
    with pytest.raises(CacheError, match='CACHE_SOURCE_CHANGED'):
        restore_document(record, lambda identity: changed)


def test_publish_into_existing_ram_index_and_report_changed_source():
    fresh, record = document()
    index = KnowledgeIndex(lambda: NOW, prepare=prepare_document)
    checked = []
    reader = SimpleNamespace(current_bundle=lambda bundle: None,
                             check_document=lambda doc: checked.append(doc.revision))
    ticket = index.begin('test')
    assert publish_records(index, ticket, reader, 'test', [record], KnowledgeLimits(),
                           lambda identity: fresh, valid=lambda: True)
    state = index.state('test')
    assert state.state == 'ready' and state.indexed_documents == 1
    entry = index.snapshot('test').entries[0]
    assert entry.prepared is not None and entry.document.revision == fresh.revision
    assert checked == [fresh.revision]
    changed, _ = document('new')
    ticket = index.begin('test')
    assert publish_records(index, ticket, reader, 'test', [record], KnowledgeLimits(),
                           lambda identity: changed)
    state = index.state('test')
    assert state.state == 'partial' and state.indexed_documents == 0
    assert state.excluded_documents == 1 and 'cache_source_changed' in state.reasons


def test_invalid_ticket_context_and_duplicates():
    fresh, record = document()
    index = KnowledgeIndex(lambda: NOW)
    reader = SimpleNamespace(current_bundle=lambda bundle: None, check_document=lambda doc: None)
    ticket = index.begin('test')
    assert publish_records(index, ticket, reader, 'test', [record], KnowledgeLimits(),
                           lambda identity: fresh, valid=lambda: False) is False
    assert index.snapshot('test') is None
    with pytest.raises(CacheError, match='CACHE_DUPLICATE_TARGET'):
        publish_records(index, ticket, reader, 'test', [record, record], KnowledgeLimits(),
                        lambda identity: fresh)
