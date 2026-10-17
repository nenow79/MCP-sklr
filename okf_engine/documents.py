"""Świeży ograniczony odczyt OKF; publiczny wynik, bez trwałego raw cache.

Właściciel wstrzykuje bieżącą ocenę bundle oraz kontrolę dostępu konkretnej
ścieżki. Callbacki muszą respektować aktualne roots i politykę dostępu.
Kontrole przed/po odczycie ograniczają wyścigi, nie gwarantują pełnego TOCTOU.
"""
import os
import stat
import threading
import time
from functools import wraps
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from .bundles import BundleState
from .limits import KnowledgeLimits
from .models import DocumentIdentity, IdentityError
from .paths import PathSecurityError, resolve_concept_file
from .parser import parse_document
from .public_view import PublicDocumentView, PublicViewError, build_public_view
from .project_scope import allowed_path
from .revisions import RevisionBinding, RevisionSigner
from .sections import SectionOutline, build_outline


class DocumentError(Exception):
    """Wyłącznie bezpieczny kod, bez ścieżek i treści dokumentu."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PublicDocument:
    view: PublicDocumentView = field(repr=False)
    outline: SectionOutline = field(repr=False)
    revision: str = field(repr=False)
    binding: RevisionBinding = field(repr=False)
    _bundle: BundleState = field(repr=False)  # wiązanie dostępu, nie serializować
    format: str = field(default="okf", repr=False)


@dataclass(frozen=True)
class DocumentBytes:
    """Prywatna migawka odczytu; raw nie trafia do cache/SQLite/logów."""
    identity: DocumentIdentity
    state: BundleState = field(repr=False)
    path: Path = field(repr=False)
    raw: bytes = field(repr=False)
    snapshot: tuple = field(repr=False)


def require_bundle(state: BundleState) -> BundleState:
    if (not isinstance(state, BundleState) or not state.available or state.entry is None
            or not state.entry.enabled or state.entry.scope not in ("central", "project") or state.path is None):
        raise DocumentError("BUNDLE_UNAVAILABLE")
    return state


def same_binding(first: BundleState, second: BundleState) -> bool:
    """Opis/label nie zmieniają tożsamości; ścieżki, root i scope ją zmieniają."""
    a, b = require_bundle(first), require_bundle(second)
    return (a.id, a.entry.root, a.entry.root_path, a.entry.subpath, a.entry.scope, a.entry.profile, a.path) == (
        b.id, b.entry.root, b.entry.root_path, b.entry.subpath, b.entry.scope, b.entry.profile, b.path)


_WINDOWS = os.name == "nt"


def _snapshot(info):
    """Windows: lstat/fstat mogą różnić się ctime mimo niezmienionego pliku.

    Porównujemy tożsamość, rozmiar i mtime; na pozostałych systemach także
    ctime. Nie jest to pełna gwarancja wykrycia zewnętrznych zmian/TOCTOU.
    """
    values = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return values if _WINDOWS else values + (info.st_ctime_ns,)


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400)


def _measure_reader(name):
    """Stałe nazwy i agregaty; pomiar nie obejmuje ani nie utrwala argumentów."""
    def decorate(method):
        @wraps(method)
        def measured(self, *args, **kwargs):
            started = time.perf_counter_ns()
            try:
                return method(self, *args, **kwargs)
            finally:
                elapsed = max(0, time.perf_counter_ns() - started)
                with self._measurement_lock:
                    calls, nanos = self._measurements.get(name, (0, 0))
                    self._measurements[name] = (calls + 1, nanos + elapsed)
        return measured
    return decorate


class DocumentReader:
    """Callback bundle(id) zwraca świeży BundleState; authorize(state,path) -> bool.

    Brak domyślnej zgody. Exceptions callbacków dostępu są fail-closed i nie
    ujawniają surowych komunikatów. Signer jest wspólną zależnością właściciela.
    """

    def __init__(self, bundle: Callable[[str], BundleState],
                 authorize: Callable[[BundleState, Path], bool],
                 signer: RevisionSigner, limits: KnowledgeLimits,
                 now: Callable[[], datetime]):
        self.bundle = bundle
        self.authorize = authorize
        self.signer = signer
        self.limits = limits
        self.now = now
        self._measurement_lock = threading.Lock()
        self._measurements = {}

    def measurements(self):
        """Suma czasu operacji, nie czas wall-clock przebiegu; bez danych wejściowych."""
        with self._measurement_lock:
            result = {}
            for name in ("bundle_check", "path_resolve", "authorize"):
                calls, nanos = self._measurements.get(name, (0, 0))
                result[name + "_calls"] = calls
                result[name + "_ns"] = nanos
            return result

    @_measure_reader("path_resolve")
    def _resolve_concept(self, directory, concept):
        return resolve_concept_file(directory, concept)

    @_measure_reader("authorize")
    def _authorize_path(self, state, path):
        return self.authorize(state, path)

    @_measure_reader("bundle_check")
    def current_bundle(self, bundle_id: str) -> BundleState:
        try:
            state = require_bundle(self.bundle(bundle_id))
            if state.id != bundle_id or state.entry.id != bundle_id:
                raise DocumentError("BUNDLE_UNAVAILABLE")
            return state
        except Exception:
            raise DocumentError("BUNDLE_UNAVAILABLE") from None

    def check_access(self, initial: BundleState, path: Path) -> BundleState:
        current = self.current_bundle(initial.id)
        if not same_binding(initial, current):
            raise DocumentError("BUNDLE_UNAVAILABLE")
        if not allowed_path(current, path.relative_to(current.path).as_posix()):
            raise DocumentError("DOCUMENT_UNAVAILABLE")
        try:
            allowed = self._authorize_path(current, path)
        except Exception:
            allowed = False
        if allowed is not True:
            raise DocumentError("DOCUMENT_UNAVAILABLE")
        return current

    def check_document(self, document: PublicDocument) -> None:
        """Ponowny dostęp/ścieżka przed publikacją lub zwrotem publicznego cache.

        Nie odczytuje body ani nie potwierdza rewizji: search może zwracać
        wcześniejszy skan. Usunięcie, link, kolizja lub zmiana wiązania blokuje wpis.
        Zgłasza bezpieczny DocumentError; wywołujący usuwa niedostępny wpis.
        """
        state = document._bundle
        current = self.current_bundle(document.view.identity.bundle_id)
        if not same_binding(state, current):
            raise DocumentError("BUNDLE_UNAVAILABLE")
        try:
            path, canonical = self._resolve_concept(current.path, document.view.identity.concept_id)
        except (PathSecurityError, OSError, ValueError):
            raise DocumentError("DOCUMENT_UNAVAILABLE") from None
        expected = RevisionBinding(current.id, current.entry.root_path, str(current.path), str(path))
        if canonical != document.view.identity.concept_id or expected != document.binding:
            raise DocumentError("DOCUMENT_UNAVAILABLE")
        self.check_access(state, path)

    def read(self, bundle_id: str, concept_id: str) -> PublicDocument:
        return self.build_document(self.read_bytes(bundle_id, concept_id))

    def read_bytes(self, bundle_id: str, concept_id: str) -> DocumentBytes:
        """Ograniczony świeży odczyt z kontrolą dostępu; nie wywołuje parsera."""
        state = self.current_bundle(bundle_id)
        try:
            path, canonical = self._resolve_concept(state.path, concept_id)
            identity = DocumentIdentity(bundle_id, canonical)
        except (PathSecurityError, IdentityError):
            raise DocumentError("DOCUMENT_UNAVAILABLE") from None
        self.check_access(state, path)
        try:
            before = path.lstat()
            if not _regular(before):
                raise DocumentError("DOCUMENT_UNAVAILABLE")
            if before.st_size > self.limits.max_document_bytes:
                raise DocumentError("SAFETY_LIMIT")
            with path.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                if not _regular(opened) or _snapshot(opened) != _snapshot(before):
                    raise DocumentError("DOCUMENT_CHANGED")
                raw = stream.read(self.limits.max_document_bytes + 1)
                after = os.fstat(stream.fileno())
            if len(raw) > self.limits.max_document_bytes:
                raise DocumentError("SAFETY_LIMIT")
            if (len(raw) != after.st_size or _snapshot(opened) != _snapshot(after)
                    or _snapshot(after) != _snapshot(path.lstat())):
                raise DocumentError("DOCUMENT_CHANGED")
            checked_path, checked_id = self._resolve_concept(state.path, canonical)
            if checked_path != path or checked_id != canonical:
                raise DocumentError("DOCUMENT_CHANGED")
        except (OSError, ValueError, PathSecurityError):
            raise DocumentError("DOCUMENT_UNAVAILABLE") from None
        self.check_access(state, path)
        return DocumentBytes(identity, state, path, raw, _snapshot(after))

    def check_bytes(self, source: DocumentBytes) -> None:
        """Ponowna kontrola migawki i ścieżki przed użyciem odczytanych bajtów."""
        if not isinstance(source, DocumentBytes):
            raise DocumentError("DOCUMENT_UNAVAILABLE")
        self.check_access(source.state, source.path)
        try:
            path, canonical = self._resolve_concept(source.state.path, source.identity.concept_id)
            info = path.lstat()
            if (path != source.path or canonical != source.identity.concept_id
                    or not _regular(info) or _snapshot(info) != source.snapshot):
                raise DocumentError("DOCUMENT_CHANGED")
        except (OSError, ValueError, PathSecurityError):
            raise DocumentError("DOCUMENT_UNAVAILABLE") from None

    def build_document(self, source: DocumentBytes) -> PublicDocument:
        """Parsuje tę samą kontrolowaną migawkę, bez drugiego odczytu pliku."""
        self.check_bytes(source)
        state, path, raw, identity = source.state, source.path, source.raw, source.identity
        bundle_id = identity.bundle_id
        parsed = parse_document(raw, identity, self.limits.to_parse_limits())
        document_format = "okf"
        if (not parsed.ok and state.entry.scope == "project" and
                parsed.errors[0].reason == "missing_frontmatter"):
            parsed = replace(parsed, errors=(), metadata={"type": "unknown"},
                             warnings=parsed.warnings + ("unconverted",))
            document_format = "markdown"
        if not parsed.ok:
            raise DocumentError(parsed.errors[0].kind)
        try:
            view = build_public_view(parsed, self.now())
        except PublicViewError as exc:
            raise DocumentError(exc.code) from None
        if document_format == "markdown":
            view = replace(view, metadata={}, type="unknown", status=None, trust_tier="unverified")
        outline = build_outline(view.body, view.body_start_line)
        binding = RevisionBinding(bundle_id, state.entry.root_path, str(state.path), str(path))
        revision = self.signer.sign(binding, raw)
        document = PublicDocument(view, outline, revision, binding, state, document_format)
        self.check_document(document)
        try:
            final = path.lstat()
            if not _regular(final) or _snapshot(final) != source.snapshot:
                raise DocumentError("DOCUMENT_CHANGED")
        except (OSError, ValueError):
            raise DocumentError("DOCUMENT_UNAVAILABLE") from None
        return document
