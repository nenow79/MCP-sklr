"""Sygnały jakości OKF (status, zaufanie, aktualność) oraz normalizacja dat publicznych metadanych.

Sygnały są deklaratywne: verified nie jest podpisem ani uprawnieniem, a zgoda na zapis go nie tworzy.
"""
import copy
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .models import MASK

STATUSES = ("draft", "stable", "deprecated")
TRUST_UNVERIFIED = "unverified"
TRUST_MACHINE = "machine-confirmed"
TRUST_HUMAN = "human-reviewed"

_DT_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})")
_MISSING = object()


def parse_okf_datetime(value: Any) -> Optional[datetime]:
    """ISO 8601 z jawnym offsetem -> datetime UTC; inaczej None (bez zgadywania)."""
    if not isinstance(value, str) or _DT_RE.fullmatch(value) is None:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def format_okf_datetime(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _valid_actor(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > 200 or MASK in text:
        return False
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def _is_human(actor: str) -> bool:
    text = actor.strip()
    return text.startswith("human:") and bool(text[len("human:"):].strip())


@dataclass(frozen=True)
class DocumentSignals:
    status: Optional[str]
    trust_tier: str
    stale: Optional[bool]
    warnings: Tuple[str, ...]


def _window_has_invalid_date(window: Any) -> bool:
    if not isinstance(window, Mapping):
        return True
    for key in ("from", "to"):
        if key in window and parse_okf_datetime(window[key]) is None:
            return True
    return False


def evaluate_signals(metadata: Mapping[str, Any], now: datetime) -> DocumentSignals:
    """Wylicza sygnały z (publicznych) metadanych. now musi mieć strefę czasową."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    warnings: List[str] = []

    def warn(code: str) -> None:
        if code not in warnings:
            warnings.append(code)

    # status: brak pola = stable (OKF); niepoprawny = None + ostrzeżenie
    status: Optional[str]
    if "status" not in metadata:
        status = "stable"
    else:
        raw_status = metadata["status"]
        normalized = raw_status.strip().casefold() if isinstance(raw_status, str) else None
        if normalized in STATUSES:
            status = normalized
        else:
            status = None
            warn("status_invalid")

    # stale_after: brak = nieznana granica (None)
    stale: Optional[bool] = None
    if "stale_after" in metadata:
        boundary = parse_okf_datetime(metadata["stale_after"])
        if boundary is None:
            warn("stale_after_invalid")
        else:
            stale = now_utc >= boundary

    # verified: pojedyncze mapping = lista jednoelementowa
    events: List[Tuple[str, datetime]] = []
    raw_verified = metadata.get("verified", _MISSING)
    items: List[Any] = []
    if raw_verified is not _MISSING:
        if isinstance(raw_verified, Mapping):
            items = [raw_verified]
        elif isinstance(raw_verified, list) and raw_verified:
            items = list(raw_verified)
        else:
            warn("verified_invalid")
    for item in items:
        moment = parse_okf_datetime(item.get("at")) if isinstance(item, Mapping) else None
        if isinstance(item, Mapping) and _valid_actor(item.get("by")) and moment is not None:
            events.append((item["by"].strip(), moment))
        else:
            warn("verified_event_invalid")
    if any(_is_human(actor) for actor, _ in events):
        trust = TRUST_HUMAN
    elif events:
        trust = TRUST_MACHINE
    else:
        trust = TRUST_UNVERIFIED

    # generated
    generated_at: Optional[datetime] = None
    if "generated" in metadata:
        generated = metadata["generated"]
        if not isinstance(generated, Mapping) or not _valid_actor(generated.get("by")):
            warn("generated_invalid")
        if isinstance(generated, Mapping) and "at" in generated:
            generated_at = parse_okf_datetime(generated["at"])
            if generated_at is None:
                warn("generated_at_invalid")
    if generated_at is not None and events and generated_at > max(moment for _, moment in events):
        warn("verification_predates_content")

    # kształt pozostałych pól (bez odrzucania konceptu)
    if "tags" in metadata:
        tags = metadata["tags"]
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            warn("tags_invalid")
    if "title" in metadata and not (isinstance(metadata["title"], str) and metadata["title"].strip()):
        warn("title_invalid")
    if "description" in metadata and not isinstance(metadata["description"], str):
        warn("description_invalid")
    if "usage_window" in metadata and _window_has_invalid_date(metadata["usage_window"]):
        warn("sources_date_invalid")
    if "sources" in metadata:
        sources = metadata["sources"]
        if not isinstance(sources, list):
            warn("sources_invalid")
        else:
            for entry in sources:
                resource = entry.get("resource") if isinstance(entry, Mapping) else None
                if not (isinstance(resource, str) and resource.strip()):
                    warn("sources_entry_invalid")
                if not isinstance(entry, Mapping):
                    continue
                if "last_modified" in entry and parse_okf_datetime(entry["last_modified"]) is None:
                    warn("sources_date_invalid")
                if "usage_window" in entry and _window_has_invalid_date(entry["usage_window"]):
                    warn("sources_date_invalid")

    # Attested Computation: tylko odczyt; nic nie jest wykonywane ani atestowane
    doc_type = metadata.get("type")
    if isinstance(doc_type, str) and doc_type.strip().casefold() == "attested computation":
        warn("computation_not_executed")
        runtime = metadata.get("runtime")
        if not (isinstance(runtime, str) and runtime.strip()):
            warn("computation_runtime_missing")

    return DocumentSignals(status, trust, stale, tuple(warnings))


def _normalize_date_in(mapping: Dict[str, Any], key: str) -> None:
    if key in mapping:
        moment = parse_okf_datetime(mapping[key])
        if moment is not None:
            mapping[key] = format_okf_datetime(moment)


def _normalize_window(window: Any) -> None:
    if isinstance(window, dict):
        _normalize_date_in(window, "from")
        _normalize_date_in(window, "to")


def normalize_public_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    """Kopia metadanych: poprawne daty OKF -> ISO 8601 (UTC, Z), verified mapping -> lista.

    Pola nieznane i niepoprawne daty pozostają bez zmian.
    """
    result: Dict[str, Any] = copy.deepcopy(dict(metadata))
    _normalize_date_in(result, "stale_after")
    generated = result.get("generated")
    if isinstance(generated, dict):
        _normalize_date_in(generated, "at")
    verified = result.get("verified")
    if isinstance(verified, dict):
        verified = [verified]
        result["verified"] = verified
    if isinstance(verified, list):
        for item in verified:
            if isinstance(item, dict):
                _normalize_date_in(item, "at")
    _normalize_window(result.get("usage_window"))
    sources = result.get("sources")
    if isinstance(sources, list):
        for entry in sources:
            if isinstance(entry, dict):
                _normalize_date_in(entry, "last_modified")
                _normalize_window(entry.get("usage_window"))
    return result
