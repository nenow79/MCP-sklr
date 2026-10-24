"""C2b: wyszukiwanie na sztucznych danych; bez MCP, TaskManager i produkcyjnej wiedzy."""
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from okf_engine.bundles import evaluate_bundles
from okf_engine.documents import DocumentReader
from okf_engine.index import KnowledgeIndex
from okf_engine.limits import KnowledgeLimits
from okf_engine.revisions import RevisionSigner
from okf_engine.scanner import scan_bundle
from okf_engine.search import SearchError, parse_request, prepare_document, search
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
    index = KnowledgeIndex(lambda: STAMP, prepare=prepare_document)
    return tmp_path, config, reader, index


def write(path, body='# T\ntext\n', metadata='type: Reference\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(('---\n' + metadata + '---\n' + body).encode())


def publish(index, reader, limits=None):
    limits = limits or reader.limits
    ticket = index.begin('general')
    scan = scan_bundle(reader, 'general', limits)
    assert index.publish(ticket, reader, 'general', scan, limits)
    return scan


def make_request(**kwargs):
    params = {'explicit_bundle': True}
    params.update(kwargs)
    return parse_request(**params)


def find(index, reader, **kwargs):
    return search(index, reader, ['general'], make_request(**kwargs), 2)


def ids(result):
    return [hit.concept_id for hit in result.hits]


def publish_unchecked(index, reader):
    from okf_engine.scanner import ScanResult
    scan = scan_bundle(reader, 'general', reader.limits)
    documents = tuple(replace(doc, revision='', view=replace(
        doc.view, warnings=doc.view.warnings + ('cache_unchecked',))) for doc in scan.documents)
    ticket = index.begin('general')
    assert index.publish(ticket, reader, 'general',
                         ScanResult(documents, False, (), {}, {}), reader.limits)
    return scan


def test_unchecked_cache_returns_fresh_section_and_revision(env, monkeypatch):
    base, _, reader, index = env
    write(base / 'a.md', '# Old\nalpha old\n')
    write(base / 'b.md', '# Other\nbeta\n')
    scan = publish_unchecked(index, reader)
    assert not scan.partial and not scan.errors and len(scan.documents) == 2, {
        'stage': 'scan', 'partial': scan.partial, 'reasons': scan.reasons,
        'errors': scan.errors, 'documents': len(scan.documents)}
    write(base / 'a.md', '# Intro\nother\n# Current\nalpha fresh\n')
    expected = reader.read('general', 'a')
    original = reader.read
    reads = []

    def counted(bundle, concept):
        reads.append(concept)
        return original(bundle, concept)

    monkeypatch.setattr(reader, 'read', counted)
    result = find(index, reader, query='alpha')
    assert reads == ['a'] and ids(result) == ['a'], {
        'stage': 'search', 'partial': result.partial, 'truncated': result.truncated,
        'reasons': result.reasons, 'hits': len(result.hits)}
    hit = result.hits[0]
    assert hit.revision == expected.revision and hit.section_id == 's2'
    assert hit.heading == 'Current' and 'fresh' in hit.excerpt
    assert 'old' not in hit.excerpt
    assert result.partial and 'cache_unchecked' in result.reasons


def test_unchecked_cache_drops_no_longer_matching_candidate(env):
    base, _, reader, index = env
    write(base / 'a.md', '# Old\nalpha\n')
    publish_unchecked(index, reader)
    write(base / 'a.md', '# Current\nbeta\n')
    result = find(index, reader, query='alpha')
    assert result.hits == () and 'cache_source_changed' in result.reasons
    assert index.snapshot('general') is not None  # brak dopasowania nie jest utratą dostępu


def test_unchecked_cache_rechecks_metadata_filter(env):
    base, _, reader, index = env
    write(base / 'a.md', '# T\nalpha\n', 'type: Reference\ntags: [old]\n')
    publish_unchecked(index, reader)
    write(base / 'a.md', '# T\nalpha\n', 'type: Specification\ntags: [new]\n')
    result = find(index, reader, query='alpha', type='Reference', tags=['old'])
    assert result.hits == () and 'cache_source_changed' in result.reasons


def test_unchecked_cache_rechecks_denied_access(env):
    base, _, reader, index = env
    write(base / 'a.md', '# T\nalpha\n')
    publish_unchecked(index, reader)
    reader.authorize = lambda state, path: False
    result = find(index, reader, query='alpha')
    assert result.hits == () and 'document_unavailable' in result.reasons


def test_unchecked_read_cannot_return_after_search_budget(env, monkeypatch):
    base, _, reader, index = env
    write(base / 'a.md', '# T\nalpha\n')
    publish_unchecked(index, reader)
    seconds = [0]
    original = reader.read

    def slow(bundle, concept):
        document = original(bundle, concept)
        seconds[0] += 3
        return document

    monkeypatch.setattr(reader, 'read', slow)
    result = search(index, reader, ['general'], make_request(query='alpha'), 2,
                    clock=lambda: seconds[0])
    assert result.hits == () and 'search_budget' in result.reasons
    assert result.partial and result.truncated


# --- walidacja argumentów ---

INVALID = [
    {'query': 5}, {'query': 'a' * 1001}, {'query': 'x', 'query_mode': 'regex'},
    {'query': 'x', 'browse': 'yes'}, {'query': 'secretquery', 'browse': True},
    {'browse': True, 'explicit_bundle': False}, {}, {'query': '***'}, {'query': '   '},
    {'query': '***', 'query_mode': 'phrase'}, {'query': 'x', 'type': ''}, {'query': 'x', 'type': 5},
    {'query': 'x', 'status': '  '}, {'query': 'x', 'tags': 'a'}, {'query': 'x', 'tags': [5]},
    {'query': 'x', 'tags': ['']}, {'query': 'x', 'tags': ['t' * 101]}, {'query': 'x', 'tags': ['t'] * 21},
    {'query': 'x', 'include_deprecated': 1}, {'query': 'x', 'limit': 0}, {'query': 'x', 'limit': 51},
    {'query': 'x', 'limit': True}, {'query': 'x', 'limit': '5'}, {'query': 'x', 'max_total_chars': 999},
    {'query': 'x', 'max_total_chars': 80001}, {'query': 'x', 'max_total_chars': False},
]


@pytest.mark.parametrize('kwargs', INVALID)
def test_invalid_arguments(kwargs):
    with pytest.raises(SearchError) as caught:
        parse_request(**kwargs)
    assert caught.value.code == 'INVALID_ARGUMENT'
    assert 'secretquery' not in str(caught.value)


def test_request_normalization():
    parsed = parse_request(query='  ZAŻÓŁĆ  gęślą zażółć ')
    assert parsed.terms == ('zażółć', 'gęślą') and parsed.mode == 'terms'
    assert 'zażółć' not in repr(parsed)
    phrase = parse_request(query='Red   FOX', query_mode='phrase')
    assert phrase.phrase == 'red fox' and phrase.terms == ()
    browse = parse_request(browse=True, type=' Reference ', tags=['SAP', ' Red'], status='Draft')
    assert (browse.type, browse.status) == ('reference', 'draft')
    assert browse.tags == frozenset({'sap', 'red'})
    assert parse_request(browse=True, explicit_bundle=True).browse


# --- ranking i sekcje ---

def test_ranking_by_field_weights(env):
    base, _, reader, index = env
    write(base / 'title_doc.md', metadata='type: Reference\ntitle: Alpha guide\n')
    write(base / 'tags_doc.md', metadata='type: Reference\ntags: [alpha]\n')
    write(base / 'desc_doc.md', metadata='type: Reference\ndescription: alpha notes\n')
    write(base / 'head_doc.md', '# Alpha heading\ntext\n')
    write(base / 'body_doc.md', '# Plain\nalpha text\n')
    scan = publish(index, reader)
    assert not scan.partial and not scan.errors and len(scan.documents) == 5, {
        'stage': 'scan', 'partial': scan.partial, 'reasons': scan.reasons,
        'errors': scan.errors, 'documents': len(scan.documents)}
    result = find(index, reader, query='alpha')
    assert ids(result) == ['title_doc', 'tags_doc', 'desc_doc', 'head_doc', 'body_doc'], {
        'stage': 'search', 'partial': result.partial, 'truncated': result.truncated,
        'reasons': result.reasons, 'hits': len(result.hits)}
    by_id = {hit.concept_id: hit for hit in result.hits}
    assert by_id['tags_doc'].section_id is None and by_id['tags_doc'].excerpt == ''
    assert by_id['desc_doc'].excerpt == 'alpha notes'
    body_hit = by_id['body_doc']
    assert (body_hit.section_id, body_hit.heading, body_hit.start_line, body_hit.end_line) == ('s1', 'Plain', 4, 5)
    assert body_hit.excerpt == 'alpha text' and body_hit.scope == 'central'
    assert (body_hit.type, body_hit.status, body_hit.trust_tier, body_hit.stale) == (
        'Reference', 'stable', 'unverified', None)
    assert body_hit.warnings == () and body_hit.tags == () and not body_hit.masked
    assert body_hit.revision.startswith('r1.') and not body_hit.content_truncated
    assert not result.partial and not result.truncated and result.reasons == ()


def test_best_section_and_lines(env):
    base, _, reader, index = env
    write(base / 'multi.md', '# One\nalpha\n# Two\nalpha beta\n')
    write(base / 'spread.md', '# One\nalpha\n# Two\nbeta\n')
    publish(index, reader)
    result = find(index, reader, query='alpha beta')
    by_id = {hit.concept_id: hit for hit in result.hits}
    multi = by_id['multi']
    assert (multi.section_id, multi.heading, multi.start_line, multi.end_line) == ('s2', 'Two', 6, 7)
    assert multi.excerpt == 'alpha beta'
    assert ids(result) == ['multi', 'spread']
    assert by_id['spread'].section_id == 's1'  # remis sekcji: wcześniejsza


def test_ties_use_bundle_and_concept_order(env):
    base, _, reader, index = env
    for name in ('c', 'a', 'b'):
        write(base / (name + '.md'), '# T\nalpha\n')
    publish(index, reader)
    assert ids(find(index, reader, query='alpha')) == ['a', 'b', 'c']


def test_many_sections_are_prepared_once(env):
    base, _, reader, index = env
    body = ''.join(f'# H{i}\ntext\n' for i in range(1999)) + '# Last\nneedle\n'
    write(base / 'big.md', body)
    publish(index, reader)
    hit = find(index, reader, query='needle').hits[0]
    assert (hit.section_id, hit.heading, hit.excerpt) == ('s2000', 'Last', 'needle')


# --- phrase, normalizacja ---

def test_phrase_mode_respects_field_boundaries(env):
    base, _, reader, index = env
    write(base / 'p1.md', '# T\nthe red fox jumps\n')
    write(base / 'p2.md', '# T\nthe red\nfox jumps\n')
    write(base / 'p3.md', '# T\nfox red\n')
    write(base / 'p4.md', '# T\nnothing\n', 'type: Reference\ntitle: Red\ndescription: fox thing\n')
    write(base / 'p5.md', '# Red\nfox\n')
    publish(index, reader)
    assert ids(find(index, reader, query='Red   FOX', query_mode='phrase')) == ['p1', 'p2']


def test_nfc_casefold_and_polish_letters(env):
    base, _, reader, index = env
    write(base / 'pl.md', '# T\nZażółć gęślą jaźń\n')
    write(base / 'pl2.md', '# T\nZaz\u0307o\u0301\u0142c\u0301\n')
    publish(index, reader)
    assert ids(find(index, reader, query='ZAŻÓŁĆ')) == ['pl', 'pl2']
    assert ids(find(index, reader, query='zaz\u0307o\u0301\u0142c\u0301')) == ['pl', 'pl2']
    assert ids(find(index, reader, query='zazolc')) == []
    assert ids(find(index, reader, query='gęślą JAŹŃ', query_mode='phrase')) == ['pl']


def test_terms_are_whole_words(env):
    base, _, reader, index = env
    write(base / 'a.md', '# T\norders_table\n')
    publish(index, reader)
    assert ids(find(index, reader, query='orders table')) == ['a']
    assert ids(find(index, reader, query='order')) == []


# --- filtry, browse, deprecated ---

def test_filters_and_browse(env):
    base, _, reader, index = env
    write(base / 'f1.md', '# T\nkeyword\n', 'type: Reference\ntags: [sap, redlake]\n')
    write(base / 'f2.md', '# T\nplain\n', 'type: Procedure\ntags: [sap]\n')
    write(base / 'f3.md', '# T\nkeyword\n', 'type: reference\ntags: [redlake]\nstatus: draft\n')
    publish(index, reader)
    assert ids(find(index, reader, browse=True, type='REFERENCE')) == ['f1', 'f3']
    assert ids(find(index, reader, browse=True, tags=['SAP'])) == ['f1', 'f2']
    assert ids(find(index, reader, browse=True, tags=['sap', 'redlake'])) == ['f1']
    assert ids(find(index, reader, browse=True, status='draft')) == ['f3']
    assert ids(find(index, reader, browse=True, status='stable')) == ['f1', 'f2']
    assert ids(find(index, reader, browse=True, type='reference', status='stable')) == ['f1']
    assert ids(find(index, reader, browse=True, type='nothing')) == []
    assert ids(find(index, reader, query='keyword', tags=['sap'])) == ['f1']
    browse = find(index, reader, browse=True)
    assert ids(browse) == ['f1', 'f2', 'f3']
    assert all(hit.section_id is None and hit.excerpt == '' for hit in browse.hits)


def test_deprecated_policy(env):
    base, _, reader, index = env
    write(base / 'd1.md', '# T\nalpha\n')
    write(base / 'd2.md', '# T\nalpha\n', 'type: Reference\nstatus: deprecated\n')
    publish(index, reader)
    default = find(index, reader, query='alpha')
    assert ids(default) == ['d1'] and default.skipped_deprecated == 1
    included = find(index, reader, query='alpha', include_deprecated=True)
    assert ids(included) == ['d1', 'd2'] and included.skipped_deprecated == 0
    assert ids(find(index, reader, browse=True, status='deprecated')) == ['d2']
    explicit = find(index, reader, query='alpha', status='deprecated')
    assert ids(explicit) == ['d2'] and explicit.skipped_deprecated == 0


# --- maskowanie ---

def test_masked_values_are_not_searchable(env):
    base, _, reader, index = env
    body = ('# T\npassword: hiddenvalue\nvisible\n-----BEGIN PRIVATE KEY-----\nRAWKEYDATA\n'
            '-----END PRIVATE KEY-----\n')
    write(base / 'm.md', body, 'type: Reference\napi_key: metavalue\n')
    publish(index, reader)
    for query in ('hiddenvalue', 'metavalue', 'rawkeydata', 'zamaskowano'):
        assert ids(find(index, reader, query=query)) == [], query
    assert ids(find(index, reader, query='password: hiddenvalue', query_mode='phrase')) == []
    result = find(index, reader, query='password')
    assert ids(result) == ['m'] and result.hits[0].masked
    dumped = json.dumps([hit.to_dict() for hit in result.hits], ensure_ascii=False)
    assert '***' in result.hits[0].excerpt
    for raw in ('hiddenvalue', 'metavalue', 'RAWKEYDATA'):
        assert raw not in dumped
    assert ids(find(index, reader, query='visible')) == ['m']


# --- stan indeksu ---

def test_index_not_ready_is_explicit(env):
    _, _, reader, index = env
    result = find(index, reader, query='alpha')
    assert result.hits == () and result.partial and result.reasons == ('index_not_ready',)
    assert result.index_states['general'].state == 'empty'


def test_no_available_bundles(env):
    _, _, reader, index = env
    result = search(index, reader, [], make_request(query='alpha'), 2)
    assert result.hits == () and not result.partial and result.reasons == ('no_available_bundles',)


def test_ready_index_without_matches_has_no_reasons(env):
    base, _, reader, index = env
    write(base / 'a.md')
    publish(index, reader)
    result = find(index, reader, query='zzzz')
    assert result.hits == () and result.count == 0
    assert not result.partial and not result.truncated and result.reasons == ()


def test_partial_index_is_propagated(env):
    base, _, reader, index = env
    write(base / 'a.md', '# T\nalpha\n')
    write(base / 'b.md', '# T\nalpha\n')
    publish(index, reader, replace(reader.limits, max_documents_per_bundle=1))
    result = find(index, reader, query='alpha')
    assert ids(result) == ['a'] and result.partial and 'document_limit' in result.reasons


def test_index_without_prepare_is_still_searchable(env):
    base, _, reader, _ = env
    index = KnowledgeIndex(lambda: STAMP)
    write(base / 'a.md', '# T\nalpha\n')
    publish(index, reader)
    assert ids(find(index, reader, query='alpha')) == ['a']


# --- ponowna kontrola dostępu ---

def test_deleted_file_is_not_returned_and_leaves_index(env):
    base, _, reader, index = env
    for name in ('a', 'b', 'c'):
        write(base / (name + '.md'), '# T\nalpha\n')
    publish(index, reader)
    (base / 'a.md').unlink()
    result = find(index, reader, query='alpha', limit=2)
    assert ids(result) == ['b', 'c'] and result.partial and not result.truncated
    assert result.reasons == ('document_unavailable',)
    assert index.state('general').state == 'partial'
    assert [entry.document.view.identity.concept_id for entry in index.snapshot('general').entries] == ['b', 'c']


def test_disabled_bundle_returns_nothing_and_clears_index(env):
    base, config, reader, index = env
    write(base / 'a.md', '# T\nalpha\n')
    publish(index, reader)
    config['bundles'][0]['enabled'] = False
    result = find(index, reader, query='alpha')
    assert result.hits == () and result.partial and result.reasons == ('bundle_unavailable',)
    assert result.index_states['general'].state == 'empty' and index.snapshot('general') is None


def test_access_lost_between_scan_and_return(env):
    base, config, reader, index = env
    write(base / 'a.md', '# T\nalpha\n')
    publish(index, reader)
    real = reader.check_document

    def lose_access(document):
        config['bundles'][0]['enabled'] = False
        real(document)

    reader.check_document = lose_access
    result = find(index, reader, query='alpha')
    assert result.hits == () and 'bundle_unavailable' in result.reasons
    assert index.snapshot('general') is None


# --- limity i budżety ---

def test_result_limit_is_reported(env):
    base, _, reader, index = env
    for name in ('a', 'b', 'c'):
        write(base / (name + '.md'), '# T\nalpha\n')
    publish(index, reader)
    result = find(index, reader, query='alpha', limit=2)
    assert ids(result) == ['a', 'b'] and result.truncated and result.reasons == ('result_limit',)
    assert not result.partial


def test_response_budget_shrinks_excerpt_but_keeps_ids(env):
    base, _, reader, index = env
    description = ' '.join(['word'] * 100)
    body = '# H\n' + 'filler ' * 80 + 'needle ' + 'tail ' * 80 + '\n'
    write(base / 'a.md', body, 'type: Reference\ndescription: ' + description + '\n')
    publish(index, reader)
    full = find(index, reader, query='needle', max_total_chars=80000).hits[0]
    assert len(json.dumps([full.to_dict()], ensure_ascii=False)) > 1000
    hit = find(index, reader, query='needle', max_total_chars=1000).hits[0]
    assert hit.content_truncated and 0 < len(hit.excerpt) < len(full.excerpt)
    assert hit.excerpt.endswith('…')
    assert len(json.dumps([hit.to_dict()], ensure_ascii=False)) <= 1000
    assert (hit.concept_id, hit.revision, hit.section_id) == (full.concept_id, full.revision, full.section_id)


def test_hit_that_cannot_fit_is_skipped(env):
    base, _, reader, index = env
    tags = ', '.join('tag%02d' % i + 'x' * 95 for i in range(30))
    write(base / 'a-big.md', '# T\nneedle\n', 'type: Reference\ntags: [' + tags + ']\n')
    write(base / 'b-small.md', '# T\nneedle\n')
    publish(index, reader)
    result = find(index, reader, query='needle', max_total_chars=1000)
    assert ids(result) == ['b-small'] and result.truncated and 'response_budget' in result.reasons


def test_time_budget_stops_scan(env):
    base, _, reader, index = env
    write(base / 'a.md', '# T\nalpha\n')
    publish(index, reader)
    calls = iter([0])
    result = search(index, reader, ['general'], make_request(query='alpha'), 2, lambda: next(calls, 100))
    assert result.hits == () and result.partial and result.truncated and 'search_budget' in result.reasons


# --- bezpieczny wynik ---

def test_hit_dict_is_json_safe_and_repr_is_safe(env):
    base, _, reader, index = env
    write(base / 'a.md', '# T\nneedle text\n', 'type: Reference\ntags: [x]\n')
    publish(index, reader)
    result = find(index, reader, query='needle')
    hit = result.hits[0]
    data = hit.to_dict()
    assert list(data) == [
        'bundle_id', 'scope', 'concept_id', 'title', 'description', 'type', 'tags', 'status', 'trust_tier',
        'stale', 'warnings', 'section_id', 'heading', 'start_line', 'end_line', 'excerpt', 'revision',
        'masked', 'content_truncated', 'format']
    json.dumps(data, ensure_ascii=False)
    assert data['tags'] == ['x'] and data['warnings'] == []
    for text in (repr(hit), repr(result)):
        assert 'needle' not in text and hit.revision not in text
