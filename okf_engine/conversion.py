"""Konwersja frontmatter in place. Body jest zachowane bajtowo; brak kuracji."""
from dataclasses import replace

import yaml

from .models import ParseLimits
from .parser import parse_document
from .public_view import build_public_view
from .textlines import join_lines, split_lines
from .writer import PreparedWrite, WriteError, prepare_write


def prepare_conversion(raw, identity, metadata_yaml, *, limits=ParseLimits(), now):
    old = parse_document(raw, identity, limits)
    if old.eol == "mixed":
        raise WriteError("MIXED_EOL_UNSUPPORTED")
    repairable = old.errors and old.errors[0].reason in ("missing_frontmatter", "missing_type")
    if not old.ok and not repairable:
        raise WriteError(old.errors[0].kind)
    if metadata_yaml is None:
        if not old.ok:
            raise WriteError("METADATA_REQUIRED")
        additions = {}
    else:
        proposal = prepare_write(identity, "create", "", metadata_yaml, limits=limits, now=now)
        proposed = parse_document(proposal.after, identity, limits).metadata
        additions = {}
        for key, value in proposed.items():
            if old.metadata is not None and key in old.metadata:
                if old.metadata[key] != value:
                    raise WriteError("CONVERSION_EXISTING_FIELD")
            else:
                additions[key] = value
    eol = {"crlf": "\r\n", "cr": "\r"}.get(old.eol, "\n")
    bom = b"\xef\xbb\xbf" if old.has_bom else b""
    content = raw[len(bom):].decode("utf-8")
    if old.metadata is None:
        header = yaml.safe_dump(additions, sort_keys=False, allow_unicode=True).replace("\n", eol)
        candidate = bom + ("---" + eol + header + "---" + eol).encode("utf-8") + raw[len(bom):]
    elif additions:
        lines = split_lines(content)
        closing = old.body_start_line - 2
        header = yaml.safe_dump(additions, sort_keys=False, allow_unicode=True).replace("\n", eol)
        candidate = bom + (join_lines(lines[:closing]) + header + join_lines(lines[closing:])).encode("utf-8")
    else:
        candidate = raw
    new = parse_document(candidate, identity, limits)
    if not new.ok:
        raise WriteError(new.errors[0].kind)
    if new.body.encode("utf-8") != old.body.encode("utf-8"):
        raise WriteError("CONVERSION_BODY_CHANGED")
    # Nie ujawniamy surowego frontmattera z komentarzami także przed konwersją.
    before_parsed = replace(old, errors=(), metadata={"type": "unknown", **(old.metadata or {})})
    if not isinstance(before_parsed.metadata.get("type"), str) or not before_parsed.metadata["type"].strip():
        before_parsed = replace(before_parsed, metadata={**before_parsed.metadata, "type": "unknown"})
    before_view = build_public_view(before_parsed, now)
    public_metadata = dict(before_view.metadata)
    if old.metadata is None or "type" not in old.metadata:
        public_metadata.pop("type", None)
    before_view = replace(before_view, metadata=public_metadata, status=None if not old.ok else before_view.status)
    after_view = build_public_view(new, now)
    warnings = tuple(dict.fromkeys(after_view.warnings + ("conversion_frontmatter_only",)))
    return PreparedWrite(identity, "update", raw, candidate, before_view, after_view,
                         candidate != raw, False, False, old.eol, new.eol, new.has_bom, warnings)
