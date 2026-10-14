"""Publiczny widok dokumentu OKF po maskowaniu (warunek C1/C2 z kontraktu, sekcja 4.3).

Istniejący server/secrets_mask.py nie jest używany: maskuje tylko pliki sekretów po nazwie, jego
wzorce są szersze (KEY, AUTH) i zmieniają liczbę linii. Tutaj maskowanie działa niezależnie od
rozszerzenia, zachowuje liczbę i kolejność linii oraz zwraca mapę zamaskowanych linii.

Heurystyka, nie DLP: nazwy tokenów rozpoznawane są po segmentach (password, passwd, passphrase, token,
secret, credential(s), apikey oraz pary api/private/access/secret + key), więc author/monkey/max_tokens
nie są maskowane, a token_count, secret_ref czy zdanie "Token: ..." - tak (znane false positives).
Proza i nietypowe sekrety nie są wykrywane.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .models import BLOCK_MASK, ERR_INVALID_OKF, MASK, DocumentIdentity, ParsedDocument
from .signals import evaluate_signals, normalize_public_metadata
from .textlines import split_lines

ERR_IDENTITY_UNSAFE = "IDENTITY_UNSAFE"
MAX_MASKED_PATHS = 50

_SECRET_TOKENS = frozenset(
    {"password", "passwd", "passphrase", "token", "secret", "credential", "credentials", "apikey"})
_SECRET_PAIRS = frozenset({("api", "key"), ("private", "key"), ("access", "key"), ("secret", "key")})
_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")
_SPLIT_RE = re.compile(r"[^a-z0-9]+")

# myślnik w prefiksie tylko jako znacznik listy ("- "), aby uniknąć kwadratowego backtrackingu
_ASSIGN_RE = re.compile(
    r"^(?P<lead>(?:[\s>*+`|]|-(?=\s))*)(?P<q1>[\"']?)(?P<name>[A-Za-z0-9_.\-]+)(?P<q2>[\"']?)"
    r"(?P<sep>\s*[:=]\s*)(?P<value>.*)$")
_BLOCK_RE = re.compile(r"[|>][+\-0-9]*(?:\s+#.*)?")
_URL_RE = re.compile(
    r"(?<![A-Za-z0-9+.\-])(?P<pre>[A-Za-z][A-Za-z0-9+.\-]*://[^\s/@:]*:)(?P<pw>[^\s/@]+)(?=@)")
_BEARER_RE = re.compile(r"(?P<pre>\bBearer\s+)(?P<tok>[A-Za-z0-9._~+/\-]{8,}=*)")
_PEM_KIND = r"[A-Z0-9 ]*PRIVATE KEY"
_PEM_BEGIN_RE = re.compile(r"-----BEGIN " + _PEM_KIND + r"-----")
_PEM_END_RE = re.compile(r"-----END " + _PEM_KIND + r"-----")
_PEM_INLINE_RE = re.compile(
    r"(?P<head>-----BEGIN " + _PEM_KIND + r"-----)(?P<body>.*?)(?P<tail>-----END " + _PEM_KIND + r"-----)")


def is_secret_name(name: Any) -> bool:
    if not isinstance(name, str):
        return False
    parts = [p for p in _SPLIT_RE.split(_CAMEL_RE.sub(r"\1_\2", name).lower()) if p]
    if any(part in _SECRET_TOKENS for part in parts):
        return True
    return any(pair in _SECRET_PAIRS for pair in zip(parts, parts[1:]))


@dataclass(frozen=True)
class MaskedText:
    text: str
    count: int  # liczba operacji maskowania; 0 = tekst bez zmian
    lines: Tuple[int, ...]  # numery (od 1) linii zmienionych względem wejścia

    @property
    def masked(self) -> bool:
        return self.count > 0


def _mask_pem(lines: List[str]) -> int:
    """Maskuje klucze prywatne w miejscu; liczba linii pozostaje bez zmian."""
    count = 0
    index = 0
    total = len(lines)

    def inline(match: "re.Match[str]") -> str:
        nonlocal count
        body = match.group("body").strip()
        if not body or body == BLOCK_MASK:
            return match.group(0)
        count += 1
        return f"{match.group('head')} {BLOCK_MASK} {match.group('tail')}"

    while index < total:
        line = _PEM_INLINE_RE.sub(inline, lines[index])
        lines[index] = line
        begin = _PEM_BEGIN_RE.search(line)
        if begin is None or _PEM_END_RE.search(line, begin.end()) is not None:
            index += 1
            continue
        end: Optional[int] = None
        for probe in range(index + 1, total):
            if _PEM_END_RE.search(lines[probe]) is not None:
                end = probe
                break
        stop = end if end is not None else total
        rest = line[begin.end():]
        prefix = ""
        end_match = None
        if end is not None:
            end_match = _PEM_END_RE.search(lines[end])
            prefix = lines[end][:end_match.start()]
        parts = [lines[k].strip() for k in range(index + 1, stop) if lines[k].strip()]
        parts += [part.strip() for part in (prefix, rest) if part.strip()]
        if parts and parts != [BLOCK_MASK]:
            count += 1
            lines[index] = line[:begin.end()]
            if stop > index + 1:
                lines[index + 1] = BLOCK_MASK
                for k in range(index + 2, stop):
                    lines[k] = ""
            else:
                lines[index] += " " + BLOCK_MASK
            if end is not None and end_match is not None:
                lines[end] = lines[end][end_match.start():]
        index = (end + 1) if end is not None else total
    return count


def _mask_assignments(lines: List[str]) -> int:
    count = 0

    def url(match: "re.Match[str]") -> str:
        nonlocal count
        if match.group("pw") == MASK:
            return match.group(0)
        count += 1
        return match.group("pre") + MASK

    def bearer(match: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        return match.group("pre") + MASK

    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        match = _ASSIGN_RE.match(line)
        if match is not None and is_secret_name(match.group("name")):
            core = match.group("value").strip()
            if core and core.strip("\"'`") != MASK:
                if _BLOCK_RE.fullmatch(core):
                    indent = len(line) - len(line.lstrip())
                    probe = index + 1
                    block: List[int] = []
                    while probe < total:
                        candidate = lines[probe]
                        if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= indent:
                            break
                        if candidate.strip():
                            block.append(probe)
                        probe += 1
                    if any(lines[k].strip() != MASK for k in block):
                        count += 1
                        for k in block:
                            lines[k] = lines[k][:len(lines[k]) - len(lines[k].lstrip())] + MASK
                    index = probe
                    continue
                quote = '"' if core[:1] in "\"'" else ""
                trailing = "," if core.endswith(",") else ""
                line = line[:match.start("value")] + f"{quote}{MASK}{quote}{trailing}"
                count += 1
        line = _URL_RE.sub(url, line)
        line = _BEARER_RE.sub(bearer, line)
        lines[index] = line
        index += 1
    return count


def mask_text(text: str) -> MaskedText:
    """Maskuje sekrety w dowolnym tekście (też .md i bloki kodu), zachowując liczbę linii."""
    original = split_lines(text)
    before = [content for content, _ in original]
    lines = list(before)
    count = _mask_pem(lines)
    count += _mask_assignments(lines)
    if count == 0:
        return MaskedText(text, 0, ())
    changed = tuple(i + 1 for i, (old, new) in enumerate(zip(before, lines)) if old != new)
    result = "".join(content + eol for content, (_, eol) in zip(lines, original))
    return MaskedText(result, count, changed)


def _is_maskable(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    return not (isinstance(value, str) and not value.strip())


def mask_metadata(value: Any) -> Tuple[Any, int, Tuple[str, ...]]:
    """Maskuje drzewo metadanych (dict/list/str); zwraca kopię, liczbę operacji i ścieżki pól."""
    paths: List[str] = []
    counter = [0]

    def record(path: str) -> None:
        counter[0] += 1
        if len(paths) < MAX_MASKED_PATHS:
            paths.append(path or "$")

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            out: Dict[str, Any] = {}
            for key, item in node.items():
                child = f"{path}.{key}" if path else str(key)
                if is_secret_name(key) and _is_maskable(item):
                    if not (isinstance(item, str) and item.strip() == MASK):
                        record(child)
                    out[key] = MASK  # cała wartość (także struktura) jest ukrywana
                else:
                    out[key] = walk(item, child)
            return out
        if isinstance(node, list):
            return [walk(item, f"{path}[{i}]") for i, item in enumerate(node)]
        if isinstance(node, str):
            masked = mask_text(node)
            if masked.count:
                record(path)
                return masked.text
        return node

    return walk(value, ""), counter[0], tuple(paths)


class PublicViewError(Exception):
    """Brak publicznego widoku; atrybut code to bezpieczny kod (INVALID_OKF / IDENTITY_UNSAFE)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PublicDocumentView:
    identity: DocumentIdentity
    title: str
    description: Optional[str]
    type: str
    tags: Tuple[str, ...]
    status: Optional[str]
    trust_tier: str
    stale: Optional[bool]
    warnings: Tuple[str, ...]
    metadata: Dict[str, Any]
    body: str
    body_start_line: int  # linia pliku, od której zaczyna się body
    masked: bool
    masked_metadata_paths: Tuple[str, ...]
    masked_body_lines: Tuple[int, ...]  # numery linii pliku (od 1)


def _identity_is_unsafe(identity: DocumentIdentity) -> bool:
    return any(mask_text(part).count for part in identity.concept_id.split("/"))


def build_public_view(doc: ParsedDocument, now: datetime) -> PublicDocumentView:
    """Buduje publiczny widok wyłącznie z maskowanych danych; now (ze strefą) wyznacza stale."""
    if not doc.ok or doc.metadata is None:
        raise PublicViewError(ERR_INVALID_OKF)
    if _identity_is_unsafe(doc.identity):
        raise PublicViewError(ERR_IDENTITY_UNSAFE)
    masked_meta, meta_count, meta_paths = mask_metadata(doc.metadata)
    public_meta = normalize_public_metadata(masked_meta)
    signals = evaluate_signals(public_meta, now)
    body = mask_text(doc.body)
    title = public_meta.get("title")
    if not (isinstance(title, str) and title.strip()):
        title = doc.identity.concept_id.rsplit("/", 1)[-1]
    description = public_meta.get("description")
    raw_tags = public_meta.get("tags")
    tags = tuple(t for t in raw_tags if isinstance(t, str)) if isinstance(raw_tags, list) else ()
    return PublicDocumentView(
        identity=doc.identity,
        title=title,
        description=description if isinstance(description, str) else None,
        type=str(public_meta["type"]),
        tags=tags,
        status=signals.status,
        trust_tier=signals.trust_tier,
        stale=signals.stale,
        warnings=tuple(doc.warnings) + tuple(w for w in signals.warnings if w not in doc.warnings),
        metadata=public_meta,
        body=body.text,
        body_start_line=doc.body_start_line,
        masked=meta_count > 0 or body.count > 0,
        masked_metadata_paths=meta_paths,
        masked_body_lines=tuple(doc.body_start_line + line - 1 for line in body.lines),
    )


def render_public_yaml(metadata: Dict[str, Any]) -> str:
    """Syntetyczny YAML z publicznych metadanych (np. do diffu); nie jest kopią źródłowego bloku."""
    dumped = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True,
                            default_flow_style=False, width=1000)
    return "# widok publiczny (syntetyczny): komentarze i formatowanie źródła nie są zachowane\n" + dumped
