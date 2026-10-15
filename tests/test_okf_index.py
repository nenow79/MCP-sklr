"""C2b: indeks w pamięci na sztucznych danych; bez search, MCP i produkcyjnej wiedzy."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from okf_engine.bundles import evaluate_bundles
from okf_engine.documents import DocumentError, DocumentReader
from okf_engine.index import KnowledgeIndex
from okf_engine.limits import KnowledgeLimits
from okf_engine.revisions import RevisionSigner
from okf_engine.scanner import scan_bundle
from roots import Root

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
STAMP = datetime(2026, 6, 2, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    root = Root('test', str(tmp_path.resolve()), 'ro')
    config = {'bundles': [{'id': 'general', 'label': 'Test', 'scope': 'central', 'root': root.alias,
                           'root_path': root.path, 'subpath': '', 'enabled': True}]}

    def bundle(bundle_id):
        return evaluate_bundles(config, [root])[0]

    reader = DocumentReader(bundle, lambda state, path: True, RevisionSigner(b'a' * 32),
                            KnowledgeLimits(), lambda: NOW)
    return tmp_path, config, reader, KnowledgeIndex(lambda: STAMP)


def write(path, body='# Title\ntext\n', metadata='type: Reference\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(('---\n' + metadata + '---\n' + body).encode())


def ids(generation):
    return [entry.document.view.identity.concept_id for entry in generation.entries]


def publish(index, reader, ticket=None, limits=None):
    limits = limits or reader.limits
    ticket = index.begin('general') if ticket is None else ticket
    scan = scan_bundle(reader, 'general', limits)
    return index.publish(ticket, reader, 'general', scan, limits)


def test_empty_state(env):
    _, _, _, index = env
    state = index.state('general')
    assert (state.state, state.previous_state, state.indexed_documents, state.indexed_at) == ('empty', None, 0, None)
    assert index.snapshot('general') is None
    assert not index.remove_document('general', 'a')


def test_publish_ready_generation(env):
    base, _, reader, index = env
    write(base / 'b.md')
    write(base / 'a.md')
    ticket = index.begin('general')
    refreshing = index.state('general')
    assert refreshing.state == 'refreshing' and refreshing.previous_state == 'empty'
    scan = scan_bundle(reader, 'general', reader.limits)
    assert index.publish(ticket, reader, 'general', scan, reader.limits)
    state = index.state('general')
    assert (state.state, state.indexed_documents, state.indexed_at, state.reasons) == ('ready', 2, STAMP, ())
    assert ids(index.snapshot('general')) == ['a', 'b']
    assert index.snapshot('general').text_bytes > 0


def test_partial_scan_publishes_verified_subset(env):
    base, _, reader, index = env
    for name in ('a', 'b', 'c'):
        write(base / (name + '.md'))
    limits = replace(reader.limits, max_documents_per_bundle=2)
    assert publish(index, reader, limits=limits)
    state = index.state('general')
    assert state.state == 'partial' and state.indexed_documents == 2 and state.reasons == ('document_limit',)


def test_scan_errors_are_excluded_not_indexed(env):
    base, _, reader, index = env
    write(base / 'ok.md')
    (base / 'bad.md').write_bytes(b'not okf')
    assert publish(index, reader)
    state = index.state('general')
    assert state.state == 'ready' and state.indexed_documents == 1 and state.excluded_documents == 1


def test_text_budget_is_explicit(env):
    base, _, reader, index = env
    for name in ('a', 'b', 'c'):
        write(base / (name + '.md'), 'word ' * 120000)
    limits = replace(reader.limits, max_index_text_bytes=1048576)
    assert publish(index, reader, limits=limits)
    state = index.state('general')
    assert state.state == 'partial' and state.reasons == ('index_text_limit',)
    assert state.indexed_documents == 1 and state.excluded_documents == 2
    assert index.snapshot('general').text_bytes <= 1048576


def test_document_removed_before_publish_is_dropped(env):
    base, _, reader, index = env
    write(base / 'a.md')
    write(base / 'b.md')
    ticket = index.begin('general')
    scan = scan_bundle(reader, 'general', reader.limits)
    (base / 'b.md').unlink()
    assert index.publish(ticket, reader, 'general', scan, reader.limits)
    state = index.state('general')
    assert state.state == 'partial' and state.reasons == ('document_unavailable',)
    assert ids(index.snapshot('general')) == ['a'] and state.excluded_documents == 1


def test_access_lost_before_publish_clears_index(env):
    base, config, reader, index = env
    write(base / 'a.md')
    assert publish(index, reader)
    ticket = index.begin('general')
    scan = scan_bundle(reader, 'general', reader.limits)
    config['bundles'][0]['enabled'] = False
    with pytest.raises(DocumentError) as caught:
        index.publish(ticket, reader, 'general', scan, reader.limits)
    assert caught.value.code == 'BUNDLE_UNAVAILABLE'
    state = index.state('general')
    assert state.state == 'empty' and state.indexed_documents == 0 and index.snapshot('general') is None


def test_access_lost_during_verification_clears_index(env):
    base, config, reader, index = env
    write(base / 'a.md')
    ticket = index.begin('general')
    scan = scan_bundle(reader, 'general', reader.limits)
    real = reader.check_document

    def lose_access(document):
        config['bundles'][0]['enabled'] = False
        real(document)

    reader.check_document = lose_access
    with pytest.raises(DocumentError):
        index.publish(ticket, reader, 'general', scan, reader.limits)
    assert index.state('general').state == 'empty' and index.snapshot('general') is None


def test_stale_ticket_cannot_publish(env):
    base, _, reader, index = env
    write(base / 'a.md')
    old = index.begin('general')
    new = index.begin('general')
    scan = scan_bundle(reader, 'general', reader.limits)
    assert not index.publish(old, reader, 'general', scan, reader.limits)
    assert index.state('general').state == 'refreshing' and index.snapshot('general') is None
    assert index.publish(new, reader, 'general', scan, reader.limits)
    assert index.state('general').state == 'ready'


def test_invalidate_during_scan_blocks_late_publish(env):
    base, _, reader, index = env
    write(base / 'a.md')
    ticket = index.begin('general')
    scan = scan_bundle(reader, 'general', reader.limits)
    index.invalidate('general')
    assert not index.publish(ticket, reader, 'general', scan, reader.limits)
    assert index.state('general').state == 'empty' and index.snapshot('general') is None


def test_invalidate_during_verification_blocks_late_publish(env):
    base, _, reader, index = env
    write(base / 'a.md')
    ticket = index.begin('general')
    scan = scan_bundle(reader, 'general', reader.limits)
    real = reader.check_document

    def invalidate_then_check(document):
        index.invalidate('general')
        real(document)

    reader.check_document = invalidate_then_check
    assert not index.publish(ticket, reader, 'general', scan, reader.limits)
    assert index.snapshot('general') is None


def test_failure_keeps_previous_generation_with_error_state(env):
    base, _, reader, index = env
    write(base / 'a.md')
    assert publish(index, reader)
    ticket = index.begin('general')
    state = index.state('general')
    assert state.state == 'refreshing' and state.previous_state == 'ready' and state.indexed_documents == 1
    assert index.finish_failed(ticket, 'general', 'SCAN_FAILED')
    state = index.state('general')
    assert (state.state, state.previous_state, state.error) == ('error', 'ready', 'SCAN_FAILED')
    assert ids(index.snapshot('general')) == ['a']
    assert not index.finish_failed(ticket - 1, 'general', 'OTHER')
    assert index.state('general').error == 'SCAN_FAILED'


def test_republish_replaces_generation_atomically(env):
    base, _, reader, index = env
    write(base / 'a.md')
    write(base / 'b.md')
    assert publish(index, reader)
    old = index.snapshot('general')
    (base / 'b.md').unlink()
    write(base / 'c.md')
    assert publish(index, reader)
    assert ids(index.snapshot('general')) == ['a', 'c']
    assert ids(old) == ['a', 'b']


def test_remove_document_marks_partial(env):
    base, _, reader, index = env
    write(base / 'a.md')
    write(base / 'b.md')
    assert publish(index, reader)
    assert index.remove_document('general', 'a')
    assert not index.remove_document('general', 'a')
    state = index.state('general')
    assert state.state == 'partial' and state.reasons == ('document_unavailable',)
    assert ids(index.snapshot('general')) == ['b'] and state.excluded_documents == 1


def test_prepare_runs_on_public_documents(env):
    base, _, reader, _ = env
    write(base / 'Orders.md', 'password: secretvalue\n')
    seen = []
    index = KnowledgeIndex(lambda: STAMP, prepare=lambda document: seen.append(document.view.body) or 'prepared')
    assert publish(index, reader)
    assert index.snapshot('general').entries[0].prepared == 'prepared'
    assert seen and 'secretvalue' not in seen[0]


def test_public_data_only_and_safe_repr(env):
    base, _, reader, index = env
    write(base / 'a.md', 'password: secretvalue\n', 'type: Reference\napi_key: metavalue\n')
    assert publish(index, reader)
    generation = index.snapshot('general')
    body = generation.entries[0].document.view.body
    assert 'secretvalue' not in body and 'metavalue' not in repr(generation.entries[0].document.view.metadata)
    for value in (repr(generation), repr(generation.entries[0]), repr(index.state('general'))):
        assert 'secretvalue' not in value and 'metavalue' not in value
        assert str(base) not in value


def test_bundles_are_independent(env):
    base, _, reader, index = env
    write(base / 'a.md')
    assert publish(index, reader)
    index.invalidate('other')
    assert index.state('general').state == 'ready' and index.state('other').state == 'empty'
