"""Cache hit na sztucznym pliku; parser blokowany podczas trafienia."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from roots import Root
from okf_engine.bundles import evaluate_bundles
from okf_engine.cache_hit import cache_hit, record_tag
from okf_engine.cache_store import CacheError, fingerprint, record_from_public
from okf_engine.documents import DocumentReader, DocumentError
from okf_engine.limits import KnowledgeLimits
from okf_engine.revisions import RevisionSigner

KEY = b'k' * 32


@pytest.fixture
def env(tmp_path):
    root = Root('test', str(tmp_path.resolve()), 'ro')
    config = {'bundles': [{'id': 'general', 'label': 'Test', 'scope': 'central',
                          'root': root.alias, 'root_path': root.path,
                          'subpath': '', 'enabled': True}]}
    allowed = [True]
    reader = DocumentReader(lambda name: evaluate_bundles(config, [root])[0],
                            lambda state, path: allowed[0], RevisionSigner(b'a' * 32),
                            KnowledgeLimits(), lambda: datetime.now(timezone.utc))
    path = tmp_path / 'note.md'
    path.write_bytes(b'---\ntype: Reference\n---\n# Title\n## Detail\npassword: SECRET_SENTINEL\n')
    source = reader.read_bytes('general', 'note')
    document = reader.build_document(source)
    record = record_from_public(document.view, 'binding', 'v1', source.snapshot[:4],
                                fingerprint(source.raw, KEY))
    return reader, source, record, record_tag(record, KEY), allowed, path


def hit(env, **kwargs):
    reader, source, record, tag, _, _ = env
    return cache_hit(reader, source, kwargs.get('record', record), kwargs.get('tag', tag),
                     KEY, binding='binding', versions='v1')


def test_hit_does_not_parse_and_revision_is_fresh(env, monkeypatch):
    from okf_engine import documents as module
    reader, source, _, _, _, _ = env
    expected = reader.build_document(source)

    def forbidden(*args, **kwargs):
        raise AssertionError('parser invoked on cache hit')

    monkeypatch.setattr(module, 'parse_document', forbidden)
    restored = hit(env)
    assert restored.revision == expected.revision
    assert restored.view.body == expected.view.body
    assert 'SECRET_SENTINEL' not in restored.view.body
    assert restored.view.masked
    reader.signer = RevisionSigner(b'b' * 32)
    assert hit(env).revision != expected.revision


def test_payload_tampering_rejected(env):
    record = replace(env[2], body='# Forged\n')
    with pytest.raises(CacheError, match='CACHE_AUTH_INVALID'):
        hit(env, record=record)


def test_source_change_rejected(env):
    reader, _, _, _, _, path = env
    path.write_bytes(b'---\ntype: Reference\n---\nchanged\n')
    updated = (reader, reader.read_bytes('general', 'note'), *env[2:])
    with pytest.raises(CacheError, match='CACHE_SOURCE_CHANGED'):
        hit(updated)


def test_access_revoked_and_missing_tag_rejected(env):
    with pytest.raises(CacheError, match='CACHE_AUTH_INVALID'):
        hit(env, tag='')
    env[4][0] = False
    with pytest.raises(DocumentError, match='DOCUMENT_UNAVAILABLE'):
        hit(env)
