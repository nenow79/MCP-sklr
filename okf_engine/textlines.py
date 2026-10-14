"""Podział tekstu na linie z zachowaniem końców linii (CRLF/LF/CR).

str.splitlines() dzieli także po innych znakach (np. U+2028), co rozjeżdża numery linii pliku.
"""
import re
from typing import List, Sequence, Tuple

_LINE_RE = re.compile(r"([^\r\n]*)(\r\n|\n|\r|\Z)")
_EOL_NAMES = {"\r\n": "crlf", "\n": "lf", "\r": "cr"}


def split_lines(text: str) -> List[Tuple[str, str]]:
    """Zwraca listę (treść linii, koniec linii); ostatnia linia może mieć pusty koniec."""
    lines: List[Tuple[str, str]] = []
    pos = 0
    size = len(text)
    while pos < size:
        match = _LINE_RE.match(text, pos)
        lines.append((match.group(1), match.group(2)))
        pos = match.end()
    return lines


def join_lines(lines: Sequence[Tuple[str, str]]) -> str:
    return "".join(content + eol for content, eol in lines)


def detect_eol(lines: Sequence[Tuple[str, str]]) -> str:
    """none / lf / crlf / cr / mixed (ostatnia linia bez końca nie jest brana pod uwagę)."""
    kinds = {eol for _, eol in lines if eol}
    if not kinds:
        return "none"
    if len(kinds) > 1:
        return "mixed"
    return _EOL_NAMES[next(iter(kinds))]
