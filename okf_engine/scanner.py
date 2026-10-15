"""Skan bundle OKF: inwentaryzacja plików i świeży odczyt publicznych dokumentów.

Skaner nie buduje indeksu i niczego nie zapisuje. Wynik zawiera publiczne
dokumenty z DocumentReader oraz liczniki bez ścieżek i treści. Budżet czasu jest
kooperacyjny: nie przerywa blokującego I/O. Linki, junctiony i reparse points
są pomijane. Katalogi ukryte (nazwa od kropki, np. .git) nie są skanowane.
Reserved index.md/log.md są liczone osobno i nie są czytane jako koncepty.
Utrata dostępu do bundle w trakcie skanu przerywa go wyjątkiem DocumentError.
"""
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from .documents import DocumentError, DocumentReader, PublicDocument
from .limits import KnowledgeLimits
from .models import DocumentIdentity, IdentityError, validate_path_segment
from .paths import find_case_collisions, is_link

REASON_DOCUMENT_LIMIT = "document_limit"
REASON_DEPTH_LIMIT = "depth_limit"
REASON_TIME_BUDGET = "scan_budget"
REASON_IO_ERROR = "scan_io_error"
RESERVED_FILES = frozenset({"index.md", "log.md"})

_scandir = os.scandir


@dataclass(frozen=True, eq=False)
class ScanResult:
    documents: Tuple[PublicDocument, ...] = field(repr=False)
    partial: bool
    reasons: Tuple[str, ...]
    skipped: Dict[str, int]  # reserved, hidden, links, invalid_name, case_collision, unread
    errors: Dict[str, int]  # bezpieczne kody DocumentError -> liczba plików


def _inventory(root: Path, bundle_id: str, limits: KnowledgeLimits, clock: Callable[[], float],
               deadline: float, reasons: set, skipped: Counter) -> List[str]:
    found: List[str] = []
    stack: List[Tuple[Path, Tuple[str, ...]]] = [(root, ())]
    while stack:
        if clock() >= deadline:
            reasons.add(REASON_TIME_BUDGET)
            return found
        directory, segments = stack.pop()
        items = []
        try:
            with _scandir(directory) as entries:
                for entry in entries:
                    if clock() >= deadline:
                        reasons.add(REASON_TIME_BUDGET)
                        return found
                    items.append((entry.name, is_link(Path(entry.path)),
                                  entry.is_dir(follow_symlinks=False),
                                  entry.is_file(follow_symlinks=False)))
        except OSError:
            reasons.add(REASON_IO_ERROR)
            continue
        for name, link, is_dir, is_file in sorted(items, key=lambda item: (item[0].casefold(), item[0])):
            if link:
                skipped["links"] += 1
                continue
            if is_dir:
                if name.startswith("."):
                    skipped["hidden"] += 1
                    continue
                try:
                    validate_path_segment(name)
                except IdentityError:
                    skipped["invalid_name"] += 1
                    continue
                if len(segments) + 1 > limits.max_depth:
                    reasons.add(REASON_DEPTH_LIMIT)
                    continue
                stack.append((directory / name, segments + (name,)))
                continue
            if not is_file or not name.lower().endswith(".md"):
                continue
            if name.casefold() in RESERVED_FILES:
                skipped["reserved"] += 1
                continue
            concept_id = "/".join(segments + (name[:-3],))
            try:
                DocumentIdentity(bundle_id, concept_id)
            except IdentityError:
                skipped["invalid_name"] += 1
                continue
            if len(found) >= limits.max_documents_per_bundle:
                reasons.add(REASON_DOCUMENT_LIMIT)
                return found
            found.append(concept_id)
    return found


def scan_bundle(reader: DocumentReader, bundle_id: str, limits: KnowledgeLimits,
                clock: Callable[[], float] = time.monotonic) -> ScanResult:
    """Skanuje aktualnie dostępny bundle. Niedostępny bundle zgłasza DocumentError."""
    state = reader.current_bundle(bundle_id)
    deadline = clock() + limits.scan_budget_s
    reasons: set = set()
    skipped: Counter = Counter()
    errors: Counter = Counter()
    candidates = _inventory(state.path, bundle_id, limits, clock, deadline, reasons, skipped)
    collided = {concept_id for group in find_case_collisions(candidates) for concept_id in group}
    if collided:
        skipped["case_collision"] += len(collided)
        candidates = [concept_id for concept_id in candidates if concept_id not in collided]
    documents: List[PublicDocument] = []
    pending = sorted(candidates)
    for index, concept_id in enumerate(pending):
        if clock() >= deadline:
            reasons.add(REASON_TIME_BUDGET)
            skipped["unread"] += len(pending) - index
            break
        try:
            documents.append(reader.read(bundle_id, concept_id))
        except DocumentError as exc:
            if exc.code == "BUNDLE_UNAVAILABLE":
                raise
            errors[exc.code] += 1
    return ScanResult(tuple(documents), bool(reasons), tuple(sorted(reasons)), dict(skipped), dict(errors))
