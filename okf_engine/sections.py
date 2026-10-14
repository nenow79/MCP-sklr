"""Sekcje wyłącznie publicznego, zamaskowanego body OKF (C2a).

Profil v1, nie pełny CommonMark: ATX i jednoliniowy Setext poza blokami
kodu. Nie interpretujemy kontenerów list/blockquote ani inline Markdown.
Identyfikatory są lokalne dla dokumentu; kontrola revision należy do serwisu.
Przy limicie zakres kończy się przed pierwszym pominiętym nagłówkiem.
"""
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .limits import MAX_SECTIONS_PER_DOCUMENT
from .textlines import join_lines, split_lines

WARN_SECTION_LIMIT = "sections_limit"
_ATX = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*)|$)")
_SETEXT = re.compile(r" {0,3}(=+|-+)[ \t]*$")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)$")
_CLOSING_HASHES = re.compile(r"[ \t]+#+[ \t]*$")


@dataclass(frozen=True)
class Section:
    id: str
    heading: Optional[str]
    level: int
    parent_id: Optional[str]
    start_line: int
    end_line: int
    subtree_end_line: int


@dataclass(frozen=True)
class SectionContent:
    content: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class SectionOutline:
    entries: Tuple[Section, ...]
    partial: bool
    warnings: Tuple[str, ...]
    _body: str
    _body_start_line: int

    def get_section(self, section_id: str, section_mode: str = "subtree") -> SectionContent:
        """Zwraca publiczny fragment z oryginalnymi EOL; brak kontroli revision w C2a."""
        if section_mode not in ("direct", "subtree"):
            raise ValueError("section_mode")
        entry = next((item for item in self.entries if item.id == section_id), None)
        if entry is None:
            raise ValueError("section_id")
        end = entry.end_line if section_mode == "direct" else entry.subtree_end_line
        lines = split_lines(self._body)
        content = join_lines(lines[entry.start_line - self._body_start_line:
                                   end - self._body_start_line + 1])
        return SectionContent(content, entry.start_line, end)


def build_outline(body: str, body_start_line: int) -> SectionOutline:
    """Wejście musi pochodzić z PublicDocumentView, nigdy z prywatnego parsera.

    s0 istnieje tylko dla niepustego zakresu przed pierwszym nagłówkiem
    (także samych pustych linii); liczy się do stałego limitu 2000 sekcji.
    Puste body daje pusty outline. Setext rozpoznajemy tylko po pojedynczej
    linii tekstu; wieloliniowe akapity pozostają treścią w profilu v1.
    """
    if not isinstance(body, str):
        raise ValueError("body")
    if isinstance(body_start_line, bool) or not isinstance(body_start_line, int) or body_start_line < 1:
        raise ValueError("body_start_line")
    lines = split_lines(body)
    # (indeks linii body, heading, level, id)
    headings = []
    fence_char = None
    fence_size = 0
    candidate = None
    paragraph_lines = 0
    partial = False
    stop = len(lines)
    for index, (text, _) in enumerate(lines):
        if fence_char is not None:
            closing = re.fullmatch(r" {0,3}" + re.escape(fence_char) +
                                   r"{" + str(fence_size) + r",}[ \t]*", text)
            if closing:
                fence_char = None
            candidate = None
            paragraph_lines = 0
            continue
        if text.startswith("\t") or text.startswith("    "):
            candidate = None
            paragraph_lines = 0
            continue
        fence = _FENCE.fullmatch(text)
        if fence and not (fence.group(1)[0] == "`" and "`" in fence.group(2)):
            fence_char = fence.group(1)[0]
            fence_size = len(fence.group(1))
            candidate = None
            paragraph_lines = 0
            continue
        atx = _ATX.fullmatch(text)
        underline = _SETEXT.fullmatch(text)
        found = None
        if atx:
            title = (atx.group(2) or "").strip(" \t")
            # Closing hashes require whitespace, including an empty heading.
            if title and set(title) == {"#"} and atx.group(2) is not None:
                title = ""
            else:
                title = _CLOSING_HASHES.sub("", title).rstrip(" \t")
            found = (index, title, len(atx.group(1)))
        elif underline and candidate is not None and paragraph_lines == 1:
            found = (candidate, lines[candidate][0].strip(" \t"),
                     1 if underline.group(1)[0] == "=" else 2)
        if found is not None:
            preamble = 1 if (headings[0][0] if headings else found[0]) > 0 else 0
            if len(headings) + preamble >= MAX_SECTIONS_PER_DOCUMENT:
                partial = True
                stop = found[0]
                break
            headings.append((*found, "s" + str(len(headings) + 1)))
            candidate = None
            paragraph_lines = 0
        elif not text.strip() or underline:
            candidate = None
            paragraph_lines = 0
        elif text.lstrip().startswith((">", "- ", "+ ", "* ")):
            candidate = None
            paragraph_lines = 0
        else:
            candidate = index
            paragraph_lines += 1
    records = []
    first = headings[0][0] if headings else stop
    if first > 0:
        records.append((0, None, 0, "s0"))
    records.extend(headings)
    end_of_body = body_start_line + stop - 1
    parents = []
    subtree_ends = [end_of_body] * len(records)
    parent_ids = []
    for index, (start, _, level, _) in enumerate(records):
        while parents and records[parents[-1]][2] >= level:
            subtree_ends[parents.pop()] = body_start_line + start - 1
        # s0 is a preamble, not a parent of top-level headings.
        if parents and records[parents[-1]][2] == 0:
            subtree_ends[parents.pop()] = body_start_line + start - 1
        parent_ids.append(records[parents[-1]][3] if parents else None)
        parents.append(index)
    entries = tuple(Section(
        id=record[3], heading=record[1], level=record[2], parent_id=parent_ids[index],
        start_line=body_start_line + record[0],
        end_line=(body_start_line + records[index + 1][0] - 1
                  if index + 1 < len(records) else end_of_body),
        subtree_end_line=subtree_ends[index]) for index, record in enumerate(records))
    return SectionOutline(entries, partial, (WARN_SECTION_LIMIT,) if partial else (),
                          body, body_start_line)
