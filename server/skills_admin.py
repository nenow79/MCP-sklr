"""Administracja skilli dla panelu; niezależna od HTTP i FastMCP."""
import threading
import re
from datetime import datetime, timezone

from skills_engine.models import SOURCE_CENTRAL, SOURCE_DEV
from skills_engine.policy import declares_no_confirm
from skills_engine.trust import compute_skill_hash


class SkillNotFound(ValueError):
    pass


class SkillConflict(ValueError):
    pass


class SkillsAdmin:
    def __init__(self, registry, config_store):
        self.registry = registry
        self.store = config_store
        self._lock = threading.RLock()

    def list(self):
        with self._lock:
            self.registry.rescan()
            entries = self.registry.list_all()
            config = self.store.snapshot()
            records = []
            trust = config.get("skills", {}).get("trust", {})
            for entry in entries:
                item = self._entry(entry)
                item.update(self._trust_status(entry, trust))
                records.append(item)
            return {
                "count": len(records), "skills": records,
                "dev_dirs": list(config.get("skills", {}).get("dev_dirs", [])),
            }

    @staticmethod
    def _entry(entry):
        record = entry["record"]
        return {
            "name": record.name, "description": record.description,
            "source": record.source, "path": str(record.base_dir),
            "warnings": list(record.warnings),
            "enabled": entry["enabled"], "shadowed": entry["shadowed"],
        }

    @staticmethod
    def _trust_status(entry, trust):
        record = entry["record"]
        status = {"trust_status": "not_applicable", "current_hash": None,
                  "trusted_at": None, "declares_no_confirm": declares_no_confirm(record.metadata),
                  "can_skip_approval": False}
        if record.source != SOURCE_CENTRAL or entry["shadowed"]:
            return status
        try:
            current = compute_skill_hash(record.base_dir)
        except Exception:
            status["trust_status"] = "hash_error"
            return status
        saved = trust.get(record.name) if isinstance(trust, dict) else None
        status["current_hash"] = current
        if isinstance(saved, dict) and isinstance(saved.get("hash"), str):
            status["trusted_at"] = saved.get("trusted_at")
            status["trust_status"] = "trusted" if saved["hash"] == current else "changed"
        else:
            status["trust_status"] = "untrusted"
        status["can_skip_approval"] = (
            status["trust_status"] == "trusted" and status["declares_no_confirm"])
        return status

    def _central_winner(self, name, expected_path):
        if not isinstance(name, str) or not name:
            raise ValueError("Podaj nazwę skilla.")
        if not isinstance(expected_path, str) or not expected_path:
            raise ValueError("Podaj ścieżkę wersji skilla pokazanej w panelu.")
        self.registry.rescan()
        winner = next((e for e in self.registry.list_all()
                       if e["record"].name == name and not e["shadowed"]), None)
        if winner is None:
            raise SkillNotFound("Nie znaleziono skilla.")
        record = winner["record"]
        if record.source != SOURCE_CENTRAL or str(record.base_dir) != expected_path:
            raise SkillConflict("Źródło lub ścieżka skilla zmieniły się. Odśwież panel.")
        return record

    def trust_version(self, name, expected_path, expected_hash):
        if not isinstance(expected_hash, str) or not re.fullmatch("[0-9a-f]{64}", expected_hash):
            raise ValueError("Niepoprawny hash wersji skilla.")
        with self._lock:
            record = self._central_winner(name, expected_path)
            try:
                current = compute_skill_hash(record.base_dir)
            except Exception:
                raise SkillConflict("Nie można sprawdzić wersji skilla. Odśwież panel.") from None
            if current != expected_hash:
                raise SkillConflict("Wersja skilla zmieniła się. Odśwież panel i potwierdź ponownie.")

            def mutate(config):
                trust = config.setdefault("skills", {}).setdefault("trust", {})
                if not isinstance(trust, dict):
                    raise ValueError("Niepoprawna konfiguracja zaufania.")
                trust[name] = {"hash": current,
                               "trusted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

            self.store.update(mutate)
            return {"name": name, "hash": current, "trusted": True}

    def revoke_trust(self, name, expected_path):
        with self._lock:
            self._central_winner(name, expected_path)

            def mutate(config):
                trust = config.setdefault("skills", {}).setdefault("trust", {})
                if not isinstance(trust, dict):
                    raise ValueError("Niepoprawna konfiguracja zaufania.")
                trust.pop(name, None)

            self.store.update(mutate)
            return {"name": name, "trusted": False}

    def set_enabled(self, name, enabled):
        if not isinstance(name, str) or not name:
            raise ValueError("Podaj nazwę skilla.")
        if not isinstance(enabled, bool):
            raise ValueError("Pole 'enabled' musi być true lub false.")
        # Serializuje zapisy z panelu wraz z publikacją flag do rejestru.
        with self._lock:
            self.registry.rescan()
            winner = next((e for e in self.registry.list_all()
                           if e["record"].name == name and not e["shadowed"]), None)
            if winner is None:
                raise SkillNotFound("Nie znaleziono skilla.")
            source = winner["record"].source

            def mutate(config):
                skills = config.setdefault("skills", {})
                key = "dev_enabled" if source == SOURCE_DEV else "disabled"
                raw = skills.get(key, [])
                if not isinstance(raw, list) or not all(isinstance(n, str) for n in raw):
                    raise ValueError("Niepoprawna konfiguracja flag skilli.")
                names = set(raw)
                include = enabled if source == SOURCE_DEV else not enabled
                if include:
                    names.add(name)
                else:
                    names.discard(name)
                skills[key] = sorted(names)

            self.store.update(mutate)
            config = self.store.snapshot().get("skills", {})
            self.registry.set_disabled(config.get("disabled", []))
            self.registry.set_dev_enabled(config.get("dev_enabled", []))
            return {"name": name, "source": source, "enabled": enabled}
