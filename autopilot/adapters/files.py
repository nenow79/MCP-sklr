"""Canonical file plans built through the existing guards and write planners."""
import os
from pathlib import Path

from autopilot.operations import AccessSnapshot, FileAccess, FileTarget, OperationDescriptor, ResourceAccess
from autopilot.paths import RootRef
from server.file_changes import SensitivePolicy, prepare_edit, prepare_write
from server.path_guard import resolve_path

WRITES = frozenset({'edit_file', 'create_file', 'create_tree', 'manage_folder'})


class FileAdapter:
    def __init__(self, roots, generations):
        self.roots, self.generations = roots, generations
        self.policy = SensitivePolicy()

    def root_ref(self, root):
        path = Path(root.path).resolve()
        available = path.is_dir() and os.access(path, os.R_OK | os.X_OK)
        info = path.stat() if available else None
        fingerprint = self.generations.digest([path.as_posix(), root.mode,
                                               (info.st_dev, info.st_ino) if info else None])
        epoch = self.generations.observe(('root', path.as_posix()), fingerprint, available=available)
        return RootRef('windows' if os.name == 'nt' else 'posix', path.as_posix(), root.mode, epoch), available

    def build(self, binding, tool, args):
        # Observe disappearance before registry.resolve can reject a missing root.
        # A later restoration must not reuse an earlier generation.
        for root in self.roots.list():
            self.root_ref(root)
        files, access, resources = [], {}, {}
        write = tool in WRITES

        def target(raw, root, *, directory=False, recursive=False):
            resolved = resolve_path(self.roots, raw, root, write=write, allow_empty=directory)
            ref, available = self.root_ref(resolved.root)
            canonical = resolved.path.as_posix()
            item = FileTarget(ref, canonical, directory, recursive)
            if item not in files:
                files.append(item)
            sensitive = bool(self.policy.reasons(resolved))
            # The entire running server and its own authority/data are excluded,
            # including newly added modules unknown to the older policy.
            if write and (resolved.path == self.policy.server_dir or self.policy.server_dir in resolved.path.parents):
                sensitive = True
            resources[ref] = ResourceAccess(ref, checked=True, available=available)
            access[(ref, canonical)] = FileAccess(ref, canonical, resolved.owner.mode,
                                                checked=True, available=available, sensitive=sensitive)

        root = args.get('root')
        noop = False
        if tool in {'read_file', 'create_file'}:
            target(args['filepath'], root)
        elif tool == 'read_files_batch':
            requests = args['requests']
            if type(requests) is not list or not 1 <= len(requests) <= 10:
                raise ValueError('invalid_operation')
            for request in requests:
                target(request['filepath'], request.get('root'))
        elif tool == 'list_directory':
            target(args.get('subpath', ''), root, directory=True, recursive=True)
        elif tool == 'search_code':
            selected = self.roots.list() if root == '*' else [self.roots.resolve(root)]
            for selected_root in selected:
                path = args.get('subpath', '')
                resolved = resolve_path(self.roots, path, selected_root.alias, allow_empty=True)
                target(path, selected_root.alias, directory=resolved.path.is_dir(), recursive=resolved.path.is_dir())
        elif tool == 'edit_file':
            plan = prepare_edit(self.roots, args['changes'], root, policy=self.policy)
            for item in plan.files:
                for association in item.bindings:
                    target(association.filepath, association.root.alias)
            noop = not any(item.changed for item in plan.files)
        elif tool == 'create_tree':
            from server.tree_tools import prepare_tree
            plan = prepare_tree(self.roots, args.get('directories') or [], args.get('files') or [], root, self.policy)
            for path, alias, owner in plan.directories:
                target(path.relative_to(Path(self.roots.resolve(alias).path)).as_posix(), alias, directory=True)
            for item in plan.changes.files:
                for association in item.bindings:
                    target(association.filepath, association.root.alias)
            # Requested aliases are checked even when several refer to the same target.
            for raw, bound_root, owner, path in plan.bindings:
                target(raw, bound_root.alias, directory=path not in {f.path for f in plan.changes.files})
        elif tool == 'manage_folder':
            from server.folder_tools import _build_create
            if args['action'] != 'create':
                return OperationDescriptor(binding, tool, args['action']), AccessSnapshot(binding), False
            plan = _build_create(self.roots, args['path'], root, self.policy)
            target(args['path'], root, directory=True)
        else:
            raise ValueError('invalid_operation')
        if tool == 'create_file':
            plan = prepare_write(self.roots, args['filepath'], args['content'], args.get('overwrite', False),
                                 root, policy=self.policy)
            noop = not any(item.changed for item in plan.files)
        actions = {'read_file': 'read', 'read_files_batch': 'read', 'list_directory': 'list',
                   'search_code': 'search', 'edit_file': 'edit', 'create_file': 'create',
                   'create_tree': 'create_tree', 'manage_folder': 'create'}
        operation = OperationDescriptor(binding, tool, actions[tool], files=tuple(files),
                                        overwrite=args.get('overwrite', False))
        return operation, AccessSnapshot(binding, tuple(resources.values()), tuple(access.values())), noop
