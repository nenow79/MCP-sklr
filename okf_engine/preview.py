"""Ograniczony publiczny podgląd C3a. Nigdy diff surowych bajtów/YAML.

Podgląd metadanych jest syntetyczny: komentarze i formatowanie źródła nie są
przenoszone. Zmiana samych komentarzy/formatowania jest jawnie sygnalizowana.
Maskowanie jest heurystyką public_view, nie pełnym DLP.
"""
import difflib
import json

from .public_view import render_public_yaml
from .textlines import split_lines
from .writer import PreparedWrite, WriteError

MAX_DIFF_LINES = 20000
MAX_DIFF_PRODUCT = 4000000
MAX_DIFF_INPUT_CHARS = 200000


def _diff(before, after, budget):
    if before == after:
        return "", False
    if len(before) + len(after) > MAX_DIFF_INPUT_CHARS:
        return "Podgląd częściowy: przekroczono budżet generowania diffu.\n", True
    # U+2028/U+2029 w treści nie są fizycznymi końcami linii Markdown.
    left = [content + ("\n" if eol else "") for content, eol in split_lines(before)]
    right = [content + ("\n" if eol else "") for content, eol in split_lines(after)]
    if len(left) + len(right) > MAX_DIFF_LINES or len(left) * len(right) > MAX_DIFF_PRODUCT:
        return "Podgląd częściowy: przekroczono budżet generowania diffu.\n", True
    chunks, used = [], 0
    for line in difflib.unified_diff(left, right, fromfile="przed", tofile="po", n=3):
        if not line.endswith("\n"):
            line += "\n[brak końca linii]\n"
        if used + len(line) > budget:
            chunks.append(line[:max(0, budget - used)])
            return "".join(chunks), True
        chunks.append(line)
        used += len(line)
    return "".join(chunks), False


def build_preview(plan: PreparedWrite, max_chars: int = 12000):
    """Zwraca JSON-safe dict, cały w max_chars przy ensure_ascii=False.

    Identyfikatorów/flag nie skracamy; nadmierny diff ma truncated i reasons.
    Funkcja nie udostępnia before/after, surowych metadanych ani hashy/rewizji.
    """
    if (not isinstance(plan, PreparedWrite) or isinstance(max_chars, bool)
            or not isinstance(max_chars, int) or not 1000 <= max_chars <= 80000):
        raise WriteError("INVALID_ARGUMENT")
    before, after = plan.before_view, plan.after_view
    old_yaml = render_public_yaml(before.metadata) if before is not None else ""
    new_yaml = render_public_yaml(after.metadata)
    metadata_diff, meta_cut = _diff(old_yaml, new_yaml, max_chars)
    body_diff, body_cut = _diff(before.body if before is not None else "", after.body, max_chars)
    public_equal = before is not None and before.metadata == after.metadata and before.body == after.body
    result = {
        "bundle_id": plan.identity.bundle_id, "concept_id": plan.identity.concept_id,
        "mode": plan.mode, "changed": plan.changed, "metadata_replaced": plan.metadata_replaced,
        "metadata_synthetic": True,
        "metadata_notice": "Syntetyczny publiczny YAML; komentarze i formatowanie źródła pominięte.",
        "source_format_changed": plan.changed and public_equal,
        "before_eol": plan.before_eol, "after_eol": plan.after_eol,
        "has_bom": plan.has_bom, "eol_adjusted": plan.eol_adjusted,
        "masked": after.masked or (before.masked if before is not None else False),
        "warnings": list(plan.warnings), "metadata_diff": metadata_diff, "body_diff": body_diff,
        "truncated": meta_cut or body_cut, "reasons": ["diff_budget"] if meta_cut or body_cut else [],
    }
    size = lambda: len(json.dumps(result, ensure_ascii=False))
    if size() > max_chars:
        result["truncated"] = True
        result["reasons"].append("response_budget")
        # Zostawiamy metadane, o ile mieszczą się wraz z obowiązkową otoczką.
        for key in ("body_diff", "metadata_diff"):
            original = result[key]
            result[key] = ""
            if size() > max_chars:
                continue
            low, high = 0, len(original)
            while low < high:
                middle = (low + high + 1) // 2
                result[key] = original[:middle]
                if size() <= max_chars:
                    low = middle
                else:
                    high = middle - 1
            result[key] = original[:low]
            break
        if size() > max_chars:
            raise WriteError("RESULT_TOO_LARGE")
    return result
