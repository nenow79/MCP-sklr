"""Wyszukiwanie w publicznym indeksie OKF: terms, phrase i browse (C2b).

Wejściem jest wyłącznie indeks publicznych dokumentów po maskowaniu; surowe bajty
i wartości sekretów nie biorą udziału w dopasowaniu. Normalizacja: NFC + casefold,
bez stemmingu, usuwania polskich diakrytyków i synonimów. Słowo to ciąg liter i
cyfr (podkreślnik rozdziela). terms: każde słowo zapytania musi wystąpić jako całe
słowo w publicznym dokumencie (tytuł, opis, tagi, pozostałe metadane, nagłówki lub
treść). phrase: dosłowna fraza po normalizacji i ściśnięciu białych znaków, szukana
w jednym polu lub jednej sekcji, bez sklejania granic pól. Znacznik [ZAMASKOWANO]
nie jest słowem wyszukiwania.

Ranking (kontrakt 6.2): dla każdego słowa suma wag pól, w których występuje:
tytuł 8, tagi 6, opis 4, nagłówek najlepszej sekcji 3, treść najlepszej sekcji 1,
każde pole najwyżej raz na słowo. Pozostałe metadane kwalifikują dokument, ale nie
dodają punktów. Najlepsza sekcja: najwięcej dopasowanych słów, potem punkty, potem
najwcześniejsza. Remis: bundle_id, concept_id. Status i zaufanie nie dodają punktów.

Przed zwrotem każdy trafiony dokument przechodzi DocumentReader.check_document
(aktualny bundle, wiązanie i dostęp); wyszukiwanie może jednak korzystać z
wcześniejszego skanu, więc nie potwierdza świeżości treści (robi to get).
Serwis (C2c) dobiera bundles, zakres i politykę odświeżania.
"""
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from .documents import DocumentError, DocumentReader, PublicDocument
from .index import IndexEntry, IndexState, KnowledgeIndex
from .models import BLOCK_MASK
from .textlines import join_lines, split_lines

ERR_INVALID_ARGUMENT = "INVALID_ARGUMENT"
MODE_TERMS = "terms"
MODE_PHRASE = "phrase"
MODES = (MODE_TERMS, MODE_PHRASE)
MAX_QUERY = 1000
MAX_TAGS = 20
MAX_FILTER = 100
MIN_LIMIT, MAX_LIMIT = 1, 50
MIN_BUDGET, MAX_BUDGET = 1000, 80000
WEIGHT_TITLE, WEIGHT_TAGS, WEIGHT_DESCRIPTION, WEIGHT_HEADING, WEIGHT_BODY = 8, 6, 4, 3, 1
EXCERPT_LENGTH = 240
EXCERPT_LEAD = 60
MAX_TITLE = 300
MAX_DESCRIPTION = 600
MAX_HEADING = 300
MAX_TAGS_OUT = 30
MAX_TAG_OUT = 100
MAX_META_NODES = 10000
MAX_BUDGET_SKIPS = 5
SCOPE_CENTRAL = "central"  # w etapie C wszystkie bundles są centralne
ELLIPSIS = "…"

_WORD = re.compile(r"[^\W_]+")
_SPACE = re.compile(r"\s+")


class SearchError(ValueError):
    """Niepoprawny argument; reason to nazwa pola, bez treści zapytania."""

    def __init__(self, reason: str) -> None:
        super().__init__(ERR_INVALID_ARGUMENT + ": " + reason)
        self.code = ERR_INVALID_ARGUMENT
        self.reason = reason


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFC", text).casefold()
    return unicodedata.normalize("NFC", folded)


def tokens(text: str) -> List[str]:
    return _WORD.findall(normalize(text.replace(BLOCK_MASK, " ")))


def phrase_text(text: str) -> str:
    return _SPACE.sub(" ", normalize(text.replace(BLOCK_MASK, " "))).strip()


def _bounded(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise SearchError(name)
    return value


def _filter(value: Any, name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_FILTER:
        raise SearchError(name)
    return normalize(value.strip())


def _tags(value: Any) -> FrozenSet[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, (list, tuple)) or len(value) > MAX_TAGS:
        raise SearchError("tags")
    result = set()
    for tag in value:
        if not isinstance(tag, str) or not tag.strip() or len(tag) > MAX_FILTER:
            raise SearchError("tags")
        result.add(normalize(tag.strip()))
    return frozenset(result)


@dataclass(frozen=True)
class SearchRequest:
    terms: Tuple[str, ...] = field(repr=False)
    phrase: str = field(repr=False)
    mode: str
    browse: bool
    type: Optional[str]
    status: Optional[str]
    tags: FrozenSet[str]
    include_deprecated: bool
    limit: int
    max_total_chars: int


def parse_request(query: Any = "", query_mode: Any = MODE_TERMS, browse: Any = False,
                  type: Any = None, tags: Any = None, status: Any = None,
                  include_deprecated: Any = False, limit: Any = 10,
                  max_total_chars: Any = 12000, explicit_bundle: bool = False) -> SearchRequest:
    """Waliduje argumenty (kontrakt 6.2). Błędy nie zawierają treści zapytania.

    explicit_bundle: wywołujący wskazał bundle; dopuszcza browse bez filtrów.
    """
    if not isinstance(query, str) or len(query) > MAX_QUERY:
        raise SearchError("query")
    if query_mode not in MODES:
        raise SearchError("query_mode")
    if not isinstance(browse, bool):
        raise SearchError("browse")
    if not isinstance(include_deprecated, bool):
        raise SearchError("include_deprecated")
    limit = _bounded(limit, "limit", MIN_LIMIT, MAX_LIMIT)
    budget = _bounded(max_total_chars, "max_total_chars", MIN_BUDGET, MAX_BUDGET)
    type_filter = _filter(type, "type")
    status_filter = _filter(status, "status")
    tag_filter = _tags(tags)
    terms: Tuple[str, ...] = ()
    phrase = ""
    if browse:
        if query.strip():
            raise SearchError("browse_with_query")
        if not (explicit_bundle or type_filter or status_filter or tag_filter):
            raise SearchError("browse_scope")
    else:
        words = tokens(query)
        if not words:
            raise SearchError("query_terms")  # puste lub same znaki specjalne (np. ***)
        if query_mode == MODE_TERMS:
            terms = tuple(dict.fromkeys(words))
        else:
            phrase = phrase_text(query)
    return SearchRequest(terms, phrase, query_mode, browse, type_filter, status_filter,
                         tag_filter, include_deprecated, limit, budget)


@dataclass(frozen=True, eq=False)
class PreparedSection:
    section_id: str
    heading: Optional[str]
    has_heading: bool
    start_line: int
    end_line: int
    heading_tokens: FrozenSet[str] = field(repr=False)
    body_tokens: FrozenSet[str] = field(repr=False)
    heading_text: str = field(repr=False)
    body_text: str = field(repr=False)


@dataclass(frozen=True, eq=False)
class PreparedDocument:
    """Znormalizowane pochodne PUBLICZNEGO tekstu (indeks trzyma je obok dokumentu)."""
    type_norm: str
    status_norm: Optional[str]
    tag_norm: FrozenSet[str] = field(repr=False)
    title_tokens: FrozenSet[str] = field(repr=False)
    tag_tokens: FrozenSet[str] = field(repr=False)
    description_tokens: FrozenSet[str] = field(repr=False)
    all_tokens: FrozenSet[str] = field(repr=False)
    title_text: str = field(repr=False)
    tag_texts: Tuple[str, ...] = field(repr=False)
    description_text: str = field(repr=False)
    meta_texts: Tuple[str, ...] = field(repr=False)
    sections: Tuple[PreparedSection, ...] = field(repr=False)


def _meta_strings(metadata: Any) -> List[str]:
    found: List[str] = []
    stack = [metadata]
    visited = 0
    while stack and visited < MAX_META_NODES:
        node = stack.pop()
        visited += 1
        if isinstance(node, str):
            found.append(node)
        elif isinstance(node, Mapping):
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    return found


def prepare_document(document: PublicDocument) -> PreparedDocument:
    """Do KnowledgeIndex(prepare=...). Linie dzielimy raz, bez get_section na sekcję."""
    view = document.view
    lines = split_lines(view.body)
    first_line = view.body_start_line
    sections: List[PreparedSection] = []
    for entry in document.outline.entries:
        chunk = lines[entry.start_line - first_line:entry.end_line - first_line + 1]
        has_heading = entry.level > 0
        body = join_lines(chunk[1:] if has_heading else chunk)
        heading = entry.heading or ""
        sections.append(PreparedSection(
            entry.id, entry.heading, has_heading, entry.start_line, entry.end_line,
            frozenset(tokens(heading)), frozenset(tokens(body)),
            phrase_text(heading), phrase_text(body)))
    description = view.description or ""
    meta = _meta_strings(view.metadata)
    title_tokens = frozenset(tokens(view.title))
    tag_tokens = frozenset(word for tag in view.tags for word in tokens(tag))
    description_tokens = frozenset(tokens(description))
    meta_tokens = frozenset(word for text in meta for word in tokens(text))
    all_tokens = frozenset().union(
        title_tokens, tag_tokens, description_tokens, meta_tokens,
        *(section.heading_tokens for section in sections),
        *(section.body_tokens for section in sections))
    return PreparedDocument(
        normalize(view.type), normalize(view.status) if view.status else None,
        frozenset(normalize(tag) for tag in view.tags),
        title_tokens, tag_tokens, description_tokens, all_tokens,
        phrase_text(view.title), tuple(phrase_text(tag) for tag in view.tags),
        phrase_text(description), tuple(phrase_text(text) for text in meta),
        tuple(sections))


def _evaluate_terms(prepared: PreparedDocument, terms: Tuple[str, ...]):
    for term in terms:
        if term not in prepared.all_tokens:
            return None
    score = 0
    for term in terms:
        if term in prepared.title_tokens:
            score += WEIGHT_TITLE
        if term in prepared.tag_tokens:
            score += WEIGHT_TAGS
        if term in prepared.description_tokens:
            score += WEIGHT_DESCRIPTION
    best: Optional[PreparedSection] = None
    best_key: Optional[Tuple[int, int]] = None
    for section in prepared.sections:  # kolejność dokumentu: remis wygrywa wcześniejsza
        matched = sum(1 for term in terms
                      if term in section.heading_tokens or term in section.body_tokens)
        if not matched:
            continue
        points = (sum(WEIGHT_HEADING for term in terms if term in section.heading_tokens)
                  + sum(WEIGHT_BODY for term in terms if term in section.body_tokens))
        key = (matched, points)
        if best_key is None or key > best_key:
            best, best_key = section, key
    if best_key is not None:
        score += best_key[1]
    return score, best


def _evaluate_phrase(prepared: PreparedDocument, phrase: str):
    in_title = phrase in prepared.title_text
    in_tags = any(phrase in tag for tag in prepared.tag_texts)
    in_description = phrase in prepared.description_text
    in_meta = any(phrase in text for text in prepared.meta_texts)
    best: Optional[PreparedSection] = None
    best_points = 0
    for section in prepared.sections:
        in_heading = phrase in section.heading_text
        in_body = phrase in section.body_text
        if not (in_heading or in_body):
            continue
        points = (WEIGHT_HEADING if in_heading else 0) + (WEIGHT_BODY if in_body else 0)
        if best is None or points > best_points:
            best, best_points = section, points
    if not (in_title or in_tags or in_description or in_meta or best is not None):
        return None
    score = ((WEIGHT_TITLE if in_title else 0) + (WEIGHT_TAGS if in_tags else 0)
             + (WEIGHT_DESCRIPTION if in_description else 0) + best_points)
    return score, best


def _evaluate(prepared: PreparedDocument, request: SearchRequest):
    """(score, najlepsza sekcja lub None) albo None, gdy dokument nie pasuje."""
    if request.type is not None and prepared.type_norm != request.type:
        return None
    if request.status is not None and prepared.status_norm != request.status:
        return None
    if request.tags and not request.tags <= prepared.tag_norm:
        return None
    if request.browse:
        return 0, None
    if request.mode == MODE_TERMS:
        return _evaluate_terms(prepared, request.terms)
    return _evaluate_phrase(prepared, request.phrase)


@dataclass(frozen=True, eq=False)
class SearchHit:
    bundle_id: str
    scope: str
    concept_id: str
    title: str = field(repr=False)
    description: Optional[str] = field(repr=False)
    type: str = field(repr=False)
    tags: Tuple[str, ...] = field(repr=False)
    status: Optional[str]
    trust_tier: str
    stale: Optional[bool]
    warnings: Tuple[str, ...]
    section_id: Optional[str]
    heading: Optional[str] = field(repr=False)
    start_line: Optional[int]
    end_line: Optional[int]
    excerpt: str = field(repr=False)
    revision: str = field(repr=False)
    masked: bool
    content_truncated: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id, "scope": self.scope, "concept_id": self.concept_id,
            "title": self.title, "description": self.description, "type": self.type,
            "tags": list(self.tags), "status": self.status, "trust_tier": self.trust_tier,
            "stale": self.stale, "warnings": list(self.warnings), "section_id": self.section_id,
            "heading": self.heading, "start_line": self.start_line, "end_line": self.end_line,
            "excerpt": self.excerpt, "revision": self.revision, "masked": self.masked,
            "content_truncated": self.content_truncated}


@dataclass(frozen=True, eq=False)
class SearchResult:
    hits: Tuple[SearchHit, ...]
    partial: bool  # niepełny zakres: indeks niegotowy/częściowy, utrata dostępu, budżet czasu
    truncated: bool  # limit wyników/odpowiedzi/czasu
    reasons: Tuple[str, ...]
    index_states: Dict[str, IndexState]
    skipped_deprecated: int  # trafienia pominięte przez politykę deprecated

    @property
    def count(self) -> int:
        return len(self.hits)


def _cap(text: str, limit: int) -> Tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit - 1] + ELLIPSIS, True


def _pattern(request: SearchRequest) -> Optional["re.Pattern[str]"]:
    if request.browse:
        return None
    if request.mode == MODE_TERMS:
        words = sorted(request.terms, key=len, reverse=True)
        return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(word) for word in words) + r")(?!\w)",
                          re.IGNORECASE)
    return re.compile(r"\s+".join(re.escape(word) for word in request.phrase.split(" ")),
                      re.IGNORECASE)


def _snippet(text: str, pattern: Optional["re.Pattern[str]"]) -> str:
    cleaned = _SPACE.sub(" ", text).strip()
    if not cleaned:
        return ""
    start = 0
    if pattern is not None:
        found = pattern.search(cleaned)
        if found is not None:
            start = max(0, found.start() - EXCERPT_LEAD)
    end = start + EXCERPT_LENGTH
    return (ELLIPSIS if start > 0 else "") + cleaned[start:end] + (ELLIPSIS if end < len(cleaned) else "")


def _make_hit(document: PublicDocument, best: Optional[PreparedSection],
              pattern: Optional["re.Pattern[str]"]) -> SearchHit:
    view = document.view
    cut = False
    title, flag = _cap(view.title, MAX_TITLE)
    cut = cut or flag
    description: Optional[str] = None
    if view.description is not None:
        description, flag = _cap(view.description, MAX_DESCRIPTION)
        cut = cut or flag
    tags: List[str] = []
    for tag in view.tags[:MAX_TAGS_OUT]:
        text, flag = _cap(tag, MAX_TAG_OUT)
        tags.append(text)
        cut = cut or flag
    cut = cut or len(view.tags) > MAX_TAGS_OUT
    heading: Optional[str] = None
    section_id = start_line = end_line = None
    if best is None:
        excerpt = _snippet(view.description or "", None)
    else:
        section_id, start_line, end_line = best.section_id, best.start_line, best.end_line
        if best.heading is not None:
            heading, flag = _cap(best.heading, MAX_HEADING)
            cut = cut or flag
        text = document.outline.get_section(best.section_id, "direct").content
        if best.has_heading:
            text = join_lines(split_lines(text)[1:])
        excerpt = _snippet(text, pattern) or _snippet(best.heading or "", pattern)
    warnings = tuple(view.warnings) + tuple(w for w in document.outline.warnings
                                            if w not in view.warnings)
    return SearchHit(
        view.identity.bundle_id, SCOPE_CENTRAL, view.identity.concept_id, title, description,
        view.type, tuple(tags), view.status, view.trust_tier, view.stale, warnings,
        section_id, heading, start_line, end_line, excerpt, document.revision, view.masked, cut)


def _json_size(hit: SearchHit) -> int:
    return len(json.dumps(hit.to_dict(), ensure_ascii=False))


def _fit(hit: SearchHit, used: int, budget: int, first: bool) -> Optional[Tuple[SearchHit, int]]:
    """Mieści wynik w budżecie JSON: najpierw skraca excerpt, potem odrzuca (None).

    Nigdy nie przecina identyfikatorów ani rewizji. used liczy nawiasy listy i separatory.
    """
    extra = 0 if first else 2
    current = hit
    while True:
        size = _json_size(current)
        if used + extra + size <= budget:
            return current, extra + size
        if not current.excerpt:
            return None
        over = used + extra + size - budget
        keep = max(0, len(current.excerpt) - max(over, 1) - 1)
        text = current.excerpt[:keep].rstrip()
        current = replace(current, excerpt=(text + ELLIPSIS) if text else "", content_truncated=True)


def search(index: KnowledgeIndex, reader: DocumentReader, bundle_ids: Sequence[str],
           request: SearchRequest, budget_s: float,
           clock: Callable[[], float] = time.monotonic) -> SearchResult:
    """Przeszukuje wskazane bundles. Dostęp jest sprawdzany przed skanem indeksu i ponownie
    dla każdego zwracanego trafienia; niedostępne wpisy znikają z indeksu."""
    bundles = sorted(set(bundle_ids))
    if not bundles:
        return SearchResult((), False, False, ("no_available_bundles",), {}, 0)
    deadline = clock() + budget_s
    reasons = set()
    partial = truncated = False
    skipped_deprecated = 0
    ranked: List[Tuple[int, str, str, IndexEntry, Optional[PreparedSection]]] = []
    exhausted = False
    for bundle_id in bundles:
        try:
            reader.current_bundle(bundle_id)
        except DocumentError:
            index.invalidate(bundle_id)
            partial = True
            reasons.add("bundle_unavailable")
            continue
        generation = index.snapshot(bundle_id)
        if generation is None:
            partial = True
            reasons.add("index_not_ready")
            continue
        if generation.partial:
            partial = True
            reasons.update(generation.reasons)
        for entry in generation.entries:
            if clock() >= deadline:
                exhausted = True
                break
            prepared = entry.prepared if entry.prepared is not None else prepare_document(entry.document)
            outcome = _evaluate(prepared, request)
            if outcome is None:
                continue
            if (not request.include_deprecated and request.status != "deprecated"
                    and entry.document.view.status == "deprecated"):
                skipped_deprecated += 1
                continue
            score, best = outcome
            ranked.append((-score, bundle_id, entry.document.view.identity.concept_id, entry, best))
        if exhausted:
            partial = truncated = True
            reasons.add("search_budget")
            break
    ranked.sort(key=lambda item: item[:3])
    pattern = _pattern(request)
    hits: List[SearchHit] = []
    used = 2  # nawiasy listy JSON
    dead = set()
    budget_skips = 0
    for _, bundle_id, concept_id, entry, best in ranked:
        if len(hits) >= request.limit:
            truncated = True
            reasons.add("result_limit")
            break
        if bundle_id in dead:
            continue
        if clock() >= deadline:
            partial = truncated = True
            reasons.add("search_budget")
            break
        try:
            reader.check_document(entry.document)
        except DocumentError as exc:
            partial = True
            if exc.code == "BUNDLE_UNAVAILABLE":
                index.invalidate(bundle_id)
                dead.add(bundle_id)
                reasons.add("bundle_unavailable")
            else:
                index.remove_document(bundle_id, concept_id)
                reasons.add("document_unavailable")
            continue
        fitted = _fit(_make_hit(entry.document, best, pattern), used, request.max_total_chars,
                      first=not hits)
        if fitted is None:
            truncated = True
            reasons.add("response_budget")
            budget_skips += 1
            if budget_skips >= MAX_BUDGET_SKIPS:
                break
            continue
        hit, size = fitted
        hits.append(hit)
        used += size
        budget_skips = 0
    states = {bundle_id: index.state(bundle_id) for bundle_id in bundles}
    return SearchResult(tuple(hits), partial, truncated, tuple(sorted(reasons)), states,
                        skipped_deprecated)
