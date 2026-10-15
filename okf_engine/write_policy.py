"""Polityka formatu dokumentacji: domyślna zgodność i jawny wymóg OKF.

Nie czyta dysku, nie zmienia plików, nie nadaje praw. Ścieżki polityki są
względne wobec przypiętego projektu; walidacja treści używa profilu parsera v1.
"""
from dataclasses import dataclass
from pathlib import Path

from .bundles import BundleError, parse_entry, _subpath
from .discovery import project_key
from .models import DocumentIdentity
from .parser import parse_document

COMPATIBLE = "compatible"
STRICT = "require_okf"
DEFAULT_EXCLUSIONS = ("docs/archive/", "docs/archives/")
_IDENTITY = DocumentIdentity("write-policy", "document")
_SPECIAL = frozenset({"agents.md", "index.md", "log.md"})


class WritePolicyError(ValueError):
    """Wyłącznie stałe komunikaty, bez treści dokumentu lub wyjątków YAML."""


@dataclass(frozen=True)
class ProjectWritePolicy:
    root_path: str
    mode: str = COMPATIBLE
    subpaths: tuple = ("docs",)
    exclusions: tuple = DEFAULT_EXCLUSIONS


def _paths(values, *, exclusions=False):
    if not isinstance(values, list) or len(values) > 50 or (not exclusions and not values):
        raise WritePolicyError("Niepoprawny zakres lub wyjątki polityki OKF.")
    result = []
    for value in values:
        if not isinstance(value, str) or not value or len(value) > 500:
            raise WritePolicyError("Niepoprawna ścieżka polityki OKF.")
        directory = exclusions and value.endswith("/")
        try:
            path = _subpath(value[:-1] if directory else value)
        except BundleError:
            raise WritePolicyError("Polityka OKF wymaga względnych ścieżek bez wildcardów i .. .") from None
        if not path:
            raise WritePolicyError("Zakres polityki OKF nie może być pusty.")
        result.append(path + ("/" if directory else ""))
    return tuple(dict.fromkeys(result))


def parse_policy(raw):
    if (not isinstance(raw, dict) or raw.get("mode") not in (COMPATIBLE, STRICT)
            or not isinstance(raw.get("root_path"), str) or not Path(raw["root_path"]).is_absolute()):
        raise WritePolicyError("Niepoprawna konfiguracja polityki OKF.")
    return ProjectWritePolicy(raw["root_path"], raw["mode"],
                              _paths(raw.get("subpaths", ["docs"])),
                              _paths(raw.get("exclusions", list(DEFAULT_EXCLUSIONS)), exclusions=True))


def project_policy(knowledge, root_path):
    section = knowledge if isinstance(knowledge, dict) else {}
    rows = section.get("project_write_policies", [])
    if not isinstance(rows, list):
        raise WritePolicyError("Niepoprawna konfiguracja polityki OKF.")
    matches = []
    for row in rows:
        # Uszkodzona konfiguracja nie może po cichu wyłączyć ochrony.
        policy = parse_policy(row)
        if project_key(policy.root_path) == project_key(root_path):
            matches.append(policy)
    if len(matches) > 1:
        raise WritePolicyError("Powielona polityka OKF projektu.")
    return matches[0] if matches else ProjectWritePolicy(root_path)


def policy_view(knowledge, root_path):
    try:
        policy = project_policy(knowledge, root_path)
    except WritePolicyError as exc:
        return {"mode": "invalid", "subpaths": [], "exclusions": [], "error": str(exc)}
    return {"mode": policy.mode, "subpaths": list(policy.subpaths),
            "exclusions": list(policy.exclusions), "error": None}


def _inside(relative, directory):
    relative, directory = project_key(relative), project_key(directory)
    return relative == directory or Path(directory) in Path(relative).parents


def _excluded(relative, exclusions):
    if Path(relative).name.casefold() in _SPECIAL:
        return True
    return any(_inside(relative, path[:-1]) if path.endswith("/")
               else project_key(relative) == project_key(path) for path in exclusions)


def _documentation(knowledge, root, relative, policy):
    if any(_inside(relative, subpath) for subpath in ("docs", *policy.subpaths)):
        return True
    if relative.casefold() in (".vibecoder_context.md", ".vibecoder_checkpoints.md"):
        return True
    for raw in knowledge.get("bundles", []) if isinstance(knowledge.get("bundles", []), list) else []:
        try:
            entry = parse_entry(raw)
        except BundleError:
            continue
        if (entry.scope == "project" and entry.root.lower() == root.alias.lower()
                and project_key(entry.root_path) == project_key(root.path)
                and entry.profile == "docs" and entry.subpath and _inside(relative, entry.subpath)):
            return True
    return False


def validate_document(knowledge, roots, path, before, after):
    """Waliduje wynik zmiany .md; zakres wyznacza fizyczna ścieżka, nie wybrany alias.

    Wyjątki trybu strict nie pozwalają zepsuć już poprawnego OKF. Brak zmiany
    bajtów nie wymaga konwersji starych plików. Odczyty nigdy tu nie trafiają.
    """
    if Path(path).suffix.casefold() != ".md" or before == after:
        return
    section = knowledge if isinstance(knowledge, dict) else {}
    require = preserve = False
    for root in roots:
        try:
            relative = Path(project_key(path)).relative_to(Path(project_key(root.path))).as_posix()
        except ValueError:
            continue
        policy = project_policy(section, root.path)
        preserve = preserve or _documentation(section, root, relative, policy)
        require = require or (policy.mode == STRICT
                              and any(_inside(relative, p) for p in policy.subpaths)
                              and not _excluded(relative, policy.exclusions))
    if not preserve and not require:
        return
    was_okf = preserve and before is not None and parse_document(before, _IDENTITY).ok
    if (require or was_okf) and not parse_document(after, _IDENTITY).ok:
        if was_okf:
            raise WritePolicyError("OKF_FORMAT_LOSS: zmiana usuwa lub psuje format istniejącego dokumentu OKF.")
        raise WritePolicyError("OKF_REQUIRED: dokument w tym zakresie wymaga poprawnego OKF. Użyj knowledge_convert lub knowledge_save.")


def validate_directory_rename(knowledge, roots, source, target):
    """Rename nie może przenieść zwykłego Markdown do aktywnego zakresu strict."""
    for root in roots:
        policy = project_policy(knowledge, root.path)
        if policy.mode != STRICT:
            continue
        for subpath in policy.subpaths:
            protected = Path(root.path) / subpath
            if any(_inside(path, protected) or _inside(protected, path) for path in (source, target)):
                raise WritePolicyError("OKF_REQUIRED: zmiana nazwy katalogu obejmuje chroniony zakres. Zmień zakres polityki w panelu przed operacją.")
