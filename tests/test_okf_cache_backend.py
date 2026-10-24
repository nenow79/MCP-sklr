"""Backend na tmp_path i rzeczywistym readerze; bez produkcyjnych danych."""
from datetime import datetime, timezone
import threading

import pytest

from roots import Root
from okf_engine.bundles import evaluate_bundles
from okf_engine.cache_backend import CacheRefreshBackend
from okf_engine.documents import DocumentReader, DocumentError
from okf_engine.limits import KnowledgeLimits
from okf_engine.revisions import RevisionSigner


def env(tmp_path):
    docs = tmp_path / 'docs'
    docs.mkdir()
    root = Root('test', str(docs.resolve()), 'ro')
    config = {'bundles': [{'id': 'general', 'label': 'Test', 'scope': 'central',
                          'root': root.alias, 'root_path': root.path,
                          'subpath': '', 'enabled': True}]}
    reader = DocumentReader(lambda name: evaluate_bundles(config, [root])[0],
                            lambda state, path: True, RevisionSigner(b'a' * 32),
                            KnowledgeLimits(), lambda: datetime.now(timezone.utc))
    for name in ('a', 'b'):
        (docs / (name + '.md')).write_text('---\ntype: Reference\n---\n# Title\n' + name,
                                         encoding='utf-8')
    return docs, config, reader


def test_cold_warm_restart_and_one_changed_document(tmp_path, monkeypatch):
    from okf_engine import documents as module
    docs, _, reader = env(tmp_path)
    parser = module.parse_document
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[1].concept_id)
        return parser(*args, **kwargs)

    monkeypatch.setattr(module, 'parse_document', counted)
    path = tmp_path / 'cache.sqlite3'
    backend = CacheRefreshBackend(path, b'k' * 32, batch_size=1)
    try:
        result = backend.run(reader, 'general', reader.limits, lambda: 0)
        assert not result.partial and len(result.documents) == 2
        assert calls == ['a', 'b']
        calls.clear()
        result = backend.run(reader, 'general', reader.limits, lambda: 0)
        assert len(result.documents) == 2 and calls == []
    finally:
        assert backend.close()
    backend = CacheRefreshBackend(path, b'k' * 32, batch_size=1)
    try:
        result = backend.run(reader, 'general', reader.limits, lambda: 0)
        assert len(result.documents) == 2 and calls == []
        (docs / 'a.md').write_text('---\ntype: Reference\n---\nchanged', encoding='utf-8')
        result = backend.run(reader, 'general', reader.limits, lambda: 0)
        assert calls == ['a'] and 'changed' in result.documents[0].view.body
        (docs / 'b.md').unlink()
        result = backend.run(reader, 'general', reader.limits, lambda: 0)
        assert [doc.view.identity.concept_id for doc in result.documents] == ['a']
    finally:
        assert backend.close()


def test_restore_uses_sqlite_without_reading_sources(tmp_path, monkeypatch):
    _, _, reader = env(tmp_path)
    path = tmp_path / 'cache.sqlite3'
    backend = CacheRefreshBackend(path, b'k' * 32, batch_size=1)
    try:
        assert len(backend.run(reader, 'general', reader.limits, lambda: 0).documents) == 2
    finally:
        assert backend.close()

    def forbidden(*args, **kwargs):
        raise AssertionError('source read during cache restore')

    monkeypatch.setattr(reader, 'read_bytes', forbidden)
    monkeypatch.setattr(reader, 'build_document', forbidden)
    monkeypatch.setattr(reader, 'check_document', forbidden)
    backend = CacheRefreshBackend(path, b'k' * 32, batch_size=1)
    try:
        result = backend.restore(reader, 'general', reader.limits, lambda: 0)
        assert len(result.documents) == 2 and result.partial
        assert 'cache_unchecked' in result.reasons
        assert all(doc.revision == '' for doc in result.documents)
    finally:
        assert backend.close()


def test_update_reads_only_target_without_inventory(tmp_path, monkeypatch):
    from okf_engine import cache_backend as module
    _, _, reader = env(tmp_path)
    reads = []
    original = reader.read_bytes

    def counted(bundle, concept):
        reads.append(concept)
        return original(bundle, concept)

    def forbidden(*args, **kwargs):
        raise AssertionError('full inventory during incremental update')

    monkeypatch.setattr(reader, 'read_bytes', counted)
    monkeypatch.setattr(module, '_inventory', forbidden)
    backend = CacheRefreshBackend(tmp_path / 'cache.sqlite3', b'k' * 32)
    try:
        result = backend.update(reader, 'general', reader.limits, lambda: 0, ['a'])
        assert reads == ['a']
        assert [doc.view.identity.concept_id for doc in result.documents] == ['a']
        assert not result.partial
    finally:
        assert backend.close()


def test_document_errors_include_safe_code_and_stage(tmp_path, monkeypatch):
    _, _, reader = env(tmp_path)
    original = reader.build_document

    def changed(source):
        if source.identity.concept_id == 'a':
            raise DocumentError('DOCUMENT_CHANGED')
        return original(source)

    monkeypatch.setattr(reader, 'build_document', changed)
    backend = CacheRefreshBackend(tmp_path / 'cache.sqlite3', b'k' * 32)
    try:
        result = backend.run(reader, 'general', reader.limits, lambda: 0)
        assert result.partial
        assert result.errors == {'DOCUMENT_CHANGED': 1}
        assert result.error_stages == {'build_document.DOCUMENT_CHANGED': 1}
        assert len(result.documents) == 1
    finally:
        assert backend.close()


def test_revoked_bundle_aborts_backend(tmp_path):
    _, config, reader = env(tmp_path)
    backend = CacheRefreshBackend(tmp_path / 'cache.sqlite3', b'k' * 32)
    config['bundles'][0]['enabled'] = False
    try:
        with pytest.raises(DocumentError, match='BUNDLE_UNAVAILABLE'):
            backend.run(reader, 'general', reader.limits, lambda: 0)
    finally:
        assert backend.close()


@pytest.mark.parametrize('first', ['worker', 'waiter'])
@pytest.mark.parametrize('safe_error', [False, True])
def test_cancel_clock_does_not_leave_waiter_blocked(tmp_path, monkeypatch, first, safe_error):
    _, _, reader = env(tmp_path)
    backend = CacheRefreshBackend(tmp_path / 'cache.sqlite3', b'k' * 32)

    caller = threading.current_thread()
    entered, release = threading.Event(), threading.Event()

    def cancelled():
        if threading.current_thread() is not caller:
            entered.set()
            if first == 'waiter':
                assert release.wait(5)
                return 0
        else:
            assert first == 'waiter'
            assert entered.wait(5)
        if safe_error:
            raise DocumentError('CACHE_CANCELLED')
        raise RuntimeError('PRIVATE_SENTINEL')

    if first == 'worker':
        submit = backend._worker.submit

        def submit_and_finish(name):
            ticket = submit(name)
            assert backend._jobs[name].done.wait(5)
            return ticket

        monkeypatch.setattr(backend._worker, 'submit', submit_and_finish)

    try:
        code = 'CACHE_CANCELLED' if safe_error else 'CACHE_REFRESH_FAILED'
        with pytest.raises(DocumentError) as raised:
            backend.run(reader, 'general', reader.limits, cancelled)
        assert raised.value.code == code
        assert 'PRIVATE_SENTINEL' not in str(raised.value)
        assert backend._jobs == {}
        release.set()
        if first == 'worker':
            monkeypatch.setattr(backend._worker, 'submit', submit)
        # Ten sam worker nadal obsługuje kolejne zadanie po anulowaniu.
        result = backend.run(reader, 'general', reader.limits, lambda: 0)
        assert len(result.documents) == 2 and not result.partial
    finally:
        release.set()
        assert backend.close()
