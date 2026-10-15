"""Nietrwałe projektowe bundles dla bezpośredniego <root>/docs.

Odkrywanie nie czyta dokumentów, nie zapisuje konfiguracji i nie nadaje praw.
Wygenerowane wpisy przechodzą następnie zwykłe evaluate_bundles/path guardy.
"""
import hashlib
import os
from pathlib import Path
from collections.abc import Mapping

from .bundles import BundleError, parse_entry


def project_key(path):
    """Tożsamość przypiętej ścieżki; bez resolve, które podążałoby za linkami."""
    return os.path.normcase(os.path.normpath(str(path)))


def effective_knowledge(knowledge, roots):
    """Ręczne wpisy najpierw, automatyczne na końcu; nie mutuje wejścia.

    Ręczne wiązanie (także wyłączone) ma pierwszeństwo w nakładającym się
    zakresie. Wpis ze starym root_path nie blokuje nowego projektu.
    Niepoprawna lista wyłączeń wstrzymuje automatykę (fail closed).
    """
    if not isinstance(knowledge, Mapping):
        return knowledge
    manual = knowledge.get("bundles", [])
    if not isinstance(manual, list):
        return knowledge
    result = dict(knowledge, bundles=list(manual))
    disabled = knowledge.get("project_docs_disabled", [])
    if not isinstance(disabled, list) or any(not isinstance(p, str) for p in disabled):
        return result
    disabled = {project_key(p) for p in disabled}
    by_alias = {root.alias.lower(): root for root in roots}
    claimed = []
    ids = {raw.get("id") for raw in manual
           if isinstance(raw, Mapping) and isinstance(raw.get("id"), str)}
    for raw in manual:
        try:
            entry = parse_entry(raw)
        except BundleError:
            continue
        root = by_alias.get(entry.root.lower())
        if root is not None and project_key(root.path) == project_key(entry.root_path):
            claimed.append(Path(project_key(Path(entry.root_path) / entry.subpath)))

    for root in roots:
        target = Path(root.path) / "docs"
        try:
            # lstat zamiast is_dir: link/junction ma trafić do diagnostyki guardów.
            target.lstat()
        except (OSError, ValueError):
            continue
        path = Path(project_key(target))
        if any(path == other or path in other.parents or other in path.parents for other in claimed):
            continue
        identity = project_key(root.path)
        salt = 0
        while True:
            digest = hashlib.sha256((identity + ":" + str(salt)).encode("utf-8")).hexdigest()[:20]
            bundle_id = "auto-docs-" + digest
            if bundle_id not in ids:
                break
            salt += 1
        ids.add(bundle_id)
        result["bundles"].append({
            "id": bundle_id, "label": root.alias + " / docs", "description": "Dokumentacja projektu",
            "scope": "project", "profile": "docs", "root": root.alias,
            "root_path": root.path, "subpath": "docs", "enabled": identity not in disabled,
        })
    return result
