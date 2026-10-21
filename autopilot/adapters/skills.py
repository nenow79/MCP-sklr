"""Central skill identity, interpreter binding and current confirmation policy."""
import sys
import shutil
from pathlib import Path

from autopilot.operations import AccessSnapshot, OperationDescriptor, ResourceAccess, ResourceTarget
from autopilot.paths import Scope
from autopilot.rules import SkillRef
from server.skills_tools import PreparedRun, prepare_script_run
from skills_engine.conda_env import resolve_env_python
from skills_engine.content import resolve_resource_path
from skills_engine.policy import decide_confirmation
from skills_engine.trust import compute_skill_hash
from autopilot.adapters.restrictions import restricted_name


class SkillAdapter:
    def __init__(self, registry, trust, store, generations):
        self.registry, self.trust, self.store, self.generations = registry, trust, store, generations
        self._known = set()

    def reference(self, record):
        self._known.add(record.name)
        config = self.store.snapshot()
        python = sys.executable
        env = record.metadata.get('conda-env', '').strip()
        if env:
            yml = record.base_dir / 'assets' / 'environment.yml'
            resolution = resolve_env_python(env, config, environment_yml=yml if yml.is_file() else None)
            if resolution.python is None:
                raise ValueError('access_denied')
            python = str(resolution.python)
        interpreters = [shutil.which(name) for name in ('node', 'bash', 'pwsh', 'powershell')]
        execution = self.generations.digest([str(record.base_dir.resolve()), str(Path(python).resolve()),
            config.get('conda', {}), record.metadata.get('network-env', ''), interpreters])
        digest = compute_skill_hash(record.base_dir)
        trusted = self.trust.is_trusted(record.name, digest)
        decision = decide_confirmation(record.source, record.metadata, trusted)
        info = record.base_dir.stat()
        fingerprint = self.generations.digest([record.source, digest, execution, trusted, decision.required,
                                                info.st_dev, info.st_ino])
        epoch = self.generations.observe(('skill', record.name), fingerprint)
        ref = SkillRef(record.name, record.source, digest, execution, epoch)
        return ref, ResourceAccess(ref, checked=True, available=True, trusted=trusted,
                                   approval_required=decision.required, restricted=record.source != 'central')

    def build(self, binding, tool, args):
        if self.registry is None or self.trust is None:
            raise ValueError('access_denied')
        self.registry.rescan()
        current = {record.name for record in self.registry.list_active()}
        for missing in self._known - current:
            self.generations.invalidate(('skill', missing))
        self._known = current
        if tool == 'list_skills':
            snapshot = self.registry.snapshot()
            # Existing catalog also returns these diagnostics; do not read then filter.
            if snapshot.disabled or snapshot.shadowed_count or snapshot.diagnostics:
                raise ValueError('manual_required')
            references = [self.reference(record) for record in snapshot.active]
            return OperationDescriptor(binding, tool, 'list', targets=tuple(
                ResourceTarget(ref) for ref, access in references)), AccessSnapshot(
                binding, tuple(access for ref, access in references)), not references
        record = self.registry.get(args['name'])
        if record is None:
            self.generations.invalidate(('skill', args['name']))
            raise ValueError('resource_changed')
        ref, access = self.reference(record)
        if tool == 'run_skill_script':
            if restricted_name(record.name, args['script']):
                raise ValueError('manual_required')
            prepared = prepare_script_run(self.registry, self.store.snapshot(), self.trust,
                args['name'], args['script'], args.get('args'), args.get('timeout', 60))
            if type(prepared) is not PreparedRun:
                raise ValueError('invalid_operation')
            operation = OperationDescriptor(binding, tool, 'run', resource=ref, script=prepared.script_rel,
                args=tuple(prepared.args), timeout_s=prepared.timeout)
        else:
            path = 'SKILL.md' if tool == 'activate_skill' else args['path']
            resolved = resolve_resource_path(record.base_dir, path)
            scope = Scope('file', resolved.relative_to(record.base_dir.resolve()).as_posix())
            operation = OperationDescriptor(binding, tool, 'activate' if tool == 'activate_skill' else 'read',
                                            targets=(ResourceTarget(ref, scope),))
        return operation, AccessSnapshot(binding, (access,)), False
