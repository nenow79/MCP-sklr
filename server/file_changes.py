#!/usr/bin/env python3
"""Przygotowanie zapisów 5b, bez modyfikacji systemu plików.

PreparedChanges/PreparedFile zawierają prywatne bajty: nie serializować ich przez
asdict(), do SQLite, logów ani telemetrii. Wyłącznie preview() jest publicznym
podglądem. Wykonawca musi ponownie sprawdzić wszystkie PathBinding i hashe.
Maskowanie jest heurystyką 5a, nie granicą bezpieczeństwa.
"""
import codecs
import difflib
import fnmatch
import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from roots import Root
from roots_registry import RootsRegistry
from server.file_tools import _eol
from server.path_guard import ResolvedPath, resolve_path
from server.secrets_mask import is_blocked_secret, is_secret_file, mask_text
from okf_engine.write_policy import WritePolicyError, validate_document

MAX_CHANGES = 50
MAX_FILES = 10
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
DIFF_LIMIT_BYTES = 200 * 1024
HINT_LINES = 20
HINT_CHARS = 2000
# difflib ma koszt kwadratowy: duży/trudny diff ma jawny podgląd częściowy.
DIFF_MAX_LINES = 20000
DIFF_MAX_PRODUCT = 4_000_000
HINT_CANDIDATES = 256
DEFAULT_PATTERNS = (
    "panel/", "command_policy.py", "config.json", "tasks.db*", ".git/", "main.py", "state.py",
)
SERVER_PATTERNS = ("server/", "tasks/", "roots.py", "roots_registry.py", "config_store.py")
_EOL_RE = re.compile(r"\r\n|\r|\n")


class ChangeError(ValueError):
    """Bezpieczny komunikat: nigdy nie zawiera surowej treści żądania/pliku."""


class SnapshotError(ChangeError):
    """Stały kod błędu dla adaptera dokładnych zapisów UTF-8."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_document_changes(registry, files):
    """Wspólna kontrola przygotowania i wykonania, bez zapisu i bez preview treści."""
    knowledge = registry.config_snapshot().get("knowledge", {})
    roots = registry.list()
    try:
        for item in files:
            paths = {item.path}
            for binding in item.bindings:
                paths.add(Path(os.path.normpath(str(Path(binding.root.path) / binding.filepath))))
            for path in paths:
                validate_document(knowledge, roots, path,
                                  item.before.raw if item.before else None, item.after)
    except WritePolicyError as exc:
        raise ChangeError(str(exc)) from None


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _matches(relative: str, patterns: Tuple[str, ...]) -> bool:
    parts = relative.casefold().split("/")
    for pattern in patterns:
        folded = pattern.casefold()
        directory = folded.endswith("/")
        wanted = folded.rstrip("/").split("/")
        candidate = parts[:len(wanted)] if directory else parts
        if len(candidate) == len(wanted) and all(
                fnmatch.fnmatchcase(part, rule) for part, rule in zip(candidate, wanted)):
            return True
    return False


@dataclass(frozen=True)
class SensitivePolicy:
    """Wzorce względne, zakotwiczone; '*' nie przechodzi przez segment ścieżki.

    main.py wstrzyknie konfigurację później. Repo serwera rozpoznajemy tylko
    przez rzeczywiste położenie tego modułu, nie przez nazwę/markery repo.
    """
    patterns: Tuple[str, ...] = DEFAULT_PATTERNS
    server_patterns: Tuple[str, ...] = SERVER_PATTERNS
    server_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1])

    def __post_init__(self) -> None:
        for attr in ("patterns", "server_patterns"):
            value = getattr(self, attr)
            if not isinstance(value, (list, tuple)) or any(
                    not isinstance(p, str) or not p or p.startswith("/")
                    or chr(92) in p or ":" in p or "//" in p
                    or any(part in (".", "..") for part in p.rstrip("/").split("/"))
                    or any(ord(ch) < 32 for ch in p) for p in value):
                raise ChangeError("Nieprawidłowe względne wzorce sensitive.")
            object.__setattr__(self, attr, tuple(value))
        object.__setattr__(self, "server_dir", Path(self.server_dir).resolve())

    def reasons(self, resolved: ResolvedPath) -> Tuple[str, ...]:
        reasons = []
        if is_secret_file(resolved.path.name) or is_secret_file(resolved.relative):
            reasons.append("plik sekretów")
        if _matches(resolved.relative, self.patterns):
            reasons.append("P10 względem roota")
        try:
            relative = resolved.path.relative_to(self.server_dir).as_posix()
        except ValueError:
            relative = None
        if relative is not None and _matches(relative, self.patterns + self.server_patterns):
            reasons.append("repo działającego serwera")
        return tuple(reasons)


@dataclass(frozen=True)
class PathBinding:
    """Przypięty alias i ścieżka wejściowa do ponownego guarda, także dla linków."""
    filepath: str
    root: Root
    owner: Root
    path: Path
    relative: str


@dataclass(frozen=True, repr=False)
class FileSnapshot:
    raw: bytes
    text: str
    encoding: str
    bom: bytes
    eol: str
    sha256: str
    identity: Tuple[int, int]
    mode: int


@dataclass(frozen=True, repr=False)
class PreparedFile:
    bindings: Tuple[PathBinding, ...]
    before: Optional[FileSnapshot]
    after: bytes
    after_text: str
    encoding: str
    bom: bytes
    after_hash: str
    eol_adjusted: bool
    sensitive_reasons: Tuple[str, ...]

    @property
    def path(self) -> Path:
        return self.bindings[0].path

    @property
    def sensitive(self) -> bool:
        return bool(self.sensitive_reasons)

    @property
    def changed(self) -> bool:
        return self.before is None or self.before.raw != self.after

    @property
    def mask_names(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys([self.path.name] + [b.relative for b in self.bindings]))


@dataclass(frozen=True, repr=False)
class PreparedChanges:
    files: Tuple[PreparedFile, ...]

    @property
    def sensitive(self) -> bool:
        return any(item.sensitive for item in self.files)

    def preview(self) -> Dict[str, Any]:
        """Tylko maskowany podgląd; wspólny budżet UTF-8, bez prywatnych bajtów."""
        remaining = DIFF_LIMIT_BYTES
        entries = []
        for item in self.files:
            before, count_before = _masked(item.mask_names, item.before.text if item.before else "")
            after, count_after = _masked(item.mask_names, item.after_text)
            hidden = item.changed and before == after and bool(count_before or count_after)
            diff, limited = _diff(before, after)
            if hidden:
                diff = "Zmieniono ukrytą treść (wartości zamaskowane).\n"
            clipped = len(diff.encode("utf-8")) > remaining
            diff = diff.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
            remaining -= len(diff.encode("utf-8"))
            binding = item.bindings[0]
            entry = {
                "root": binding.root.alias, "filepath": binding.relative,
                "locations": [{"root": b.root.alias, "filepath": b.relative} for b in item.bindings],
                "diff": diff, "truncated": limited or clipped,
                "masked": bool(count_before or count_after), "hidden_change": hidden,
                "sensitive": item.sensitive, "sensitive_reasons": list(item.sensitive_reasons),
                "created": item.before is None, "changed": item.changed,
                "encoding": item.encoding, "bom": bool(item.bom),
                "eol_before": item.before.eol if item.before else "none",
                "eol_after": _eol(item.after_text), "eol_adjusted": item.eol_adjusted,
            }
            # Nawet hash pozornie niesekretnej wartości w .env może być wyrocznią.
            if not any(is_secret_file(name) for name in item.mask_names):
                entry["before_hash"] = item.before.sha256 if item.before else None
                entry["after_hash"] = item.after_hash
            entries.append(entry)
        return {"files": entries, "sensitive": self.sensitive,
                "truncated": any(e["truncated"] for e in entries),
                "diff_bytes": DIFF_LIMIT_BYTES - remaining}


def _masked(names: Tuple[str, ...], text: str) -> Tuple[str, int]:
    count = 0
    for name in names:
        result = mask_text(name, text)
        text, count = result.text, count + result.count
    return text, count


def _diff(before: str, after: str) -> Tuple[str, bool]:
    if before == after:
        return "", False
    left, right = before.splitlines(keepends=True), after.splitlines(keepends=True)
    if (len(left) + len(right) > DIFF_MAX_LINES
            or len(left) * len(right) > DIFF_MAX_PRODUCT
            or len(before.encode("utf-8")) + len(after.encode("utf-8")) > 2 * DIFF_LIMIT_BYTES):
        return "Diff skrócony: przekroczono budżet generowania podglądu.\n", True
    # Linie bez newline oznaczamy jawnie; niczego nie doklejamy do zapisywanej treści.
    output = []
    for line in difflib.unified_diff(left, right, fromfile="przed", tofile="po", n=3):
        output.append(line if line.endswith(("\n", "\r")) else line + "\n[brak końca linii]\n")
    return "".join(output), False


def read_snapshot(path: Path, *, max_bytes: Optional[int] = None,
                  utf8_only: bool = False) -> FileSnapshot:
    """Odczyt bajtowy z zachowaniem formatu. Żadnych zapisów ani surowych błędów I/O."""
    max_bytes = MAX_FILE_BYTES if max_bytes is None else max_bytes
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_FILE_BYTES:
        raise ChangeError("Nieprawidłowy limit snapshotu.")
    if is_blocked_secret(path.name):
        raise ChangeError("Pliki .pfx i .p12 są zablokowane.")
    try:
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ChangeError("Ścieżka nie wskazuje zwykłego pliku.")
            if info.st_size > max_bytes:
                if utf8_only:
                    raise SnapshotError("SAFETY_LIMIT")
                raise ChangeError("Plik przekracza limit 5 MiB.")
            raw = stream.read(max_bytes + 1)
            end = os.fstat(stream.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                end.st_size, end.st_mtime_ns, end.st_ctime_ns):
            raise ChangeError("Plik zmienił się podczas odczytu; ponów przygotowanie.")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, ChangeError):
            raise
        raise ChangeError("Nie można odczytać zwykłego pliku.") from None
    if len(raw) > max_bytes:
        if utf8_only:
            raise SnapshotError("SAFETY_LIMIT")
        raise ChangeError("Plik przekracza limit 5 MiB.")
    if utf8_only and len(raw) != end.st_size:
        raise ChangeError("Plik zmienił się podczas odczytu.")
    if any(byte < 32 and byte not in (9, 10, 12, 13) for byte in raw):
        raise ChangeError("Plik binarny lub nieobsługiwane kodowanie.")
    bom = codecs.BOM_UTF8 if raw.startswith(codecs.BOM_UTF8) else b""
    body = raw[len(bom):]
    encoding = "utf-8"
    try:
        text = body.decode(encoding)
    except UnicodeDecodeError:
        if utf8_only:
            raise SnapshotError("INVALID_UTF8") from None
        if bom:
            raise ChangeError("Nieprawidłowy UTF-8 z BOM.") from None
        encoding = "cp1250"
        try:
            text = body.decode(encoding)
        except UnicodeDecodeError:
            raise ChangeError("Nieobsługiwane kodowanie (UTF-8/cp1250).") from None
    return FileSnapshot(raw, text, encoding, bom, _eol(text), hashlib.sha256(raw).hexdigest(),
                        (info.st_dev, info.st_ino), stat.S_IMODE(info.st_mode))


def prepare_exact_write(registry: RootsRegistry, filepath: str, content: bytes, *,
                        before: Optional[FileSnapshot], root: Any = None,
                        max_bytes: Optional[int] = None) -> PreparedChanges:
    """Przygotowuje dokładne bajty UTF-8, bez normalizacji tekstu i bez preview.

    Snapshot musi być tym samym, na którym adapter zweryfikował rewizję.
    Powtórny odczyt wykrywa zmianę między walidacją treści a budową transakcji.
    Kontrola braku linków oraz reguł domeny należy również do adaptera.
    """
    max_bytes = MAX_FILE_BYTES if max_bytes is None else max_bytes
    if (not isinstance(content, bytes) or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_FILE_BYTES
            or len(content) > max_bytes):
        raise ChangeError("Nieprawidłowe bajty lub limit zapisu.")
    bom = codecs.BOM_UTF8 if content.startswith(codecs.BOM_UTF8) else b""
    try:
        text = content[len(bom):].decode("utf-8")
    except UnicodeDecodeError:
        raise ChangeError("Nieprawidłowy UTF-8.") from None
    resolved, binding = _binding(registry, filepath, root)
    if not resolved.path.parent.is_dir():
        raise ChangeError("Katalog rodzica nie istnieje.")
    if before is None:
        if os.path.lexists(resolved.path):
            raise ChangeError("Cel tworzenia już istnieje.")
    else:
        if not isinstance(before, FileSnapshot) or before.encoding != "utf-8":
            raise ChangeError("Nieprawidłowy snapshot UTF-8.")
        current = read_snapshot(resolved.path, max_bytes=max_bytes, utf8_only=True)
        if (current.raw, current.identity, current.mode) != (before.raw, before.identity, before.mode):
            raise SnapshotError("SNAPSHOT_CONFLICT")
    item = PreparedFile((binding,), before, content, text, "utf-8", bom,
                        hashlib.sha256(content).hexdigest(), False,
                        SensitivePolicy().reasons(resolved))
    validate_document_changes(registry, (item,))
    return PreparedChanges((item,))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ChangeError(f"{label} musi być tekstem.")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ChangeError(f"{label} zawiera nieprawidłowy Unicode.") from None
    if size > MAX_TOTAL_BYTES:
        raise ChangeError("Treść żądania przekracza limit 20 MiB.")
    if any(ord(ch) < 32 and ch not in "\t\n\r\f" for ch in value):
        raise ChangeError(f"{label} zawiera nieobsługiwane znaki sterujące.")
    return value


def _insert(text: str, eol: str) -> Tuple[str, bool]:
    ending = {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}.get(eol)
    normalized = _EOL_RE.sub(lambda _: ending, text) if ending else text
    return normalized, normalized != text


def _encode(text: str, encoding: str, bom: bytes) -> bytes:
    try:
        raw = bom + text.encode(encoding)
    except UnicodeEncodeError:
        raise ChangeError("Nowej treści nie można zapisać w dotychczasowym kodowaniu.") from None
    if len(raw) > MAX_FILE_BYTES:
        raise ChangeError("Wynik przekracza limit 5 MiB na plik.")
    return raw


def _binding(registry: RootsRegistry, filepath: Any, root: Any) -> Tuple[ResolvedPath, PathBinding]:
    resolved = resolve_path(registry, filepath, root, write=True)
    if is_blocked_secret(filepath) or is_blocked_secret(resolved.path.name):
        raise ChangeError("Pliki .pfx i .p12 są zablokowane.")
    # Zachowujemy ścieżkę nierozwiązaną: ponowny guard musi wykryć zmianę linka/junctiona.
    return resolved, PathBinding(filepath, resolved.root, resolved.owner, resolved.path, resolved.relative)


def _hint(names: Tuple[str, ...], text: str, old: str) -> str:
    masked, _ = _masked(names, text)
    lines = masked.splitlines(keepends=True)
    if not lines:
        return "[pusty plik]"
    # Ranking nigdy nie porównuje sekretów: dla plików sekretów pokazujemy początek.
    secret = any(is_secret_file(name) for name in names)
    width = HINT_LINES if secret else min(HINT_LINES, max(1, len(old.splitlines())))
    start = 0
    sampled = len(lines) > HINT_CANDIDATES
    if not secret:
        query = " ".join(old.split())[:256]
        indices = range(len(lines)) if not sampled else sorted({
            i * (len(lines) - 1) // (HINT_CANDIDATES - 1) for i in range(HINT_CANDIDATES)})
        best = -1.0
        for index in indices:
            candidate = " ".join("".join(lines[index:index + width]).split())[:256]
            score = difflib.SequenceMatcher(None, query, candidate, autojunk=False).ratio()
            if score > best:
                best, start = score, index
    fragment = "".join(lines[start:start + width])
    visible = []
    for ch in fragment:
        code = ord(ch)
        if ch == "\r":
            visible.append("<CR>")
        elif ch == "\n":
            visible.append("<LF>\n")
        elif ch == "\t":
            visible.append("<TAB>")
        elif (code < 32 or 0x7F <= code <= 0x9F or 0x200B <= code <= 0x200F
              or 0x202A <= code <= 0x202E or 0x2066 <= code <= 0x2069):
            visible.append(f"<U+{code:04X}>")
        else:
            visible.append(ch)
        if sum(map(len, visible)) >= HINT_CHARS:
            break
    note = "Fragment zamaskowany (bez rankingu sekretów)" if secret else "Najbliższy fragment"
    if sampled and not secret:
        note += " (ograniczony ranking)"
    return f"{note}, od linii {start + 1}:\n" + "".join(visible)[:HINT_CHARS]


def _replace(text: str, old: str, new: str, names: Tuple[str, ...], index: int) -> str:
    position, first, count = 0, -1, 0
    while True:
        found = text.find(old, position)
        if found < 0:
            break
        if first < 0:
            first = found
        count += 1
        position = found + 1  # także nakładające się wystąpienia
    if count != 1:
        message = f"Zmiana {index}: old_text ma {count} dopasowań; wymagane dokładnie 1."
        if count == 0:
            message += "\n" + _hint(names, text, old)
        raise ChangeError(message)
    return text[:first] + new + text[first + len(old):]


def _file(bindings: Tuple[PathBinding, ...], before: Optional[FileSnapshot], text: str,
          adjusted: bool, reasons: Tuple[str, ...]) -> PreparedFile:
    encoding, bom = (before.encoding, before.bom) if before else ("utf-8", b"")
    raw = _encode(text, encoding, bom)
    return PreparedFile(bindings, before, raw, text, encoding, bom,
                        hashlib.sha256(raw).hexdigest(), adjusted, tuple(dict.fromkeys(reasons)))


def _totals(files: Tuple[PreparedFile, ...]) -> None:
    if sum(len(f.before.raw) for f in files if f.before) > MAX_TOTAL_BYTES:
        raise ChangeError("Stan początkowy przekracza łącznie 20 MiB.")
    if sum(len(f.after) for f in files) > MAX_TOTAL_BYTES:
        raise ChangeError("Wynik przekracza łącznie 20 MiB.")


def prepare_edit(registry: RootsRegistry, changes: Any, root: Any = None, *,
                 policy: Optional[SensitivePolicy] = None) -> PreparedChanges:
    """Walidacja całego żądania i sekwencyjne zmiany w pamięci; brak częściowego wyniku."""
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CHANGES:
        raise ChangeError("changes musi być listą od 1 do 50 wpisów.")
    policy = policy if policy is not None else SensitivePolicy()
    groups: Dict[str, PreparedFile] = {}
    request_bytes = 0
    for index, change in enumerate(changes, 1):
        if (not isinstance(change, dict) or not {"filepath", "old_text", "new_text"} <= set(change)
                or set(change) - {"filepath", "old_text", "new_text", "root"}):
            raise ChangeError(f"Zmiana {index}: wymagane filepath/old_text/new_text, opcjonalnie root.")
        old, new = _text(change["old_text"], "old_text"), _text(change["new_text"], "new_text")
        if not old:
            raise ChangeError("old_text nie może być pusty.")
        request_bytes += len(old.encode("utf-8")) + len(new.encode("utf-8"))
        if request_bytes > MAX_TOTAL_BYTES:
            raise ChangeError("Treść żądania przekracza łącznie 20 MiB.")
        resolved, binding = _binding(registry, change["filepath"], change.get("root", root))
        key = _key(resolved.path)
        previous = groups.get(key)
        if previous is None:
            if len(groups) >= MAX_FILES:
                raise ChangeError("Żądanie przekracza limit 10 różnych plików.")
            before = read_snapshot(resolved.path)
            text, bindings, adjusted, reasons = before.text, (), False, ()
        else:
            before = previous.before
            text, bindings = previous.after_text, previous.bindings
            adjusted, reasons = previous.eol_adjusted, previous.sensitive_reasons
        if binding not in bindings:
            bindings += (binding,)
        normalized, changed_eol = _insert(new, before.eol)
        names = tuple(dict.fromkeys([resolved.path.name] + [b.relative for b in bindings]))
        text = _replace(text, old, normalized, names, index)
        groups[key] = _file(bindings, before, text, adjusted or changed_eol,
                            reasons + policy.reasons(resolved))
        _totals(tuple(groups.values()))
    validate_document_changes(registry, tuple(groups.values()))
    return PreparedChanges(tuple(groups.values()))


def prepare_write(registry: RootsRegistry, filepath: Any, content: Any,
                  overwrite: Any = False, root: Any = None, *,
                  policy: Optional[SensitivePolicy] = None) -> PreparedChanges:
    """Fundament create_file: istniejący cel tylko z overwrite, rodzic musi istnieć."""
    if not isinstance(overwrite, bool):
        raise ChangeError("overwrite musi być bool.")
    text = _text(content, "content")
    policy = policy if policy is not None else SensitivePolicy()
    resolved, binding = _binding(registry, filepath, root)
    try:
        exists = resolved.path.exists()
        if not resolved.path.parent.is_dir():
            raise ChangeError("Katalog nadrzędny musi istnieć; nie tworzymy go automatycznie.")
    except OSError:
        raise ChangeError("Nie można sprawdzić celu zapisu.") from None
    if exists and not overwrite:
        raise ChangeError("Cel już istnieje; wymagane overwrite=True.")
    before = read_snapshot(resolved.path) if exists else None
    text, adjusted = _insert(text, before.eol) if before else (text, False)
    item = _file((binding,), before, text, adjusted, policy.reasons(resolved))
    _totals((item,))
    validate_document_changes(registry, (item,))
    return PreparedChanges((item,))
