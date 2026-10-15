"""Świeży ograniczony odczyt OKF; publiczny wynik, bez trwałego raw cache.

Właściciel wstrzykuje bieżącą ocenę bundle oraz kontrolę dostępu konkretnej
ścieżki. Callbacki muszą respektować aktualne roots i politykę dostępu.
Kontrole przed/po odczycie ograniczają wyścigi, nie gwarantują pełnego TOCTOU.
"""
import os
import stat
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
    format: str = "okf"


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
            allowed = self.authorize(current, path)
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
            path, canonical = resolve_concept_file(current.path, document.view.identity.concept_id)
        except (PathSecurityError, OSError, ValueError):
            raise DocumentError("DOCUMENT_UNAVAILABLE") from None
        expected = RevisionBinding(current.id, current.entry.root_path, str(current.path), str(path))
        if canonical != document.view.identity.concept_id or expected != document.binding:
            raise DocumentError("DOCUMENT_UNAVAILABLE")
        self.check_access(state, path)

    def read(self, bundle_id: str, concept_id: str) -> PublicDocument:
        state = self.current_bundle(bundle_id)
        try:
            path, canonical = resolve_concept_file(state.path, concept_id)
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
            checked_path, checked_id = resolve_concept_file(state.path, canonical)
            if checked_path != path or checked_id != canonical:
                raise DocumentError("DOCUMENT_CHANGED")
        except (OSError, ValueError, PathSecurityError):
            raise DocumentError("DOCUMENT_UNAVAILABLE") from None
        self.check_access(state, path)
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
            if not _regular(final) or _snapshot(final) != _snapshot(after):
                raise DocumentError("DOCUMENT_CHANGED")
        except (OSError, ValueError):
            raise DocumentError("DOCUMENT_UNAVAILABLE") from None
        return document
