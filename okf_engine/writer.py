"""C3a: przygotowanie bajtów OKF bez I/O, rewizji, kolejki i uprawnień.

PreparedWrite jest PRYWATNY: before/after oraz widoki mogą zawierać dane
wrażliwe. Nie serializować przez asdict, do MCP, SQLite ani logów. Do panelu
przekazujemy wyłącznie build_preview z okf_engine.preview. Adapter C3b musi
sprawdzić dostęp, rewizję i snapshot, a potem wykonać transakcję po zgodzie.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple

from .models import DocumentIdentity, ParseLimits, ParsedDocument
from .parser import parse_document
from .public_view import PublicDocumentView, PublicViewError, build_public_view
from .textlines import detect_eol, join_lines, split_lines

_BOM = b"\xef\xbb\xbf"
_EOLS = {"lf": "\n", "crlf": "\r\n", "cr": "\r"}
_YAML_MARKER = re.compile(r"^(?:---|\.\.\.)(?:[ \t]|$)")
_TRUST_FIELDS = ("generated", "verified")
_LIFECYCLE_FIELDS = ("status", "stale_after", "usage_window")
_MISSING = object()


class WriteError(ValueError):
    """Stały kod, nigdy tekst wejścia ani komunikat biblioteki YAML."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, repr=False)
class PreparedWrite:
    identity: DocumentIdentity
    mode: str
    before: Optional[bytes]
    after: bytes
    before_view: Optional[PublicDocumentView]
    after_view: PublicDocumentView
    changed: bool
    metadata_replaced: bool
    eol_adjusted: bool
    before_eol: Optional[str]
    after_eol: str
    has_bom: bool
    warnings: Tuple[str, ...] = field(default=())


def _encode(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise WriteError("INVALID_UTF8") from None


def _parse(raw: bytes, identity: DocumentIdentity, limits: ParseLimits) -> ParsedDocument:
    parsed = parse_document(raw, identity, limits)
    if not parsed.ok:
        issue = parsed.errors[0]
        if issue.reason == "not_utf8":
            raise WriteError("INVALID_UTF8")
        raise WriteError(issue.kind)
    if parsed.eol == "mixed":
        raise WriteError("MIXED_EOL_UNSUPPORTED")
    return parsed


def _view(parsed: ParsedDocument, now: datetime) -> PublicDocumentView:
    try:
        return build_public_view(parsed, now)
    except PublicViewError as exc:
        raise WriteError(exc.code) from None


def _normalize(text: str, eol: str) -> Tuple[str, bool]:
    lines = split_lines(text)
    if detect_eol(lines) == "mixed":
        raise WriteError("MIXED_EOL_UNSUPPORTED")
    normalized = "".join(content + (eol if ending else "") for content, ending in lines)
    return normalized, normalized != text


def _check_keys(before, after):
    """Mapping keys chronione rekurencyjnie; listy nie mają stabilnej tożsamości.

    Zmiana listy wymaga oceny całej propozycji w diffie (kontrakt 6.4), zamiast
    zgadywania powiązania elementów po pozycji, nazwie autora lub źródle.
    """
    if isinstance(before, dict):
        if not isinstance(after, dict):
            if before:
                raise WriteError("METADATA_KEY_LOSS")
            return
        for key, value in before.items():
            if key not in after:
                raise WriteError("METADATA_KEY_LOSS")
            _check_keys(value, after[key])


def _lists_changed(before, after):
    if isinstance(before, list) or isinstance(after, list):
        return before != after
    if isinstance(before, dict) and isinstance(after, dict):
        return any(_lists_changed(before.get(key, _MISSING), after.get(key, _MISSING))
                   for key in before.keys() | after.keys())
    return False


def prepare_write(identity: DocumentIdentity, mode: str, body: str,
                  metadata_yaml: Optional[str] = None, *, before: Optional[bytes] = None,
                  limits: ParseLimits = ParseLimits(), now: datetime) -> PreparedWrite:
    """Składa i waliduje pełny dokument; before=None oznacza brak pliku.

    Create używa LF bez BOM. Update zachowuje BOM i jednorodne EOL pliku,
    normalizując jednorodne końce linii propozycji (jawne eol_adjusted).
    metadata_yaml=None zachowuje frontmatter byte-for-byte. Jeśli zamykający
    delimiter nie miał końca linii, dopisujemy separator przed nowym body.
    Nie dopisujemy autorstwa, weryfikacji, dat ani domyślnego statusu do pliku.
    """
    if (not isinstance(identity, DocumentIdentity) or not isinstance(limits, ParseLimits)
            or mode not in ("create", "update") or not isinstance(body, str)
            or (metadata_yaml is not None and not isinstance(metadata_yaml, str))
            or (before is not None and not isinstance(before, bytes))
            or not isinstance(now, datetime) or now.utcoffset() is None):
        raise WriteError("INVALID_ARGUMENT")
    if mode == "create" and before is not None:
        raise WriteError("CREATE_CONFLICT")
    if mode == "create" and metadata_yaml is None:
        raise WriteError("METADATA_REQUIRED")
    if mode == "update" and before is None:
        raise WriteError("DOCUMENT_UNAVAILABLE")
    # Ogranicz wejścia przed dzieleniem na linie i parsowaniem.
    if len(body) > limits.max_document_bytes or (metadata_yaml is not None and
                                               len(metadata_yaml) > limits.max_frontmatter_bytes):
        raise WriteError("SAFETY_LIMIT")
    if len(_encode(body)) > limits.max_document_bytes:
        raise WriteError("SAFETY_LIMIT")
    if metadata_yaml is not None and len(_encode(metadata_yaml)) > limits.max_frontmatter_bytes:
        raise WriteError("SAFETY_LIMIT")

    old = _parse(before, identity, limits) if before is not None else None
    old_view = _view(old, now) if old is not None else None
    eol = _EOLS.get(old.eol, "\n") if old is not None else "\n"
    normalized_body, adjusted = _normalize(body, eol)
    # Zagnieżdżone pełne frontmatter w argumencie body to pomyłka wywołującego.
    body_probe = parse_document(_encode(normalized_body), identity, limits)
    if body_probe.metadata is not None:
        raise WriteError("BODY_FRONTMATTER_UNSUPPORTED")

    if metadata_yaml is None:
        # body_start_line odnosi się do fizycznych linii, także dla CR i CRLF.
        prefix = join_lines(split_lines(before.decode("utf-8-sig"))[:old.body_start_line - 1])
        if normalized_body and not prefix.endswith(("\n", "\r")):
            prefix += eol
            adjusted = True
    else:
        if any(_YAML_MARKER.match(content) for content, _ in split_lines(metadata_yaml)):
            raise WriteError("METADATA_DELIMITERS_UNSUPPORTED")
        metadata, metadata_adjusted = _normalize(metadata_yaml, eol)
        adjusted = adjusted or metadata_adjusted
        if metadata and not metadata.endswith(("\n", "\r")):
            metadata += eol
        prefix = "---" + eol + metadata + "---" + eol
    candidate = (_BOM if old is not None and old.has_bom else b"") + _encode(prefix + normalized_body)
    new = _parse(candidate, identity, limits)
    new_view = _view(new, now)
    changed = candidate != before
    if old is not None and changed:
        if old.body != normalized_body and old_view.masked_body_lines:
            raise WriteError("MASKED_UPDATE_UNSUPPORTED")
        if metadata_yaml is not None:
            if old_view.masked_metadata_paths:
                raise WriteError("MASKED_UPDATE_UNSUPPORTED")
            _check_keys(old.metadata, new.metadata)

    warnings = list(new_view.warnings)
    if adjusted:
        warnings.append("eol_adjusted")
    previous_meta = old.metadata if old is not None else {}
    if any(previous_meta.get(key, _MISSING) != new.metadata.get(key, _MISSING) for key in _TRUST_FIELDS):
        warnings.append("trust_metadata_changed")
    if any(previous_meta.get(key, _MISSING) != new.metadata.get(key, _MISSING) for key in _LIFECYCLE_FIELDS):
        warnings.append("lifecycle_metadata_changed")
    if _lists_changed(previous_meta, new.metadata):
        warnings.append("metadata_lists_changed")
    return PreparedWrite(identity, mode, before, candidate, old_view, new_view, changed,
                         metadata_yaml is not None, adjusted, old.eol if old else None,
                         new.eol, new.has_bom, tuple(dict.fromkeys(warnings)))
