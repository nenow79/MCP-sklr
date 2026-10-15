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

from .documents import DocumentError, DocumentReader, PublicDocument
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
                validate_commit: Optional[Callable[[], bool]] = None) -> bool:
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
            reader.current_bundle(bundle_id)
        except DocumentError:
            self.invalidate(bundle_id)
            raise
        documents = sorted(scan.documents, key=lambda item: item.view.identity.concept_id)
        entries: List[IndexEntry] = []
        reasons = set(scan.reasons)
        excluded = sum(scan.errors.values()) + scan.skipped.get("unread", 0)
        used = 0
        for position, document in enumerate(documents):
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
