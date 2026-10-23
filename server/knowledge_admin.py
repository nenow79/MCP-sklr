"""Panelowa konfiguracja bundles. Nie nadaje roots i nie usuwa plików."""
import hashlib
import hmac
import json
import secrets
from dataclasses import asdict, replace

from okf_engine.bundles import BundleError, evaluate_bundles, parse_entry
from okf_engine.coordination import coordinate_refresh
from okf_engine.discovery import effective_knowledge, project_key
from okf_engine.public_view import mask_text
from okf_engine.write_policy import parse_policy, policy_view
from state import resolve_central_skills_dir


class KnowledgeConflict(ValueError):
    pass


class KnowledgeAdmin:
    def __init__(self, store, roots, service):
        self.store, self.roots, self.service = store, roots, service
        self._key = secrets.token_bytes(32)

    def _revision(self):
        config = self.store.snapshot()
        knowledge = effective_knowledge(config.get("knowledge", {}), self.roots.list())
        raw = json.dumps([knowledge, config.get("roots")], sort_keys=True,
                         ensure_ascii=False).encode("utf-8")
        return hmac.new(self._key, raw, hashlib.sha256).hexdigest()

    def _check(self, revision):
        if not isinstance(revision, str) or not hmac.compare_digest(revision.encode(), self._revision().encode()):
            raise KnowledgeConflict("Konfiguracja zmieniła się. Odśwież widok Wiedza.")

    def _states(self, knowledge):
        roots = self.roots.list()
        return evaluate_bundles(effective_knowledge(knowledge, roots), roots,
                                resolve_central_skills_dir(self.store.snapshot()), allow_project=True)

    def list(self):
        with self.store.lock:
            config = self.store.snapshot().get("knowledge", {})
            states = self._states(config)
            manual = config.get("bundles", []) if isinstance(config, dict) else []
            manual_count = len(manual) if isinstance(manual, list) else 0
            public = {item["id"]: item for item in self.service.bundles()["bundles"]}
            entries = []
            for index, state in enumerate(states):
                entry = state.entry
                if state.available and entry.scope == 'project':
                    reason = getattr(self.service, 'project_reason', lambda root: '')(entry.root)
                    if reason:
                        state = replace(state, available=False, writable=False, reason=reason)
                automatic = index >= manual_count
                entries.append({"key": "auto:" + state.id if automatic else str(index), "id": state.id,
                                "automatic": automatic,
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
                                "indexed_documents": public.get(state.id, {}).get("indexed_documents", 0),
                                "reasons": public.get(state.id, {}).get("reasons", []),
                                "excluded_documents": public.get(state.id, {}).get("excluded_documents", 0),
                                "refresh_task_id": public.get(state.id, {}).get("refresh_task_id"),
                                "index_error": public.get(state.id, {}).get("error"),
                                "last_refresh": public.get(state.id, {}).get("last_refresh", {})})
            return {"bundles": entries, "count": len(entries), "revision": self._revision(),
                    "roots": [{"alias": root.alias, "mode": root.mode,
                               "write_policy": policy_view(config, root.path)} for root in self.roots.list()]}

    def set_project_policy(self, proposal, revision):
        """Jawna akcja użytkownika w panelu; bez audytu/konwersji i zapisu dokumentów."""
        with self.store.lock:
            self._check(revision)
            if not isinstance(proposal, dict):
                raise ValueError("Oczekiwano polityki projektu.")
            root = self.roots.get(proposal.get("root"))
            if root is None:
                raise ValueError("Wybierz istniejący folder z zakładki Foldery.")
            policy = parse_policy({**proposal, "root_path": root.path})

            def mutate(config):
                section = config.setdefault("knowledge", {})
                rows = section.get("project_write_policies", [])
                if not isinstance(rows, list):
                    raise ValueError("Niepoprawna konfiguracja polityk projektu.")
                # Nie usuwamy cudzych lub uszkodzonych wpisów przy aktualizacji.
                parsed = [parse_policy(row) for row in rows]
                section["project_write_policies"] = [asdict(row) for row in parsed
                    if project_key(row.root_path) != project_key(root.path)] + [asdict(policy)]
                # JSON zapisuje tuple jako listy, ale stan w pamięci musi być taki sam.
                for row in section["project_write_policies"]:
                    row["subpaths"], row["exclusions"] = list(row["subpaths"]), list(row["exclusions"])

            self.store.update(mutate)
            return self.list()

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
                state = states[len(entries) - 1]
                if state.reason not in ("", "disabled"):
                    raise ValueError("Bundle niedostępne: " + state.reason)
            self.store.update(mutate)
            return self.list()

    @coordinate_refresh
    def change(self, key, action, enabled, revision):
        with self.store.lock:
            self._check(revision)
            if not isinstance(key, str) or action not in ("enabled", "remove", "refresh"):
                raise ValueError("Niepoprawna operacja bundle.")
            if key.startswith("auto:"):
                return self._change_automatic(key[5:], action, enabled)
            if not key.isdigit():
                raise ValueError("Niepoprawna operacja bundle.")
            index = int(key)
            manual = self.store.snapshot().get("knowledge", {}).get("bundles", [])
            if not isinstance(manual, list) or index >= len(manual):
                raise KnowledgeConflict("Wpis już nie istnieje. Odśwież widok.")
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

    def _change_automatic(self, bundle_id, action, enabled):
        section = self.store.snapshot().get("knowledge", {})
        manual = section.get("bundles", [])
        state = next((state for state in self._states(section)[len(manual):]
                      if state.id == bundle_id), None)
        if state is None:
            raise KnowledgeConflict("Źródło już nie istnieje. Odśwież widok.")
        if action == "refresh":
            if not state.available:
                raise ValueError("Nie można odświeżyć niedostępnego bundle.")
            self.service.refresh_bundle(state.id)
        elif action == "enabled" and isinstance(enabled, bool):
            identity = project_key(state.entry.root_path)

            def mutate(config):
                section = config.setdefault("knowledge", {})
                disabled = section.get("project_docs_disabled", [])
                if not isinstance(disabled, list) or any(not isinstance(p, str) for p in disabled):
                    raise ValueError("Niepoprawna konfiguracja wyłączeń dokumentacji.")
                section["project_docs_disabled"] = [p for p in disabled if project_key(p) != identity]
                if not enabled:
                    section["project_docs_disabled"].append(state.entry.root_path)
                elif not next(item for item in self._states(section) if item.id == bundle_id).available:
                    raise ValueError("Nie można włączyć niedostępnego bundle. Sprawdź diagnostykę.")

            self.store.update(mutate)
        else:
            raise ValueError("Automatyczne źródło można włączyć lub wyłączyć.")
        return self.list()
