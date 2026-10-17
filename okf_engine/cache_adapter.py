"""Adapter poprawności cache -> istniejący indeks RAM, bez bootstrap/runtime.

fresh(identity) zwraca świeży PublicDocument przez kontrolowany DocumentReader.
Nie podpisujemy starego body tokenem aktualnego pliku. Ten etap nadal wymaga
świeżego parsowania; optymalizacja byte-read/fingerprint jest osobnym etapem.
"""
from dataclasses import replace

from .cache_store import CacheError, CacheRecord
from .documents import PublicDocument
from .scanner import ScanResult
from .sections import build_outline


def restore_document(record, fresh):
    if not isinstance(record, CacheRecord):
        raise CacheError('CACHE_RECORD_INVALID')
    document = fresh(record.identity)
    if not isinstance(document, PublicDocument) or document.view.identity != record.identity:
        raise CacheError('CACHE_DOCUMENT_INVALID')
    view = document.view
    if (record.metadata != view.metadata or record.body != view.body
            or record.body_start_line != view.body_start_line or record.format != document.format):
        raise CacheError('CACHE_SOURCE_CHANGED')
    # Zachowujemy świeże signals, mask flags, binding i rewizję; sekcje budujemy
    # z dokładnie porównanej treści. Cache nie jest źródłem uprawnień ani tokenów.
    return replace(document, outline=build_outline(record.body, record.body_start_line))


def publish_records(index, ticket, reader, bundle_id, records, limits, fresh, *,
                    commit_lock=None, valid=None):
    """Publikuje wyłącznie zgodne rekordy; zmiana źródła daje jawny partial.

    fresh musi sprawdzić wiązanie z contextem koordynatora, nie tylko identity.
    CacheError z niespójności rekordów przerywa; utrata dostępu propaguje się do
    właściciela, który unieważnia przebieg. Brak cichego fallback do starego body.
    """
    documents, seen, skipped = [], set(), 0
    for record in records:
        if valid is not None and not valid():
            return False
        if not isinstance(record, CacheRecord) or record.identity.bundle_id != bundle_id:
            raise CacheError('CACHE_CONTEXT_INVALID')
        if record.identity.concept_id in seen:
            raise CacheError('CACHE_DUPLICATE_TARGET')
        seen.add(record.identity.concept_id)
        if len(seen) > limits.max_documents_per_bundle:
            raise CacheError('CACHE_RECORD_LIMIT')
        try:
            documents.append(restore_document(record, fresh))
        except CacheError as exc:
            if str(exc) != 'CACHE_SOURCE_CHANGED':
                raise
            skipped += 1
    scan = ScanResult(tuple(documents), bool(skipped),
                      ('cache_source_changed',) if skipped else (), {},
                      {'CACHE_SOURCE_CHANGED': skipped} if skipped else {})
    return index.publish(ticket, reader, bundle_id, scan, limits,
                         commit_lock=commit_lock, validate_commit=valid)
