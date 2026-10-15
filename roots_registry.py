#!/usr/bin/env python3
"""RootsRegistry: lista folderów (roots) w config.json, bezpieczna wątkowo.

Wszystkie zmiany przechodzą przez ConfigStore.update (wspólny lock, zapis atomowy, wycofanie przy
błędzie). Operacje na systemie plików (validate_root) wykonujemy poza lockiem; pod lockiem działa
tylko czysta logika (check_new_root). Wpisy, których nie da się sparsować, są zachowywane w pliku
(nie giną po cichu), ale nie są widoczne dla narzędzi.
"""
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from config_store import ConfigStore
from roots import (
    MODES, MigrationResult, Root, RootError, RootNotFound, Zones, check_new_root, clean_description,
    find_root_for, forbidden_zones, migrate_legacy, needs_migration, unique_alias,
    validate_alias, validate_root,
)

logger = logging.getLogger(__name__)

ZonesSource = Union[Zones, Callable[[], Zones], None]
DefaultListener = Callable[[Optional[Root], Optional[Root]], None]

_DEFAULT_WORDS = ("", "false", "none", "null")  # tolerancja: model bywa niechlujny z parametrem root
_FALLBACK_CENTRAL = "~/.agents/skills"


def _peek(config: Dict[str, Any]) -> List[Any]:
    """Lista wpisów bez modyfikowania configu."""
    value = config.get("roots")
    return value if isinstance(value, list) else []


def _raw_list(config: Dict[str, Any]) -> List[Any]:
    """Lista wpisów do modyfikacji (tworzy ją, gdy config ma roots == None)."""
    value = config.get("roots")
    if not isinstance(value, list):
        value = []
        config["roots"] = value
    return value


def _parse(raw: List[Any]) -> List[Tuple[int, Root]]:
    """Poprawne wpisy z indeksem w liście surowej; co najwyżej jeden domyślny (pierwszy wygrywa)."""
    parsed: List[Tuple[int, Root]] = []
    default_seen = False
    for index, entry in enumerate(raw):
        try:
            root = Root.from_dict(entry)
        except RootError:
            continue
        if root.default:
            if default_seen:
                root = replace(root, default=False)
            default_seen = True
        parsed.append((index, root))
    return parsed


def _default_of(parsed: List[Tuple[int, Root]]) -> Optional[Root]:
    for _, root in parsed:
        if root.default:
            return root
    return None


def _find(parsed: List[Tuple[int, Root]], alias: Any) -> Optional[Tuple[int, Root]]:
    wanted = str(alias).strip().lower()
    for index, root in parsed:
        if root.alias.lower() == wanted:
            return index, root
    return None


def _aliases(parsed: List[Tuple[int, Root]]) -> List[str]:
    return [root.alias for _, root in parsed]


class RootsRegistry:
    def __init__(self, store: ConfigStore, zones: ZonesSource = None,
                 central_dir: Union[Path, str, Callable[[], Any], None] = None,
                 on_default_change: Optional[DefaultListener] = None) -> None:
        self._store = store
        self._zones_source = zones
        self._central_source = central_dir
        self._listener = on_default_change

    # --- konfiguracja wewnętrzna ---
    def set_default_listener(self, listener: Optional[DefaultListener]) -> None:
        self._listener = listener

    def _zones(self) -> Zones:
        source = self._zones_source
        if isinstance(source, Zones):
            return source
        if callable(source):
            return source()
        return forbidden_zones()

    def _central(self) -> Optional[Path]:
        source = self._central_source
        if callable(source):
            source = source()
        if source is None:
            with self._store.lock:
                skills = self._store.data.get("skills")
                source = skills.get("central_dir") if isinstance(skills, dict) else None
            source = source or _FALLBACK_CENTRAL
        try:
            return Path(str(source)).expanduser()
        except (OSError, RuntimeError):
            return None

    def _notify(self, old: Optional[Root], new: Optional[Root]) -> None:
        """Informuje o zmianie domyślnego roota (poza lockiem; błąd słuchacza nie psuje operacji)."""
        old_path = old.path if old else None
        new_path = new.path if new else None
        if old_path == new_path or self._listener is None:
            return
        try:
            self._listener(old, new)
        except Exception:
            logger.exception("Słuchacz zmiany domyślnego folderu zgłosił błąd")

    # --- odczyt ---
    def config_snapshot(self) -> Dict[str, Any]:
        """Prywatna dla serwera kopia konfiguracji dla wspólnych polityk zapisów."""
        return self._store.snapshot()

    def _parsed(self) -> List[Tuple[int, Root]]:
        with self._store.lock:
            return _parse(list(_peek(self._store.data)))

    def list(self) -> List[Root]:
        return [root for _, root in self._parsed()]

    def get(self, alias: Any) -> Optional[Root]:
        found = _find(self._parsed(), alias)
        return found[1] if found else None

    def default(self) -> Optional[Root]:
        return _default_of(self._parsed())

    def find(self, path: Any) -> Optional[Root]:
        """Najgłębszy root zawierający ścieżkę (jego tryb obowiązuje) albo None."""
        return find_root_for(path, self.list())

    def list_status(self) -> List[Dict[str, Any]]:
        """Lista dla panelu i narzędzi: wpis + dostępność (folder mógł zniknąć po dodaniu)."""
        result = []
        for root in self.list():
            entry = root.to_dict()
            available = Path(root.path).is_dir()
            entry["valid"] = available
            entry["reason"] = "" if available else "Folder nie istnieje lub jest niedostępny."
            result.append(entry)
        return result

    def resolve(self, alias: Any = None) -> Root:
        """Root dla parametru `root` narzędzia: brak, pusty napis, 'false' lub False = domyślny."""
        parsed = self._parsed()
        if alias is None or alias is False or (
                isinstance(alias, str) and alias.strip().lower() in _DEFAULT_WORDS):
            default = _default_of(parsed)
            if default is None:
                raise RootError(
                    "Brak domyślnego folderu. Dodaj folder w panelu zatwierdzeń (zakładka Foldery) "
                    "albo podaj parametr root. Dostępne: " + (", ".join(_aliases(parsed)) or "brak") + ".")
            return default
        if not isinstance(alias, str):
            raise RootError("Parametr root musi być nazwą (aliasem) folderu.")
        found = _find(parsed, alias)
        if found is None:
            raise RootError(
                f"Nieznany folder '{alias}'. Dostępne: " + (", ".join(_aliases(parsed)) or "brak") + ".")
        return found[1]

    # --- zmiany ---
    def check_add(self, path: Any, mode: Any, alias: Optional[str] = None,
                  description: Any = "") -> Root:
        """Sucha walidacja dodania (bez zapisu): zwraca kandydata albo zgłasza RootError.

        Używane przez request_root do odrzucenia złego żądania bez tworzenia zadania. Właściwe
        dodanie (add) waliduje ponownie, bo stan mógł się zmienić do czasu zatwierdzenia.
        """
        if mode not in MODES:
            raise RootError(f"Tryb folderu musi być jednym z: {list(MODES)}.")
        resolved = validate_root(path, mode, zones=self._zones(), central_dir=self._central())
        explicit_alias = validate_alias(alias) if alias else None
        parsed = self._parsed()
        roots = [root for _, root in parsed]
        final_alias = explicit_alias or unique_alias(resolved.name, _aliases(parsed))
        candidate = Root(final_alias, str(resolved), mode, clean_description(description),
                         _default_of(parsed) is None)
        check_new_root(candidate, roots)
        return candidate

    def add(self, path: Any, mode: Any, alias: Optional[str] = None, description: Any = "",
            make_default: bool = False) -> Root:
        """Dodaje folder. Pierwszy folder (albo gdy brak domyślnego) zostaje domyślnym."""
        if mode not in MODES:
            raise RootError(f"Tryb folderu musi być jednym z: {list(MODES)}.")
        resolved = validate_root(path, mode, zones=self._zones(), central_dir=self._central())
        explicit_alias = validate_alias(alias) if alias else None
        holder: Dict[str, Any] = {}

        def mutate(config: Dict[str, Any]) -> None:
            raw = _raw_list(config)
            parsed = _parse(raw)
            roots = [root for _, root in parsed]
            holder["old"] = _default_of(parsed)
            final_alias = explicit_alias or unique_alias(resolved.name, _aliases(parsed))
            becomes_default = bool(make_default) or holder["old"] is None
            candidate = Root(final_alias, str(resolved), mode, clean_description(description),
                             becomes_default)
            check_new_root(candidate, roots)
            if becomes_default:
                for index, root in parsed:
                    if root.default:
                        raw[index]["default"] = False
            raw.append(candidate.to_dict())
            holder["root"] = candidate
            holder["new"] = candidate if becomes_default else holder["old"]

        self._store.update(mutate)
        self._notify(holder["old"], holder["new"])
        return holder["root"]

    def update(self, alias: Any, *, mode: Any = None, description: Any = None,
               new_alias: Optional[str] = None) -> Root:
        """Zmienia tryb, opis lub alias (ścieżkę zmienia się przez usunięcie i dodanie)."""
        current = self.get(alias)
        if current is None:
            raise RootNotFound(f"Nieznany folder '{alias}'.")
        new_mode = current.mode if mode is None else mode
        if new_mode not in MODES:
            raise RootError(f"Tryb folderu musi być jednym z: {list(MODES)}.")
        target_alias = validate_alias(new_alias) if new_alias else None
        if new_mode != current.mode:
            # zmiana trybu wymaga ponownej walidacji ścieżki (ro -> rw nie może objąć katalogu skills);
            # zmiana opisu lub aliasu działa też dla folderu, który zniknął z dysku
            validate_root(current.path, new_mode, zones=self._zones(), central_dir=self._central())
        holder: Dict[str, Any] = {}

        def mutate(config: Dict[str, Any]) -> None:
            raw = _raw_list(config)
            parsed = _parse(raw)
            found = _find(parsed, alias)
            if found is None:
                raise RootNotFound(f"Nieznany folder '{alias}'.")
            index, root = found
            holder["old"] = _default_of(parsed)
            others = [other for i, other in parsed if i != index]
            candidate = Root(
                target_alias or root.alias, root.path, new_mode,
                root.description if description is None else clean_description(description),
                root.default)
            check_new_root(candidate, others)
            raw[index].update(candidate.to_dict())
            holder["root"] = candidate
            holder["new"] = _default_of(_parse(raw))

        self._store.update(mutate)
        self._notify(holder["old"], holder["new"])
        return holder["root"]

    def remove(self, alias: Any) -> Root:
        """Usuwa folder. Usunięcie domyślnego nie wybiera następnego (brak domyślnego do czasu wyboru)."""
        holder: Dict[str, Any] = {}

        def mutate(config: Dict[str, Any]) -> None:
            raw = _raw_list(config)
            parsed = _parse(raw)
            found = _find(parsed, alias)
            if found is None:
                raise RootNotFound(f"Nieznany folder '{alias}'.")
            index, root = found
            holder["old"] = _default_of(parsed)
            del raw[index]
            holder["root"] = root
            holder["new"] = _default_of(_parse(raw))

        self._store.update(mutate)
        self._notify(holder["old"], holder["new"])
        return holder["root"]

    def set_default(self, alias: Any) -> Root:
        holder: Dict[str, Any] = {}

        def mutate(config: Dict[str, Any]) -> None:
            raw = _raw_list(config)
            parsed = _parse(raw)
            found = _find(parsed, alias)
            if found is None:
                raise RootNotFound(f"Nieznany folder '{alias}'.")
            target_index, target = found
            holder["old"] = _default_of(parsed)
            for index, root in parsed:
                raw[index]["default"] = index == target_index
            holder["root"] = replace(target, default=True)
            holder["new"] = holder["root"]

        self._store.update(mutate)
        self._notify(holder["old"], holder["new"])
        return holder["root"]

    # --- migracja ---
    def migrate(self) -> MigrationResult:
        """Jednorazowa migracja workspace i references na roots (gdy config["roots"] == None)."""
        with self._store.lock:
            if not needs_migration(self._store.data):
                return MigrationResult(False)
            snapshot = self._store.snapshot()
        result = migrate_legacy(snapshot, zones=self._zones(), central_dir=self._central())

        def mutate(config: Dict[str, Any]) -> bool:
            if not needs_migration(config):  # ktoś zdążył wcześniej
                return False
            config["roots"] = result.roots
            return True

        result.migrated = bool(self._store.update(mutate))
        return result
