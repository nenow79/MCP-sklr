"""Pipeline SQLite + worker na tmp_path, bez serwera i rewizji API."""
import threading
from datetime import datetime, timezone

from okf_engine.cache_pipeline import CachePipeline
from okf_engine.cache_store import record_from_public, fingerprint
from okf_engine.models import DocumentIdentity
from okf_engine.parser import parse_document
from okf_engine.public_view import build_public_view


def record(concept, text):
    raw = ('---\ntype: Reference\n---\n# Title\n' + text).encode()
    view = build_public_view(parse_document(raw, DocumentIdentity('test', concept)),
                             datetime.now(timezone.utc))
    return record_from_public(view, 'binding', 'v1', (1, 2, len(raw), 100),
                              fingerprint(raw, b'k' * 32))


def make(path, source, reads, published, ready, allowed=None):
    def read(context, concept):
        reads.append(concept)
        return source.get(concept)

    def publish(context, records, valid):
        assert valid()
        published.append(records)
        ready.set()

    return CachePipeline(path, lambda key: ('test', 'binding', 'v1'),
                         lambda context: True if allowed is None else allowed[0],
                         read, publish, batch_size=1)


def test_restart_loads_cache_and_edit_reads_one_document(tmp_path):
    path = tmp_path / 'cache.sqlite3'
    source = {'a': record('a', 'first'), 'b': record('b', 'second')}
    reads, published, ready = [], [], threading.Event()
    pipeline = make(path, source, reads, published, ready)
    pipeline.start()
    try:
        pipeline.submit('bundle', ['a', 'b'])
        assert ready.wait(5)
    finally:
        assert pipeline.close()
    assert reads == ['a', 'b'] and len(published[-1]) == 2
    reads.clear()
    ready.clear()
    pipeline = make(path, source, reads, published, ready)
    pipeline.start()
    try:
        pipeline.submit('bundle')
        assert ready.wait(5)
        assert reads == [] and len(published[-1]) == 2
        ready.clear()
        source['a'] = record('a', 'changed')
        pipeline.submit('bundle', ['a'])
        assert ready.wait(5)
        assert reads == ['a']
        assert 'changed' in published[-1][0].body
        ready.clear()
        del source['b']
        pipeline.submit('bundle', ['b'])
        assert ready.wait(5)
        assert [r.identity.concept_id for r in published[-1]] == ['a']
    finally:
        assert pipeline.close()


def test_revoked_access_blocks_publication(tmp_path):
    entered, release = threading.Event(), threading.Event()
    published = []
    allowed = [True]

    def read(context, concept):
        entered.set()
        assert release.wait(5)
        return record(concept, 'text')

    pipeline = CachePipeline(tmp_path / 'cache.sqlite3',
                             lambda key: ('test', 'binding', 'v1'),
                             lambda context: allowed[0], read,
                             lambda *args: published.append(args))
    pipeline.start()
    try:
        pipeline.submit('bundle', ['a'])
        assert entered.wait(5)
        allowed[0] = False
        release.set()
    finally:
        release.set()
        assert pipeline.close()
    assert published == []
