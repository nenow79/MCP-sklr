#!/usr/bin/env python3
"""Foldery (roots): model, walidacja ścieżek, strefy zakazane i migracja ze starej konfiguracji.

Logika bez zależności od FastMCP, panelu i configu na dysku (poza rozwiązywaniem ścieżek
w systemie plików). Każdy root to folder z trybem: "rw" (odczyt i zapis) albo "ro" (tylko odczyt).
"""
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

MODE_RW = "rw"
MODE_RO = "ro"
MODES = (MODE_RW, MODE_RO)

MAX_ROOTS = 20
ALIAS_MAX = 32
MAX_DESCRIPTION = 200
ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Zmienne środowiskowe wskazujące katalogi, w których (i pod którymi) nie wolno ustawiać rootów.
_TREE_ENV = (
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "SYSTEMROOT", "WINDIR",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
)
# Katalogi z sekretami w profilu użytkownika.
_SECRET_DIRS = (".ssh", ".aws", ".azure", ".kube", ".gnupg")


class RootError(ValueError):
    """Błąd walidacji folderu (komunikat po polsku, gotowy do pokazania użytkownikowi)."""


class RootNotFound(RootError):
    """Nie ma folderu o podanym aliasie (panel: HTTP 404)."""


class RootConflict(RootError):
    """Duplikat aliasu lub ścieżki (panel: HTTP 409)."""


# --- alias ---
def sanitize_alias(raw: Any) -> str:
    """Zamienia dowolny napis na poprawny alias (małe litery, cyfry, '_' i '-'); pusty = 'root'."""
    text = str(raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    text = text.strip("-_")[:ALIAS_MAX].rstrip("-_")
    return text or "root"


def unique_alias(base: Any, taken: Sequence[str]) -> str:
    """Poprawny alias niewystępujący w `taken` (bez rozróżniania wielkości liter): base, base-2, ..."""
    taken_set = {str(t).lower() for t in taken}
    candidate = sanitize_alias(base)
    if candidate not in taken_set:
        return candidate
    for number in range(2, 1000):
        suffix = f"-{number}"
        trimmed = candidate[: ALIAS_MAX - len(suffix)].rstrip("-_") or "root"
        option = trimmed + suffix
        if option not in taken_set:
            return option
    raise RootError("Nie można utworzyć unikalnego aliasu.")


def validate_alias(alias: Any) -> str:
    if not isinstance(alias, str) or not ALIAS_RE.match(alias):
        raise RootError(
            "Alias musi mieć 1-32 znaki: małe litery, cyfry, '_' lub '-', pierwszy znak litera lub cyfra.")
    return alias


def clean_description(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())[:MAX_DESCRIPTION]


# --- model ---
@dataclass(frozen=True)
class Root:
    alias: str
    path: str
    mode: str
    description: str = ""
    default: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def path_obj(self) -> Path:
        return Path(self.path)

    @classmethod
    def from_dict(cls, data: Any) -> "Root":
        """Buduje Root z wpisu configu (bez sprawdzania systemu plików)."""
        if not isinstance(data, Mapping):
            raise RootError("Wpis folderu musi być obiektem.")
        alias = validate_alias(data.get("alias"))
        mode = data.get("mode")
        if mode not in MODES:
            raise RootError(f"Tryb folderu musi być jednym z: {list(MODES)}.")
        path = data.get("path")
        if not isinstance(path, str) or not path.strip():
            raise RootError("Brak ścieżki folderu.")
        default = data.get("default", False)
        if not isinstance(default, bool):
            raise RootError("Pole 'default' musi być true lub false.")
        return cls(alias, path, mode, clean_description(data.get("description")), default)


# --- strefy zakazane ---
@dataclass(frozen=True)
class Zones:
    """guard: root nie może być tym folderem ani jego przodkiem (wnętrze dozwolone, np. projekty w profilu).
    tree: root nie może być tym folderem, jego przodkiem ani leżeć w jego wnętrzu."""
    guard: Tuple[Path, ...] = field(default_factory=tuple)
    tree: Tuple[Path, ...] = field(default_factory=tuple)


def _safe_resolve(raw: Any) -> Optional[Path]:
    try:
        return Path(str(raw)).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return None


def _within(child: Path, parent: Path) -> bool:
    """True, gdy child == parent albo leży w parent (porównanie zgodne z systemem plików)."""
    return child == parent or parent in child.parents


def forbidden_zones(env: Optional[Mapping[str, str]] = None, home: Optional[Path] = None) -> Zones:
    """Wylicza strefy zakazane dla bieżącego użytkownika (env i home można podać w testach)."""
    environ = os.environ if env is None else env
    if home is None:
        try:
            home = Path.home()
        except (RuntimeError, KeyError, OSError):
            home = None
    home_path = _safe_resolve(home) if home is not None else None

    guard: List[Path] = []
    tree: List[Path] = []
    if home_path is not None:
        guard.append(home_path)
        if home_path.parent != home_path:
            guard.append(home_path.parent)  # np. C:\Users
        for name in _SECRET_DIRS:
            resolved = _safe_resolve(home_path / name)
            if resolved is not None:
                tree.append(resolved)
    for key in _TREE_ENV:
        value = environ.get(key)
        if value:
            resolved = _safe_resolve(value)
            if resolved is not None:
                tree.append(resolved)
    return Zones(tuple(dict.fromkeys(guard)), tuple(dict.fromkeys(tree)))


def check_zones(path: Path, zones: Zones) -> None:
    if path == Path(path.anchor):
        raise RootError("Katalog główny dysku nie może być folderem roboczym.")
    for zone in zones.guard:
        if _within(zone, path):
            raise RootError(
                f"Ten folder zawiera katalog chroniony ({zone}); wskaż konkretny folder projektu.")
    for zone in zones.tree:
        if _within(zone, path) or _within(path, zone):
            raise RootError(f"Folder jest katalogiem chronionym ({zone}) albo leży w nim lub go zawiera.")


# --- walidacja ---
def _is_unc(raw: str) -> bool:
    return raw.startswith("\\\\") or raw.startswith("//")


def validate_root(path: Any, mode: Any, *, zones: Optional[Zones] = None,
                  central_dir: Any = None) -> Path:
    """Sprawdza folder i zwraca ścieżkę po resolve() (symlinki i junctiony rozwiązane).

    Ten sam test uruchamiamy przy dodawaniu i tuż przed wykonaniem zatwierdzonej operacji.
    """
    if mode not in MODES:
        raise RootError(f"Tryb folderu musi być jednym z: {list(MODES)}.")
    if not isinstance(path, (str, os.PathLike)) or not str(path).strip():
        raise RootError("Podaj ścieżkę do folderu.")
    raw = str(path).strip()
    if _is_unc(raw):
        raise RootError("Ścieżki sieciowe (UNC) i z prefiksem \\\\?\\ nie są obsługiwane.")
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        raise RootError("Ścieżka musi być bezwzględna (np. C:\\projekty\\app).")
    try:
        resolved = expanded.resolve(strict=True)
    except FileNotFoundError:
        raise RootError(f"Folder nie istnieje: {raw}")
    except (OSError, ValueError, RuntimeError) as exc:
        raise RootError(f"Nie można odczytać ścieżki: {exc}")
    if _is_unc(str(resolved)):
        raise RootError("Folder wskazuje na zasób sieciowy (UNC); takie ścieżki nie są obsługiwane.")
    if not resolved.is_dir():
        raise RootError(f"To nie jest folder: {raw}")
    check_zones(resolved, zones if zones is not None else forbidden_zones())
    if mode == MODE_RW and central_dir is not None:
        central = _safe_resolve(central_dir)
        if central is not None and (_within(resolved, central) or _within(central, resolved)):
            raise RootError(
                "Folder do zapisu nie może być katalogiem centralnym skills ani leżeć w nim lub go zawierać "
                "(skrypty skills są zaufane po hashu i nie mogą być zmieniane przez model).")
    return resolved


def check_new_root(candidate: Root, existing: Sequence[Root]) -> None:
    """Sprawdza kandydata na tle istniejącej listy (przy edycji podaj listę bez edytowanego wpisu).

    Reguły: unikalny alias i ścieżka, limit MAX_ROOTS, 'ro' wewnątrz 'rw' dozwolone (chroni podfolder),
    'rw' wewnątrz 'ro' niedozwolone (wynik byłby niejednoznaczny).
    """
    if len(existing) >= MAX_ROOTS:
        raise RootError(f"Osiągnięto limit {MAX_ROOTS} folderów.")
    new_path = Path(candidate.path)
    for other in existing:
        if other.alias.lower() == candidate.alias.lower():
            raise RootConflict(f"Alias '{candidate.alias}' jest już zajęty.")
        other_path = Path(other.path)
        if new_path == other_path:
            raise RootConflict(f"Ten folder jest już na liście jako '{other.alias}'.")
        if candidate.mode == MODE_RW and other.mode == MODE_RO and _within(new_path, other_path):
            raise RootError(
                f"Folder do zapisu nie może leżeć w folderze tylko do odczytu ('{other.alias}').")
        if candidate.mode == MODE_RO and other.mode == MODE_RW and _within(other_path, new_path):
            raise RootError(
                f"Folder tylko do odczytu nie może zawierać folderu do zapisu ('{other.alias}').")


def find_root_for(path: Any, roots: Sequence[Root]) -> Optional[Root]:
    """Najgłębszy root zawierający ścieżkę (jego tryb obowiązuje); None, gdy ścieżka jest poza rootami."""
    target = Path(str(path))
    best: Optional[Root] = None
    best_depth = -1
    for root in roots:
        base = Path(root.path)
        if _within(target, base) and len(base.parts) > best_depth:
            best, best_depth = root, len(base.parts)
    return best


# --- migracja ze starej konfiguracji ---
@dataclass
class MigrationResult:
    migrated: bool
    roots: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)  # (źródło, powód)


def needs_migration(config: Mapping[str, Any]) -> bool:
    """roots == None oznacza konfigurację sprzed fazy 5.1; [] to konfiguracja zmigrowana i pusta."""
    return config.get("roots") is None


def migrate_legacy(config: Mapping[str, Any], *, zones: Optional[Zones] = None,
                   central_dir: Any = None) -> MigrationResult:
    """Buduje listę roots z `workspace.path` (rw, domyślny) i `references` (ro).

    Nie zmienia configu: wywołujący wpisuje wynik do config["roots"]. Stare klucze zostają
    nietknięte (wycofanie). Wpisy nieprawidłowe są pomijane z powodem w `skipped`.
    """
    if not needs_migration(config):
        return MigrationResult(False)
    active_zones = zones if zones is not None else forbidden_zones()
    if central_dir is None:
        skills = config.get("skills")
        central_dir = skills.get("central_dir") if isinstance(skills, Mapping) else None

    accepted: List[Root] = []
    skipped: List[Tuple[str, str]] = []

    def attempt(label: str, raw_path: Any, mode: str, alias_hint: Any,
                description: Any, default: bool) -> None:
        try:
            resolved = validate_root(raw_path, mode, zones=active_zones, central_dir=central_dir)
            alias = unique_alias(alias_hint, [r.alias for r in accepted])
            root = Root(alias, str(resolved), mode, clean_description(description), default)
            check_new_root(root, accepted)
        except RootError as exc:
            skipped.append((label, str(exc)))
            return
        accepted.append(root)

    workspace = config.get("workspace")
    raw_workspace = workspace.get("path") if isinstance(workspace, Mapping) else None
    if raw_workspace:
        attempt("workspace", raw_workspace, MODE_RW, Path(str(raw_workspace)).name, "", True)

    references = config.get("references")
    if isinstance(references, Mapping):
        for name, entry in references.items():
            label = f"references.{name}"
            if isinstance(entry, Mapping):
                raw_path, description = entry.get("path"), entry.get("description", "")
            elif isinstance(entry, str):
                raw_path, description = entry, ""
            else:
                skipped.append((label, "Nieprawidłowy wpis referencji."))
                continue
            attempt(label, raw_path, MODE_RO, name, description, False)

    return MigrationResult(True, [root.to_dict() for root in accepted], skipped)
