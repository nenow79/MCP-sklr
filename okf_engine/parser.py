"""Bezpieczny parser dokumentu OKF: bajty UTF-8 -> prywatny ParsedDocument.

YAML jest komponowany do drzewa węzłów (yaml.compose), a nie ładowany konstruktorami. Własne
przejście po drzewie liczy węzły, głębokość i efektywną ekspansję aliasów, wykrywa cykle, duplikaty
kluczy, tagi i klucz scalający <<, oraz rozstrzyga skalary według profilu v1 (docs/specs/okf-support.md
sekcja 4.1). Parser nie naprawia błędnego YAML i nie zwraca surowych komunikatów YAML.
"""
import math
import re
from typing import Any, Dict, List, Optional

import yaml

from .models import (
    ERR_INVALID_OKF, ERR_SAFETY_LIMIT, ERR_UNSUPPORTED_PROFILE, DocumentIdentity, Issue,
    ParseLimits, ParsedDocument)
from .textlines import detect_eol, join_lines, split_lines

_TAG_PREFIX = "tag:yaml.org,2002:"
_STR_TAG = _TAG_PREFIX + "str"
_MAP_TAG = _TAG_PREFIX + "map"
_SEQ_TAG = _TAG_PREFIX + "seq"
_MERGE_TAG = _TAG_PREFIX + "merge"
_SCALAR_TAGS = frozenset(_TAG_PREFIX + name for name in ("str", "int", "float", "bool", "null", "timestamp"))

_NULLS = frozenset({"", "~", "null", "Null", "NULL"})
_TRUES = frozenset({"true", "True", "TRUE"})
_FALSES = frozenset({"false", "False", "FALSE"})
_INT_RE = re.compile(r"-?(?:0|[1-9][0-9]{0,17})")
_FLOAT_RE = re.compile(r"-?(?:0|[1-9][0-9]*)\.[0-9]+(?:[eE][+-]?[0-9]+)?")
_RESOLVER = yaml.resolver.Resolver()


class _Reject(Exception):
    def __init__(self, kind: str, reason: str, line: Optional[int] = None) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason
        self.line = line


def _file_line(node: yaml.Node) -> int:
    return node.start_mark.line + 2  # frontmatter zaczyna się w linii 2 pliku


def _scalar_value(node: yaml.ScalarNode) -> Any:
    """Profil skalarów v1: tekst poza jednoznacznym null/bool/liczbą dziesiętną."""
    value = node.value
    if node.style is not None:
        return value
    if node.tag == _STR_TAG and _RESOLVER.resolve(yaml.ScalarNode, value, (True, False)) != _STR_TAG:
        return value  # jawne !!str
    if value in _NULLS:
        return None
    if value in _TRUES:
        return True
    if value in _FALSES:
        return False
    if _INT_RE.fullmatch(value):
        return int(value)
    if len(value) <= 64 and _FLOAT_RE.fullmatch(value):
        number = float(value)
        if math.isfinite(number):
            return number
    return value


class _Builder:
    def __init__(self, limits: ParseLimits) -> None:
        self.limits = limits
        self.nodes = 0
        self.alias_refs = 0
        self.seen: set = set()
        self.stack: set = set()

    def _count(self) -> None:
        self.nodes += 1
        if self.nodes > self.limits.max_yaml_nodes:
            raise _Reject(ERR_SAFETY_LIMIT, "yaml_too_many_nodes")

    def build(self, node: yaml.Node, depth: int) -> Any:
        if depth > self.limits.max_yaml_depth:
            raise _Reject(ERR_SAFETY_LIMIT, "yaml_too_deep", _file_line(node))
        node_id = id(node)
        if node_id in self.stack:
            raise _Reject(ERR_UNSUPPORTED_PROFILE, "yaml_cycle", _file_line(node))
        if node_id in self.seen:
            self.alias_refs += 1
            if self.alias_refs > self.limits.max_alias_references:
                raise _Reject(ERR_SAFETY_LIMIT, "too_many_aliases", _file_line(node))
        else:
            self.seen.add(node_id)
        self._count()
        if isinstance(node, yaml.ScalarNode):
            self._check_scalar_tag(node)
            return _scalar_value(node)
        if isinstance(node, yaml.SequenceNode):
            if node.tag != _SEQ_TAG:
                raise _Reject(ERR_UNSUPPORTED_PROFILE, "unsupported_tag", _file_line(node))
            self.stack.add(node_id)
            try:
                return [self.build(child, depth + 1) for child in node.value]
            finally:
                self.stack.discard(node_id)
        if isinstance(node, yaml.MappingNode):
            if node.tag != _MAP_TAG:
                raise _Reject(ERR_UNSUPPORTED_PROFILE, "unsupported_tag", _file_line(node))
            self.stack.add(node_id)
            try:
                return self._build_mapping(node, depth)
            finally:
                self.stack.discard(node_id)
        raise _Reject(ERR_UNSUPPORTED_PROFILE, "unsupported_node", _file_line(node))

    @staticmethod
    def _check_scalar_tag(node: yaml.ScalarNode) -> None:
        if node.tag == _MERGE_TAG:
            raise _Reject(ERR_UNSUPPORTED_PROFILE, "merge_key", _file_line(node))
        if node.tag not in _SCALAR_TAGS:
            raise _Reject(ERR_UNSUPPORTED_PROFILE, "unsupported_tag", _file_line(node))

    def _build_mapping(self, node: yaml.MappingNode, depth: int) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                raise _Reject(ERR_UNSUPPORTED_PROFILE, "non_text_key", _file_line(key_node))
            self._count()
            self._check_scalar_tag(key_node)
            key = _scalar_value(key_node)
            if not isinstance(key, str):
                raise _Reject(ERR_UNSUPPORTED_PROFILE, "non_text_key", _file_line(key_node))
            if key in result:
                raise _Reject(ERR_INVALID_OKF, "duplicate_key", _file_line(key_node))
            result[key] = self.build(value_node, depth + 1)
        return result


def _failed(identity: DocumentIdentity, size: int, issue: Issue, **extra: Any) -> ParsedDocument:
    return ParsedDocument(identity=identity, size_bytes=size, errors=(issue,), **extra)


def _yaml_error_issue(exc: yaml.YAMLError) -> Issue:
    reason = "multiple_documents" if "single document" in str(exc) else "yaml_syntax"
    mark = getattr(exc, "problem_mark", None)
    line = mark.line + 2 if mark is not None else None
    return Issue(ERR_INVALID_OKF, reason, line)


def parse_document(data: bytes, identity: DocumentIdentity,
                   limits: ParseLimits = ParseLimits()) -> ParsedDocument:
    """Parsuje ograniczone bajty dokumentu; nie czyta dysku i nie zależy od zegara."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")
    raw = bytes(data)
    size = len(raw)
    if size > limits.max_document_bytes:
        return _failed(identity, size, Issue(ERR_SAFETY_LIMIT, "document_too_large"))
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _failed(identity, size, Issue(ERR_INVALID_OKF, "not_utf8"))
    if has_bom:
        text = text[1:]
    warnings: List[str] = ["bom"] if has_bom else []

    lines = split_lines(text)
    eol = detect_eol(lines)
    if eol == "mixed":
        warnings.append("mixed_eol")
    common = dict(warnings=tuple(warnings), has_bom=has_bom, eol=eol)

    if not lines or lines[0][0].rstrip(" \t") != "---":
        return _failed(identity, size, Issue(ERR_INVALID_OKF, "missing_frontmatter", 1),
                       body=text, body_start_line=1, **common)

    close: Optional[int] = None
    fm_bytes = 0
    for index in range(1, len(lines)):
        content, line_eol = lines[index]
        if content.rstrip(" \t") == "---":
            close = index
            break
        fm_bytes += len(content.encode("utf-8")) + len(line_eol)
        if fm_bytes > limits.max_frontmatter_bytes:
            return _failed(identity, size, Issue(ERR_SAFETY_LIMIT, "frontmatter_too_large"), **common)
    if close is None:
        return _failed(identity, size, Issue(ERR_INVALID_OKF, "unterminated_frontmatter", 1), **common)

    yaml_text = join_lines(lines[1:close])
    body = join_lines(lines[close + 1:])
    body_start_line = close + 2
    common.update(body=body, body_start_line=body_start_line)

    try:
        root = yaml.compose(yaml_text, Loader=yaml.SafeLoader)
    except RecursionError:
        return _failed(identity, size, Issue(ERR_SAFETY_LIMIT, "yaml_too_deep"), **common)
    except MemoryError:
        return _failed(identity, size, Issue(ERR_SAFETY_LIMIT, "yaml_memory"), **common)
    except yaml.YAMLError as exc:
        return _failed(identity, size, _yaml_error_issue(exc), **common)
    if not isinstance(root, yaml.MappingNode):
        return _failed(identity, size, Issue(ERR_INVALID_OKF, "yaml_not_mapping", 2), **common)

    try:
        metadata = _Builder(limits).build(root, 1)
    except _Reject as rejection:
        return _failed(identity, size, Issue(rejection.kind, rejection.reason, rejection.line), **common)

    errors: List[Issue] = []
    type_value = metadata.get("type")
    if "type" not in metadata:
        errors.append(Issue(ERR_INVALID_OKF, "missing_type", 2))
    elif not isinstance(type_value, str) or not type_value.strip():
        errors.append(Issue(ERR_INVALID_OKF, "type_not_string", 2))
    return ParsedDocument(identity=identity, size_bytes=size, errors=tuple(errors), metadata=metadata, **common)
