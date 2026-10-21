"""Resolve the entire selected knowledge scope before any search/document read."""
from dataclasses import asdict

from autopilot.operations import AccessSnapshot, OperationDescriptor, ResourceAccess, ResourceTarget
from autopilot.paths import Scope
from autopilot.rules import BundleRef


class KnowledgeAdapter:
    def __init__(self, service, files, generations):
        self.service, self.files, self.generations = service, files, generations
        self._known = set()

    def reference(self, state):
        entry = state.entry
        dependency = None
        if entry.scope == 'project':
            root = self.files.roots.resolve(entry.root)
            ref, available = self.files.root_ref(root)
            dependency = asdict(ref)
            if not available:
                raise ValueError('access_denied')
        info = state.path.stat()
        digest = self.generations.digest([asdict(entry), str(state.path), dependency, info.st_dev, info.st_ino])
        epoch = self.generations.observe(('bundle', state.id), digest, available=state.available)
        return BundleRef(state.id, digest, epoch)

    def build(self, binding, tool, args):
        if self.service is None:
            raise ValueError('access_denied')
        with self.service._lock:
            self.service._sync()
            available = set(self.service._states)
            for missing in self._known - available:
                self.generations.invalidate(('bundle', missing))
            self._known = available
            selected = (sorted(self.service._states) if tool == 'knowledge_bundles' else
                        self.service._select(args.get('bundle_id'), args.get('scope', 'all')))
            targets, access = [], []
            for key in selected:
                state = self.service._current(key)
                ref = self.reference(state)
                scope = None if tool == 'knowledge_bundles' else (
                    Scope('file', args['concept_id'] + '.md') if tool == 'knowledge_get' else Scope('tree', '.'))
                targets.append(ResourceTarget(ref, scope))
                access.append(ResourceAccess(ref, checked=True, available=state.available))
        action = {'knowledge_bundles': 'list', 'knowledge_search': 'search', 'knowledge_get': 'read'}[tool]
        return OperationDescriptor(binding, tool, action, targets=tuple(targets)), AccessSnapshot(
            binding, resources=tuple(access)), not targets
