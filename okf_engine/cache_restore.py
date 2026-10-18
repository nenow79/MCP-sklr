"""Odtworzenie podpisanego cache bez odczytu pliku; nie potwierdza świeżości.

Właściciel potwierdza bieżący BundleState i wersje parsera/maskowania.
Dokument ma revision='': wyszukiwarka MUSI odczytać źródło i ponownie ocenić
trafienie przed zwrotem sekcji/revizji. Nie używać z niezmienionym search.
"""
import hmac
from dataclasses import replace

from .cache_hit import record_tag
from .cache_store import CacheError, CacheRecord
from .documents import PublicDocument, require_bundle
from .models import ParsedDocument, BLOCK_MASK
from .paths import relative_file
from .project_scope import allowed_path
from .public_view import build_public_view
from .revisions import RevisionBinding
from .sections import build_outline


def restore_cached_document(record, key, state, *, binding, versions, now):
    state = require_bundle(state)
    if (not isinstance(record, CacheRecord) or record.identity.bundle_id != state.id
            or record.binding != binding or record.versions != versions):
        raise CacheError('CACHE_CONTEXT_INVALID')
    relative = relative_file(record.identity.concept_id)
    if not allowed_path(state, relative):
        raise CacheError('CACHE_SCOPE_INVALID')
    if record.auth_tag is None:
        raise CacheError('CACHE_AUTH_INVALID')
    expected = record_tag(record, key)
    if not hmac.compare_digest(record.auth_tag.encode('utf-8'), expected.encode('ascii')):
        raise CacheError('CACHE_AUTH_INVALID')
    metadata = record.metadata if record.format == 'okf' else {'type': 'unknown'}
    parsed = ParsedDocument(record.identity, 0, metadata=metadata, body=record.body,
                            body_start_line=record.body_start_line,
                            warnings=('cache_unchecked',))
    view = build_public_view(parsed, now)
    if record.format == 'markdown':
        view = replace(view, metadata={}, type='unknown', status=None, trust_tier='unverified',
                       warnings=view.warnings + ('unconverted',))
    if BLOCK_MASK in view.body or '***' in view.body or '***' in str(view.metadata):
        view = replace(view, masked=True)
    revision_binding = RevisionBinding(state.id, state.entry.root_path, str(state.path),
                                       str(state.path / relative))
    return PublicDocument(view, build_outline(view.body, view.body_start_line), '',
                          revision_binding, state, record.format)
