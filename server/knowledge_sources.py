"""Live OKF sources and per-bundle configuration generations.

Config listeners perform RAM comparisons only. Filesystem discovery/rechecks
remain in source/current; a cached generation never grants filesystem access.
"""
from dataclasses import asdict, replace
import json
from pathlib import Path

from okf_engine.bundles import evaluate_bundles, recheck_bundle
from okf_engine.discovery import effective_knowledge
from okf_engine.limits import parse_limits
from state import resolve_central_skills_dir


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _overlaps(first, second):
    a, b = Path(first), Path(second)
    return a == b or a in b.parents or b in a.parents


class KnowledgeSources:
    def __init__(self, store, roots, selection=None, evaluate=evaluate_bundles):
        self.store, self.roots, self.selection = store, roots, selection
        self.evaluate = evaluate
        self._tracked = {}
        self._revision = 0
        self._evaluated = None
        self._states = {}
        store.add_change_listener(self._changed)

    def _dependencies(self, config, entry):
        """No filesystem I/O: retain only configuration relevant to this bundle."""
        knowledge = config.get('knowledge', {})
        target = Path(entry.root_path) / entry.subpath
        roots = [row for row in config.get('roots', []) if isinstance(row, dict)
                 and (row.get('alias') == entry.root or
                      isinstance(row.get('path'), str) and _overlaps(target, row['path']))]
        # The default marker selects legacy search scope; it grants no access.
        roots = [{key: value for key, value in row.items() if key != 'default'} for row in roots]
        bundles = []
        configured = knowledge.get('bundles', [])
        limits, _ = parse_limits(knowledge.get('limits', {}))
        positions = []
        for index, row in enumerate(configured):
            if not isinstance(row, dict):
                continue
            if row.get('id') == entry.id:
                positions.append(limits is not None and index < limits.max_bundles)
            path = row.get('root_path')
            subpath = row.get('subpath', '')
            if (row.get('id') == entry.id or isinstance(path, str) and isinstance(subpath, str)
                    and _overlaps(target, Path(path) / subpath)):
                bundles.append(row)
        disabled = knowledge.get('project_docs_disabled', [])
        if isinstance(disabled, list) and all(isinstance(p, str) for p in disabled):
            from okf_engine.discovery import project_key
            disabled = project_key(entry.root_path) in {project_key(p) for p in disabled}
        else:
            disabled = 'invalid_project_docs_disabled'
        return _json([asdict(entry), roots, bundles, positions, knowledge.get('limits', {}),
                      disabled if entry.scope == 'project' else None,
                      str(resolve_central_skills_dir(config))])

    def _observe(self, config, entry):
        try:
            signature = self._dependencies(config, entry)
        except (TypeError, ValueError, KeyError, AttributeError):
            # A config invalidator must not turn a malformed configuration
            # update into a new exception/rollback path for unrelated writers.
            signature = 'invalid_configuration'
        previous = self._tracked.get(entry.id)
        epoch = previous[2] if previous is not None else 0
        if previous is not None and signature != previous[1]:
            epoch += 1
        self._tracked[entry.id] = (entry, signature, epoch)
        return signature, epoch

    def _changed(self):
        # Called under ConfigStore.lock; catch changes followed by restoration
        # even when no request evaluated the intermediate configuration.
        self._revision += 1
        for entry, _, _ in tuple(self._tracked.values()):
            self._observe(self.store.data, entry)

    def project_reason(self, root):
        if self.selection is None:
            return ''
        sid, aliases = self.selection()
        return 'session_not_selected' if sid is None else ('' if root in aliases else 'project_not_in_session')

    def _inputs(self):
        config = self.store.snapshot()
        roots = self.roots.list()
        skills = resolve_central_skills_dir(config)
        selection = self.selection() if self.selection is not None else None
        token = (_json([config.get('knowledge', {}), [r.to_dict() for r in roots], str(skills)]),
                 self._revision, selection)
        return config, roots, skills, selection, token

    def source(self):
        with self.store.lock:
            config, roots, skills, selection, token = self._inputs()
            knowledge = effective_knowledge(config.get('knowledge', {}), roots)
            limits, _ = parse_limits(knowledge.get('limits', {}) if isinstance(knowledge, dict) else None)
            states = self.evaluate(knowledge, roots, skills, allow_project=True)
            versions = {}
            selected = []
            for state in states:
                if state.available and state.entry.scope == 'project':
                    reason = self.project_reason(state.entry.root)
                    if reason:
                        state = replace(state, available=False, writable=False, reason=reason)
                selected.append(state)
                if state.available:
                    versions[state.id] = (self._observe(config, state.entry), state.writable,
                                          str(state.path), selection if state.entry.scope == 'project' else None)
            self._evaluated = token
            self._states = {state.id: state for state in selected if state.available}
            return selected, limits, versions

    def current(self, bundle_id):
        with self.store.lock:
            _, _, skills, _, token = self._inputs()
            if token != self._evaluated:
                return None
            state = self._states.get(bundle_id)
            return recheck_bundle(state, skills) if state is not None else None
