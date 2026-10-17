"""Uwierzytelniony cache-hit bez YAML parsera; brak integracji SQLite/runtime.

Tag musi powstać bezpośrednio po kontrolowanym parsowaniu i maskowaniu,
nie przez podpisanie rekordu właśnie pobranego z niezaufanego SQLite.
Starszy rekord bez tagu wymaga parsowania. Klucz jest osobny od RevisionSigner.
"""
import hashlib
import hmac
from dataclasses import replace

from .cache_store import CacheError, CacheRecord, fingerprint, _payload
from .documents import PublicDocument
from .models import ParsedDocument, BLOCK_MASK
from .public_view import build_public_view
from .revisions import RevisionBinding
from .sections import build_outline


def record_tag(record, key):
    """Wywoływać na publicznym rekordzie bezpośrednio po świeżym parsowaniu."""
    if not isinstance(record, CacheRecord) or not isinstance(key, bytes) or len(key) != 32:
        raise CacheError('CACHE_AUTH_INVALID')
    parts = [record.identity.bundle_id, record.identity.concept_id, record.binding,
             record.versions, record.digest, _payload(record)]
    data = b'sklr-cache-record-v1\0'
    for part in parts:
        value = part.encode('utf-8')
        data += len(value).to_bytes(8, 'big') + value
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def cache_hit(reader, source, record, tag, key, *, binding, versions):
    """Zwraca PublicDocument lub bezpieczny błąd wymagający ponownego parsowania.

    binding/versions podaje aktualny adapter konfiguracji. Nie używać wartości
    z rekordu jako dowodu zgodności z aktualną konfiguracją.
    """
    reader.check_bytes(source)
    if (not isinstance(record, CacheRecord) or record.identity != source.identity
            or record.binding != binding or record.versions != versions):
        raise CacheError('CACHE_CONTEXT_INVALID')
    if not isinstance(tag, str) or len(tag) != 64:
        raise CacheError('CACHE_AUTH_INVALID')
    expected = record_tag(record, key)
    if not hmac.compare_digest(tag.encode('utf-8'), expected.encode('ascii')):
        raise CacheError('CACHE_AUTH_INVALID')
    if not hmac.compare_digest(record.digest, fingerprint(source.raw, key)):
        raise CacheError('CACHE_SOURCE_CHANGED')
    # Ponowne maskowanie i walidacja publicznych metadanych, bez parse_document.
    parsed = ParsedDocument(source.identity, len(source.raw),
                            metadata=record.metadata if record.format == 'okf' else {'type': 'unknown'},
                            body=record.body, body_start_line=record.body_start_line,
                            warnings=('cache_hit',))
    view = build_public_view(parsed, reader.now())
    if record.format == 'markdown':
        view = replace(view, metadata={}, type='unknown', status=None, trust_tier='unverified',
                       warnings=view.warnings + ('unconverted',))
    # Cache nie zachowuje list pierwotnie maskowanych pól; sygnał jest konserwatywny.
    if BLOCK_MASK in view.body or '***' in view.body or '***' in str(view.metadata):
        view = replace(view, masked=True)
    state, path = source.state, source.path
    revision_binding = RevisionBinding(source.identity.bundle_id, state.entry.root_path,
                                       str(state.path), str(path))
    document = PublicDocument(view, build_outline(view.body, view.body_start_line),
                              reader.signer.sign(revision_binding, source.raw),
                              revision_binding, state, record.format)
    reader.check_document(document)
    reader.check_bytes(source)
    return document
