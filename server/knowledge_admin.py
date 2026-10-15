"""Panelowa konfiguracja bundles. Nie nadaje roots i nie usuwa plików."""
import hashlib
import hmac
import json
import secrets

from okf_engine.bundles import BundleError, evaluate_bundles, parse_entry
from okf_engine.public_view import mask_text
from state import resolve_central_skills_dir


class KnowledgeConflict(ValueError):
    pass


class KnowledgeAdmin:
    def __init__(self, store, roots, service):
        self.store, self.roots, self.service = store, roots, service
        self._key = secrets.token_bytes(32)

    def _revision(self):
        config = self.store.snapshot()
        raw = json.dumps([config.get("knowledge"), config.get("roots")], sort_keys=True,
                         ensure_ascii=False).encode("utf-8")
        return hmac.new(self._key, raw, hashlib.sha256).hexdigest()

    def _check(self, revision):
        if not isinstance(revision, str) or not hmac.compare_digest(revision.encode(), self._revision().encode()):
            raise KnowledgeConflict("Konfiguracja zmieniła się. Odśwież widok Wiedza.")

    def _states(self, knowledge):
        return evaluate_bundles(knowledge, self.roots.list(),
                                resolve_central_skills_dir(self.store.snapshot()), allow_project=True)

    def list(self):
        with self.store.lock:
            config = self.store.snapshot().get("knowledge", {})
            states = self._states(config)
            public = {item["id"]: item for item in self.service.bundles()["bundles"]}
            entries = []
            for index, state in enumerate(states):
                entry = state.entry
                entries.append({"key": str(index), "id": state.id,
                                "label": mask_text(entry.label).text if entry else "Niepoprawny wpis",
                                "description": mask_text(entry.description).text if entry else "",
                                "root": entry.root if entry else None,
                                "subpath": entry.subpath if entry else None,
                                "scope": entry.scope if entry else None,
                                "profile": entry.profile if entry else None,
                                "enabled": entry.enabled if entry else False,
                                "available": state.available, "writable": state.writable,
                                "reason": state.reason, "detail": state.detail,
                                "index_state": public.get(state.id, {}).get("index_state", "empty"),
                                "indexed_documents": public.get(state.id, {}).get("indexed_documents", 0)})
            return {"bundles": entries, "count": len(entries), "revision": self._revision(),
                    "roots": [{"alias": root.alias, "mode": root.mode} for root in self.roots.list()]}

    def _entries(self, config):
        section = config.setdefault("knowledge", {})
        if not isinstance(section, dict) or not isinstance(section.get("bundles", []), list):
            raise ValueError("Niepoprawna konfiguracja wiedzy.")
        return section.setdefault("bundles", [])

    def add(self, proposal, revision):
        with self.store.lock:
            self._check(revision)
            if not isinstance(proposal, dict):
                raise ValueError("Oczekiwano konfiguracji bundle.")
            root = self.roots.get(proposal.get("root"))
            if root is None:
                raise ValueError("Wybierz istniejący folder z zakładki Foldery.")
            raw = {key: proposal[key] for key in
                   ("id", "label", "scope", "subpath", "description", "enabled", "profile") if key in proposal}
            raw.update(root=root.alias, root_path=root.path)
            try:
                parse_entry(raw)
            except BundleError as exc:
                raise ValueError("Niepoprawne pole: " + exc.code) from None

            def mutate(config):
                entries = self._entries(config)
                entries.append(raw)
                states = self._states(config["knowledge"])
                # Nie wolno unieważnić innego bundle przez nakładający się katalog.
                if states[-1].reason not in ("", "disabled"):
                    raise ValueError("Bundle niedostępne: " + states[-1].reason)
            self.store.update(mutate)
            return self.list()

    def change(self, key, action, enabled, revision):
        with self.store.lock:
            self._check(revision)
            if not isinstance(key, str) or not key.isdigit() or action not in ("enabled", "remove", "refresh"):
                raise ValueError("Niepoprawna operacja bundle.")
            index = int(key)
            if action == "enabled" and not isinstance(enabled, bool):
                raise ValueError("enabled musi być true lub false.")
            if action == "refresh":
                section = self.store.snapshot().get("knowledge", {})
                states = self._states(section)
                if index >= len(states) or not states[index].available:
                    raise ValueError("Nie można odświeżyć niedostępnego bundle.")
                self.service.refresh_bundle(states[index].id)
                return self.list()

            def mutate(config):
                entries = self._entries(config)
                if index >= len(entries):
                    raise KnowledgeConflict("Wpis już nie istnieje. Odśwież widok.")
                if action == "remove":
                    entries.pop(index)
                else:
                    if not isinstance(entries[index], dict):
                        raise ValueError("Usuń niepoprawny wpis i dodaj go ponownie.")
                    entries[index]["enabled"] = enabled
                    if enabled and not self._states(config["knowledge"])[index].available:
                        raise ValueError("Nie można włączyć niedostępnego bundle. Sprawdź diagnostykę.")
            self.store.update(mutate)
            return self.list()
