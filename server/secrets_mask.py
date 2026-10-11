#!/usr/bin/env python3
"""Maskowanie sekretów w odczycie plików (P14).

Heurystyka ograniczająca przypadkowe wycieki (np. `search_code` po PASSWORD na referencji), a nie
granica bezpieczeństwa: sekret w pliku o innej nazwie lub w nietypowym formacie nie zostanie wykryty.
Wyszukiwanie ma działać na tekście po zamaskowaniu (brak wyroczni wartości sekretu).
"""
import re
from dataclasses import dataclass
from typing import List, Tuple

MASK = "***"
BLOCK_MASK = "[ZAMASKOWANO]"

SECRET_EXACT = frozenset({".env", "pip.ini", "pip.conf", ".npmrc"})
EXAMPLE_ENV = frozenset({".env.example", ".env.sample", ".env.template"})
SECRET_SUFFIXES = (".pem", ".key")
BLOCKED_SUFFIXES = (".pfx", ".p12")  # binarne: maskowanie niemożliwe, odczyt zablokowany

_QUOTES = "'" + '"'
_NAME_RE = re.compile(r"TOKEN|PASSWORD|PASSWD|SECRET|KEY|CREDENTIAL|AUTH", re.IGNORECASE)
_LINE_RE = re.compile(r"^(?P<head>\s*(?:export\s+)?(?P<name>[^=\s#;][^=]*?)\s*=\s*)(?P<value>.*)$")
_URL_CRED_RE = re.compile(r"(?P<pre>[A-Za-z][A-Za-z0-9+.\-]*://[^\s/@:]+:)(?P<pw>[^\s/@]+)(?=@)")
_PEM_RE = re.compile(
    r"(?P<head>-----BEGIN (?P<kind>[A-Z0-9 ]*PRIVATE KEY)-----)(?P<body>.*?)(?P<tail>-----END (?P=kind)-----)",
    re.DOTALL)
_PEM_OPEN_RE = re.compile(r"(?P<head>-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----)(?P<body>(?:(?!-----END).)*)\Z",
                          re.DOTALL)


@dataclass(frozen=True)
class MaskResult:
    text: str
    count: int

    @property
    def masked(self) -> bool:
        return self.count > 0


def _basename(filename: str) -> str:
    return re.split(r"[\\/]", str(filename))[-1].strip().lower()


def is_secret_file(filename: str) -> bool:
    name = _basename(filename)
    if name in EXAMPLE_ENV:
        return False
    if name in SECRET_EXACT or name.startswith(".env."):
        return True
    return name.endswith(SECRET_SUFFIXES)


def is_blocked_secret(filename: str) -> bool:
    return _basename(filename).endswith(BLOCKED_SUFFIXES)


def _mask_line(line: str, counter: List[int]) -> str:
    match = _LINE_RE.match(line)
    if match and _NAME_RE.search(match.group("name")):
        stripped = match.group("value").strip().strip(_QUOTES)
        if stripped and stripped != MASK:
            counter[0] += 1
            return match.group("head") + MASK

    def replace(url: "re.Match[str]") -> str:
        if url.group("pw") == MASK:
            return url.group(0)
        counter[0] += 1
        return url.group("pre") + MASK

    return _URL_CRED_RE.sub(replace, line)


def _mask_pem(text: str) -> Tuple[str, int]:
    count = 0

    def closed(match: "re.Match[str]") -> str:
        nonlocal count
        if match.group("body").strip() == BLOCK_MASK:
            return match.group(0)
        count += 1
        return f"{match.group('head')}\n{BLOCK_MASK}\n{match.group('tail')}"

    def opened(match: "re.Match[str]") -> str:
        nonlocal count
        if match.group("body").strip() == BLOCK_MASK:
            return match.group(0)
        count += 1
        return f"{match.group('head')}\n{BLOCK_MASK}\n"

    text = _PEM_RE.sub(closed, text)
    text = _PEM_OPEN_RE.sub(opened, text)
    return text, count


def mask_text(filename: str, text: str) -> MaskResult:
    """Zwraca tekst z zamaskowanymi sekretami (tylko dla plików z listy sekretów)."""
    if not is_secret_file(filename):
        return MaskResult(text, 0)
    name = _basename(filename)
    if name.endswith(SECRET_SUFFIXES):
        masked, count = _mask_pem(text)
        if name.endswith(".key") and count == 0 and masked.strip() and masked.strip() != BLOCK_MASK:
            return MaskResult(BLOCK_MASK, 1)  # surowy klucz bez nagłówka PEM
        return MaskResult(masked, count)
    counter = [0]
    lines = text.splitlines(keepends=True)
    out = []
    for line in lines:
        body = line.rstrip("\r\n")
        eol = line[len(body):]
        out.append(_mask_line(body, counter) + eol)
    return MaskResult("".join(out), counter[0])
