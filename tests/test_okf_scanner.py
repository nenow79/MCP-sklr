"""C2b: skaner bundle na sztucznych plikach; bez indeksu, MCP i produkcyjnej wiedzy."""
import os
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import okf_engine.scanner as scanner
from okf_engine.bundles import evaluate_bundles
from okf_engine.documents import DocumentError, DocumentReader
from okf_engine.limits import KnowledgeLimits
from okf_engine.revisions import RevisionSigner
from okf_engine.scanner import scan_bundle
from roots import Root

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    root = Root('test', str(tmp_path.resolve()), 'ro')
    config = {'bundles': [{'id': 'general', 'label': 'Test', 'scope': 'central', 'root': root.alias,
                           'root_path': root.path, 'subpath': '', 'enabled': True}]}
    controls = {'denied': set()}

    def bundle(bundle_id):
        return evaluate_bundles(config, [root])[0]

    def authorize(state, path):
        return path.name not in controls['denied']

    reader = DocumentReader(bundle, authorize, RevisionSigner(b'a' * 32), KnowledgeLimits(), lambda: NOW)
    return tmp_path, config, controls, reader


def write(path, body='# Title\ntext\n', metadata='type: Reference\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(('---\n' + metadata + '---\n' + body).encode())


def ids(result):
    return [doc.view.identity.concept_id for doc in result.documents]


def make_link(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError, AttributeError):
        pass
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
        if result.returncode == 0:
            return
    pytest.skip('nie można utworzyć linku w tym środowisku')


def test_inventory_reserved_hidden_and_non_markdown(env):
    base, _, _, reader = env
    for name in ('a.md', 'sub/b.md', 'sub/deep/c.md', 'index.md', 'log.md', 'sub/Index.md', '.hidden/x.md'):
        write(base / name)
    (base / 'notes.txt').write_text('x', encoding='utf-8')
    result = scan_bundle(reader, 'general', reader.limits)
    assert ids(result) == ['a', 'sub/b', 'sub/deep/c']
    assert result.skipped == {'reserved': 3, 'hidden': 1}
    assert not result.partial and result.reasons == () and result.errors == {}


def test_read_errors_are_counted_without_content(env):
    base, _, controls, reader = env
    write(base / 'ok.md', 'password: hiddenvalue\n')
    (base / 'plain.md').write_bytes(b'no frontmatter hiddenvalue')
    (base / 'bytes.md').write_bytes(b'\xff')
    write(base / 'denied.md')
    controls['denied'].add('denied.md')
    result = scan_bundle(reader, 'general', reader.limits)
    assert ids(result) == ['ok']
    assert result.errors == {'INVALID_OKF': 2, 'DOCUMENT_UNAVAILABLE': 1}
    assert not result.partial
    assert 'hiddenvalue' not in result.documents[0].view.body
    assert 'hiddenvalue' not in repr(result) and 'ok.md' not in repr(result)


def test_document_limit_is_partial(env):
    base, _, _, reader = env
    for name in ('a', 'b', 'c'):
        write(base / (name + '.md'))
    result = scan_bundle(reader, 'general', replace(reader.limits, max_documents_per_bundle=2))
    assert ids(result) == ['a', 'b']
    assert result.partial and result.reasons == ('document_limit',)


def test_depth_limit_is_partial(env):
    base, _, _, reader = env
    for name in ('top.md', 'a/mid.md', 'a/b/deep.md'):
        write(base / name)
    result = scan_bundle(reader, 'general', replace(reader.limits, max_depth=1))
    assert ids(result) == ['a/mid', 'top']
    assert result.partial and result.reasons == ('depth_limit',)


def test_budget_exhausted_during_walk(env):
    base, _, _, reader = env
    write(base / 'a.md')
    calls = iter([0])
    result = scan_bundle(reader, 'general', reader.limits, lambda: next(calls, 100))
    assert result.documents == () and result.partial and result.reasons == ('scan_budget',)


def test_budget_exhausted_between_reads(env):
    base, _, _, reader = env
    for name in ('a', 'b', 'c'):
        write(base / (name + '.md'))
    clock_value = [0]

    def now():
        clock_value[0] += 10
        return NOW

    reader.now = now
    result = scan_bundle(reader, 'general', reader.limits, lambda: clock_value[0])
    assert ids(result) == ['a']
    assert result.partial and result.reasons == ('scan_budget',) and result.skipped == {'unread': 2}


def test_background_scan_continues_beyond_short_budget(env):
    base, _, _, reader = env
    for name in ('a', 'b', 'c'):
        write(base / (name + '.md'))
    seconds = [0]

    def now():
        seconds[0] += 10
        return NOW

    reader.now = now
    result = scan_bundle(reader, 'general', reader.limits,
                         clock=lambda: seconds[0], background_budget_s=90)
    assert ids(result) == ['a', 'b', 'c']
    assert not result.partial and result.skipped == {}


def test_background_scan_stops_at_total_scan_budget(env):
    base, _, _, reader = env
    for name in ('a', 'b', 'c'):
        write(base / (name + '.md'))
    seconds = [0]

    def now():
        seconds[0] += 50
        return NOW

    reader.now = now
    result = scan_bundle(reader, 'general', reader.limits,
                         clock=lambda: seconds[0], background_budget_s=90)
    assert ids(result) == ['a', 'b']
    assert result.partial and result.reasons == ('scan_budget',)
    assert result.skipped == {'unread': 1}


@pytest.mark.parametrize('budget', [True, 0, 91, float('nan'), float('inf'), '90'])
def test_invalid_background_budget_is_rejected(env, budget):
    _, _, _, reader = env
    with pytest.raises(ValueError, match='invalid background scan budget'):
        scan_bundle(reader, 'general', reader.limits, background_budget_s=budget)


def test_links_are_skipped(env, monkeypatch):
    base, _, _, reader = env
    write(base / 'ok.md')
    write(base / 'linked' / 'x.md')
    write(base / 'alias.md')
    monkeypatch.setattr(scanner, 'is_link', lambda path: path.name in ('linked', 'alias.md'))
    result = scan_bundle(reader, 'general', reader.limits)
    assert ids(result) == ['ok'] and result.skipped == {'links': 2}


def test_real_link_is_not_followed(env, tmp_path_factory):
    base, _, _, reader = env
    outside = tmp_path_factory.mktemp('outside')
    write(outside / 'x.md')
    write(base / 'ok.md')
    make_link(base / 'linked', outside)
    result = scan_bundle(reader, 'general', reader.limits)
    assert ids(result) == ['ok'] and result.skipped == {'links': 1}


def test_invalid_names_are_skipped(env):
    base, _, _, reader = env
    write(base / 'ok.md')
    write(base / 'a .md')
    write(base / 'index' / 'x.md')
    result = scan_bundle(reader, 'general', reader.limits)
    assert ids(result) == ['ok'] and result.skipped == {'invalid_name': 2}


def test_case_collisions_are_excluded(env, monkeypatch):
    base, _, _, reader = env
    write(base / 'a.md')
    write(base / 'b.md')
    monkeypatch.setattr(scanner, 'find_case_collisions', lambda concept_ids: [('a', 'b')])
    result = scan_bundle(reader, 'general', reader.limits)
    assert result.documents == () and result.skipped == {'case_collision': 2}


def test_scandir_error_is_partial(env, monkeypatch):
    base, _, _, reader = env
    write(base / 'a.md')

    def broken(path):
        raise OSError('sensitive internal detail')

    monkeypatch.setattr(scanner, '_scandir', broken)
    result = scan_bundle(reader, 'general', reader.limits)
    assert result.documents == () and result.partial and result.reasons == ('scan_io_error',)
    assert 'sensitive' not in repr(result)


def test_unavailable_bundle_is_not_scanned(env):
    base, config, _, reader = env
    write(base / 'a.md')
    config['bundles'][0]['enabled'] = False
    with pytest.raises(DocumentError) as caught:
        scan_bundle(reader, 'general', reader.limits)
    assert caught.value.code == 'BUNDLE_UNAVAILABLE'


def test_access_lost_during_scan_aborts(env):
    base, config, _, reader = env
    write(base / 'a.md')
    write(base / 'b.md')

    def now():
        config['bundles'][0]['enabled'] = False
        return NOW

    reader.now = now
    with pytest.raises(DocumentError) as caught:
        scan_bundle(reader, 'general', reader.limits)
    assert caught.value.code == 'BUNDLE_UNAVAILABLE'
