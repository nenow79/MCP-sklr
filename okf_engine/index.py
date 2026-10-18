"""Publiczny indeks dokumentów OKF w pamięci (generacje per bundle).

Przechowuje wyłącznie PublicDocument (po maskowaniu), nigdy surowych bajtów ani
trwałego cache. Publikacja jest atomowa: generacja jest niezmienną migawką i
zastępowana w całości pod blokadą. Ticket z begin() chroni przed spóźnioną
publikacją po invalidate()/nowszym skanie. Przed publikacją każdy dokument
przechodzi DocumentReader.check_document (aktualny bundle, wiązanie, dostęp).
Brak dostępu do bundle w trakcie publikacji usuwa indeks bundle i zgłasza
DocumentError. Niepełny skan publikuje wyłącznie zweryfikowany podzbiór jako
partial; nie mieszamy go z dawną generacją. Metody są bezpieczne wątkowo, a I/O
odbywa się poza blokadą. Planowanie odświeżeń i backoff należą do serwisu (C2c).
"""
import json
import threading
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .documents import DocumentError, DocumentReader, PublicDocument, same_binding
from .project_scope import allowed_path
from .limits import KnowledgeLimits
from .scanner import ScanResult

STATE_EMPTY = "empty"
STATE_READY = "ready"
STATE_PARTIAL = "partial"
STATE_REFRESHING = "refreshing"
STATE_ERROR = "error"
REASON_TEXT_LIMIT = "index_text_limit"
REASON_DOCUMENT_UNAVAILABLE = "document_unavailable"


def public_text_bytes(document: PublicDocument) -> int:
    """Suma bajtów UTF-8 publicznego tekstu i metadanych (limit indeksu, nie RSS)."""
    view = document.view
    parts = (view.title, view.description or "", view.type, view.body,
             json.dumps(view.metadata, ensure_ascii=False, sort_keys=True, default=str))
    return sum(len(part.encode("utf-8", errors="replace")) for part in parts)


@dataclass(frozen=True, eq=False)
class IndexEntry:
    document: PublicDocument = field(repr=False)
    text_bytes: int
    prepared: Any = field(default=None, repr=False)  # wynik prepare(), np. znormalizowany tekst


@dataclass(frozen=True, eq=False)
class IndexGeneration:
    entries: Tuple[IndexEntry, ...] = field(repr=False)  # posortowane po concept_id
    partial: bool
    reasons: Tuple[str, ...]
    indexed_at: datetime
    excluded: int  # dokumenty pominięte: błędy odczytu, nieprzeczytane, odrzucone, ponad limit
    text_bytes: int


@dataclass(frozen=True)
class IndexState:
    state: str
    previous_state: Optional[str]  # dla refreshing/error: stan ostatniej generacji
    indexed_documents: int
    indexed_at: Optional[datetime]
    reasons: Tuple[str, ...]
    excluded_documents: int
    error: Optional[str]  # bezpieczny kod


class _Slot:
    __slots__ = ("ticket", "generation", "refreshing", "error")

    def __init__(self) -> None:
        self.ticket = 0
        self.generation: Optional[IndexGeneration] = None
        self.refreshing = False
        self.error: Optional[str] = None


class KnowledgeIndex:
    def __init__(self, now: Callable[[], datetime],
                 prepare: Optional[Callable[[PublicDocument], Any]] = None) -> None:
        self._now = now
        self._prepare = prepare
        self._lock = threading.Lock()
        self._slots: Dict[str, _Slot] = {}

    def _slot(self, bundle_id: str) -> _Slot:
        slot = self._slots.get(bundle_id)
        if slot is None:
            slot = self._slots[bundle_id] = _Slot()
        return slot

    def begin(self, bundle_id: str) -> int:
        """Rozpoczyna odświeżanie; nowszy ticket unieważnia poprzednie."""
        with self._lock:
            slot = self._slot(bundle_id)
            slot.ticket += 1
            slot.refreshing = True
            slot.error = None
            return slot.ticket

    def invalidate(self, bundle_id: str) -> None:
        """Usuwa generację i unieważnia trwające skany (disabled, utrata roota)."""
        with self._lock:
            slot = self._slot(bundle_id)
            slot.ticket += 1
            slot.generation = None
            slot.refreshing = False
            slot.error = None

    def finish_failed(self, ticket: int, bundle_id: str, code: str) -> bool:
        """Zapisuje bezpieczny kod błędu dla aktualnego ticketu; poprzednia generacja zostaje."""
        with self._lock:
            slot = self._slot(bundle_id)
            if slot.ticket != ticket:
                return False
            slot.refreshing = False
            slot.error = code
            return True

    def publish(self, ticket: int, reader: DocumentReader, bundle_id: str, scan: ScanResult,
                limits: KnowledgeLimits, *, commit_lock=None,
                validate_commit: Optional[Callable[[], bool]] = None,
                budget_exhausted: Optional[Callable[[], bool]] = None,
                cached_unchecked: bool = False) -> bool:
        """Publikuje zweryfikowaną generację; False, gdy ticket jest nieaktualny.

        DocumentError(BUNDLE_UNAVAILABLE) oznacza utratę dostępu: indeks bundle jest usunięty.
        C2c: opcjonalny commit_lock i validate_commit chronią wyłącznie końcową
        podmianę generacji; kosztowna walidacja dokumentów nie blokuje konfiguracji.
        Kolejność locków: zewnętrzny config -> wewnętrzny index, nigdy odwrotnie.
        """
        with self._lock:
            if self._slot(bundle_id).ticket != ticket:
                return False
        try:
            current_bundle = reader.current_bundle(bundle_id)
        except DocumentError:
            self.invalidate(bundle_id)
            raise
        documents = sorted(scan.documents, key=lambda item: item.view.identity.concept_id)
        entries: List[IndexEntry] = []
        reasons = set(scan.reasons)
        excluded = sum(scan.errors.values()) + scan.skipped.get("unread", 0)
        used = 0
        for position, document in enumerate(documents):
            if budget_exhausted is not None and budget_exhausted():
                # Nie publikujemy niedokończonego podzbioru wskutek timeoutu.
                # Właściciel kończy refresh jako błąd, zachowując starą generację.
                raise DocumentError("REFRESH_BUDGET_EXHAUSTED")
            size = public_text_bytes(document)
            if used + size > limits.max_index_text_bytes:
                reasons.add(REASON_TEXT_LIMIT)
                excluded += len(documents) - position
                break
            if document.view.identity.bundle_id != bundle_id:
                reasons.add(REASON_DOCUMENT_UNAVAILABLE)
                excluded += 1
                continue
            try:
                if cached_unchecked:
                    # Tylko podpisane rekordy przekazane przez backend restore.
                    # Nie sprawdzamy plików źródłowych; search sprawdza każde trafienie.
                    if (document.revision or 'cache_unchecked' not in document.view.warnings
                            or not same_binding(document._bundle, current_bundle)
                            or not allowed_path(current_bundle,
                                                document.view.identity.concept_id + '.md')):
                        raise DocumentError('DOCUMENT_UNAVAILABLE')
                    reasons.add('cache_unchecked')
                else:
                    reader.check_document(document)
            except DocumentError as exc:
                if exc.code == "BUNDLE_UNAVAILABLE":
                    self.invalidate(bundle_id)
                    raise
                reasons.add(REASON_DOCUMENT_UNAVAILABLE)
                excluded += 1
                continue
            prepared = self._prepare(document) if self._prepare is not None else None
            entries.append(IndexEntry(document, size, prepared))
            used += size
        generation = IndexGeneration(tuple(entries), bool(reasons), tuple(sorted(reasons)),
                                     self._now(), excluded, used)
        with commit_lock if commit_lock is not None else nullcontext():
            if validate_commit is not None and not validate_commit():
                return False
            with self._lock:
                slot = self._slot(bundle_id)
                if slot.ticket != ticket:
                    return False
                if budget_exhausted is not None and budget_exhausted():
                    raise DocumentError("REFRESH_BUDGET_EXHAUSTED")
                slot.generation = generation
                slot.refreshing = False
                slot.error = None
                return True

    def mark_partial(self, bundle_id, reason):
        with self._lock:
            generation = self._slot(bundle_id).generation
            if generation is not None:
                self._slot(bundle_id).generation = replace(
                    generation, partial=True, reasons=tuple(sorted(set(generation.reasons) | {reason})))

    def drop_targets(self, bundle_id, targets):
        """Po committed usuwa stare cele i unieważnia spóźnioną publikację; reszta zostaje."""
        targets = frozenset(targets)
        with self._lock:
            slot = self._slot(bundle_id)
            slot.ticket += 1
            slot.refreshing = False
            generation = slot.generation
            if generation is not None:
                entries = tuple(entry for entry in generation.entries
                                if entry.document.view.identity.concept_id not in targets)
                slot.generation = replace(generation, entries=entries, partial=True,
                                          reasons=tuple(sorted(set(generation.reasons) | {'update_pending'})),
                                          text_bytes=sum(entry.text_bytes for entry in entries))

    def patch(self, ticket, reader, bundle_id, scan, limits, targets, *,
              commit_lock=None, validate_commit=None):
        """Przetwarza tylko zmienione dokumenty, zachowując resztę bieżącej generacji.

        Nie zmienia indexed_at całego zbioru: patch nie jest pełnym uzgodnieniem.
        Brak poprawnego odczytu usuwa dotknięty stary wpis i oznacza partial.
        Zmiana generacji podczas przygotowania daje False, bez nadpisania.
        """
        from .models import DocumentIdentity
        targets = frozenset(targets)
        if not targets or len(targets) > limits.max_documents_per_bundle:
            raise DocumentError('SAFETY_LIMIT')
        for concept in targets:
            DocumentIdentity(bundle_id, concept)
        with self._lock:
            slot = self._slot(bundle_id)
            if slot.ticket != ticket:
                return False
            previous = slot.generation
        current = reader.current_bundle(bundle_id)
        entries = {entry.document.view.identity.concept_id: entry
                   for entry in (previous.entries if previous is not None else ())
                   if entry.document.view.identity.concept_id not in targets}
        reasons = (set(previous.reasons if previous else ('index_not_ready',)) - {'update_pending'}) | set(scan.reasons)
        seen = set()
        for document in scan.documents:
            concept = document.view.identity.concept_id
            if (document.view.identity.bundle_id != bundle_id or concept not in targets
                    or concept in seen or not same_binding(document._bundle, current)):
                raise DocumentError('DOCUMENT_UNAVAILABLE')
            seen.add(concept)
            reader.check_document(document)
            size = public_text_bytes(document)
            prepared = self._prepare(document) if self._prepare is not None else None
            entries[concept] = IndexEntry(document, size, prepared)
        missed = len(targets - seen)
        if missed:
            reasons.add('document_unavailable')
        used = sum(entry.text_bytes for entry in entries.values())
        if len(entries) > limits.max_documents_per_bundle or used > limits.max_index_text_bytes:
            raise DocumentError('SAFETY_LIMIT')
        generation = IndexGeneration(tuple(entries[key] for key in sorted(entries)), bool(reasons),
                                     tuple(sorted(reasons)), previous.indexed_at if previous else self._now(),
                                     (previous.excluded if previous else 0) + missed, used)
        with commit_lock if commit_lock is not None else nullcontext():
            if validate_commit is not None and not validate_commit():
                return False
            with self._lock:
                slot = self._slot(bundle_id)
                if slot.ticket != ticket or slot.generation is not previous:
                    return False
                slot.generation = generation
                slot.refreshing = False
                slot.error = None
                return True

    def snapshot(self, bundle_id: str) -> Optional[IndexGeneration]:
        """Niezmienna migawka bieżącej generacji albo None."""
        with self._lock:
            slot = self._slots.get(bundle_id)
            return slot.generation if slot is not None else None

    def remove_document(self, bundle_id: str, concept_id: str) -> bool:
        """Usuwa niedostępny wpis; generacja staje się partial z jawnym powodem."""
        with self._lock:
            slot = self._slots.get(bundle_id)
            generation = slot.generation if slot is not None else None
            if generation is None:
                return False
            kept = tuple(entry for entry in generation.entries
                         if entry.document.view.identity.concept_id != concept_id)
            if len(kept) == len(generation.entries):
                return False
            slot.generation = replace(
                generation, entries=kept, partial=True,
                reasons=tuple(sorted(set(generation.reasons) | {REASON_DOCUMENT_UNAVAILABLE})),
                excluded=generation.excluded + 1,
                text_bytes=sum(entry.text_bytes for entry in kept))
            return True

    def state(self, bundle_id: str) -> IndexState:
        with self._lock:
            slot = self._slots.get(bundle_id)
            generation = slot.generation if slot is not None else None
            if generation is None:
                base, count, at, reasons, excluded = STATE_EMPTY, 0, None, (), 0
            else:
                base = STATE_PARTIAL if generation.partial else STATE_READY
                count, at = len(generation.entries), generation.indexed_at
                reasons, excluded = generation.reasons, generation.excluded
            if slot is not None and slot.refreshing:
                return IndexState(STATE_REFRESHING, base, count, at, reasons, excluded, None)
            if slot is not None and slot.error:
                return IndexState(STATE_ERROR, base, count, at, reasons, excluded, slot.error)
            return IndexState(base, None, count, at, reasons, excluded, None)
