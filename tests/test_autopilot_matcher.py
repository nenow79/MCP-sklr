"""Server-snapshot decisions only; no app startup, real guards or dispatch."""
from dataclasses import asdict, replace

import pytest

from autopilot.matcher import evaluate
from autopilot.models import ContractBinding
from autopilot.operations import (
    AccessSnapshot, FileAccess, FileTarget, OperationDescriptor, OperationError,
    ResourceAccess, ResourceTarget,
)
from autopilot.paths import RootRef, Scope
from autopilot.rules import BundleRef, ProfileRef, SkillRef, parse_policy

KEY = b'k' * 32
ROOT = RootRef('posix', '/work/demo', 'rw', 'a' * 32)
BUNDLE = BundleRef('docs', 'b' * 64, 'c' * 32)
SKILL = SkillRef('reader', 'central', 'd' * 64, 'e' * 64, 'f' * 32)
PROFILE = ProfileRef('a' * 32, 'b' * 64, 'c' * 32)


def context(rules):
    policy = parse_policy({'version': 1, 'rules': rules}, hmac_key=KEY)
    binding = ContractBinding('d' * 32, 'e' * 32, 1, 1, policy.digest)
    return policy, binding


def files_rule(scope='src', *, rule_id='files', overwrite=False, root=ROOT):
    return {'id': rule_id, 'kind': 'files', 'tools': ['read_file', 'edit_file', 'create_file',
            'create_tree', 'manage_folder', 'search_code', 'read_files_batch', 'list_directory'],
            'root': asdict(root), 'scope': {'kind': 'tree', 'path': scope}, 'overwrite': overwrite}


def resource_access(ref, **changes):
    return ResourceAccess(ref, checked=True, available=True, **changes)


def file_snapshot(binding, targets, **changes):
    roots = tuple(dict.fromkeys(target.root for target in targets))
    paths = tuple(dict.fromkeys((target.root, target.path) for target in targets))
    return AccessSnapshot(binding, resources=tuple(resource_access(root) for root in roots),
                          files=tuple(FileAccess(root, path, 'rw', checked=True, available=True, **changes)
                                      for root, path in paths))


def check(policy, binding, op, snapshot, code='allowed', ids=None):
    decision = evaluate(policy, op, snapshot, binding)
    assert decision.code == code
    assert decision.allowed is (code == 'allowed')
    if code != 'allowed':
        assert decision.matched_rule_ids == ()
    elif ids is not None:
        assert decision.matched_rule_ids == ids
    return decision


def test_all_batch_locations_are_required_and_partial_match_is_not_returned():
    policy, binding = context([files_rule()])
    targets = (FileTarget(ROOT, '/work/demo/src/a.py'), FileTarget(ROOT, '/work/demo/src2/b.py'))
    op = OperationDescriptor(binding, 'read_files_batch', 'read', files=targets)
    check(policy, binding, op, file_snapshot(binding, targets), 'scope_mismatch')
    op = replace(op, files=targets[:1])
    check(policy, binding, op, file_snapshot(binding, targets[:1]), ids=('files',))


def test_batch_can_use_multiple_complete_rules_and_no_hidden_parent_creation():
    policy, binding = context([files_rule('src/new', rule_id='src'), files_rule('docs', rule_id='docs')])
    targets = (FileTarget(ROOT, '/work/demo/src/new/a.py'), FileTarget(ROOT, '/work/demo/docs/a.md'))
    op = OperationDescriptor(binding, 'create_tree', 'create_tree', files=targets)
    check(policy, binding, op, file_snapshot(binding, targets), ids=('docs', 'src'))
    parent = FileTarget(ROOT, '/work/demo/src', directory=True)
    check(policy, binding, replace(op, files=(*targets, parent)),
          file_snapshot(binding, (*targets, parent)), 'scope_mismatch')


def test_additional_binding_for_same_physical_file_cannot_be_ignored():
    policy, binding = context([files_rule()])
    narrower = replace(ROOT, path='/work/demo/src', epoch='b' * 32)
    targets = (FileTarget(ROOT, '/work/demo/src/a.py'), FileTarget(narrower, '/work/demo/src/a.py'))
    op = OperationDescriptor(binding, 'edit_file', 'edit', files=targets)
    check(policy, binding, op, file_snapshot(binding, targets), 'scope_mismatch')


@pytest.mark.parametrize('flag,code', [('sensitive', 'sensitive_operation'), ('restricted', 'manual_required')])
def test_file_server_classification_has_priority_over_scope(flag, code):
    policy, binding = context([files_rule()])
    target = FileTarget(ROOT, '/work/demo/src/a.py')
    op = OperationDescriptor(binding, 'edit_file', 'edit', files=(target,))
    check(policy, binding, op, file_snapshot(binding, (target,), **{flag: True}), code)


@pytest.mark.parametrize('path', ['AGENTS.md', 'src/SKILL.md', '.git/config', '.env', 'autopilot-contract.md'])
def test_protected_writes_do_not_depend_on_a_permissive_sensitive_flag(path):
    policy, binding = context([files_rule('.')])
    target = FileTarget(ROOT, '/work/demo/' + path)
    op = OperationDescriptor(binding, 'create_file', 'create', files=(target,))
    check(policy, binding, op, file_snapshot(binding, (target,)), 'sensitive_operation')
    check(policy, binding, replace(op, tool='read_file', action='read'), file_snapshot(binding, (target,)))


def test_nested_read_only_owner_and_unchecked_context_cannot_authorize_write():
    policy, binding = context([files_rule()])
    target = FileTarget(ROOT, '/work/demo/src/a.py')
    op = OperationDescriptor(binding, 'edit_file', 'edit', files=(target,))
    snapshot = file_snapshot(binding, (target,))
    for row in (replace(snapshot.files[0], owner_mode='ro'), replace(snapshot.files[0], checked=False),
                replace(snapshot.files[0], available=False)):
        check(policy, binding, op, replace(snapshot, files=(row,)), 'access_denied')
    check(policy, binding, op, replace(snapshot, resources=()), 'resource_changed')


def test_full_overwrite_is_separate_from_exact_edits_and_file_scans():
    rule = files_rule()
    rule['scope'] = {'kind': 'file', 'path': 'src/a.py'}
    policy, binding = context([rule])
    target = FileTarget(ROOT, '/work/demo/src/a.py')
    snapshot = file_snapshot(binding, (target,))
    op = OperationDescriptor(binding, 'edit_file', 'edit', files=(target,))
    check(policy, binding, op, snapshot)
    check(policy, binding, replace(op, tool='create_file', action='create', overwrite=True), snapshot, 'scope_mismatch')
    directory = FileTarget(ROOT, '/work/demo/src', directory=True, recursive=True)
    check(policy, binding, OperationDescriptor(binding, 'search_code', 'search', files=(directory,)),
          file_snapshot(binding, (directory,)), 'scope_mismatch')


def test_explicit_overwrite_rule_and_create_folder_success():
    policy, binding = context([files_rule(overwrite=True)])
    target = FileTarget(ROOT, '/work/demo/src/a.py')
    op = OperationDescriptor(binding, 'create_file', 'create', files=(target,), overwrite=True)
    check(policy, binding, op, file_snapshot(binding, (target,)))
    folder = FileTarget(ROOT, '/work/demo/src/new', directory=True)
    op = OperationDescriptor(binding, 'manage_folder', 'create', files=(folder,))
    check(policy, binding, op, file_snapshot(binding, (folder,)))


def test_narrow_root_inside_git_does_not_bypass_protected_write():
    git_root = replace(ROOT, path='/work/demo/.git')
    policy, binding = context([files_rule('.', root=git_root)])
    target = FileTarget(git_root, '/work/demo/.git/config')
    op = OperationDescriptor(binding, 'edit_file', 'edit', files=(target,))
    check(policy, binding, op, file_snapshot(binding, (target,)), 'sensitive_operation')


@pytest.mark.parametrize('field,value', [('epoch', 'b' * 32), ('mode', 'ro'), ('path', '/work/other')])
def test_changed_root_never_reuses_previous_delegation(field, value):
    policy, binding = context([files_rule()])
    changed = replace(ROOT, **{field: value})
    target = FileTarget(changed, changed.path + '/src/a.py')
    op = OperationDescriptor(binding, 'read_file', 'read', files=(target,))
    expected = 'scope_mismatch' if field == 'path' else 'resource_changed'
    check(policy, binding, op, file_snapshot(binding, (target,)), expected)


@pytest.mark.parametrize('field,value', [('session_id', 'f' * 32), ('run_revision', 2),
                                      ('contract_revision', 2), ('contract_id', 'f' * 32)])
def test_every_server_binding_is_checked(field, value):
    policy, binding = context([files_rule()])
    target = FileTarget(ROOT, '/work/demo/src/a.py')
    op = OperationDescriptor(binding, 'read_file', 'read', files=(target,))
    snapshot = file_snapshot(binding, (target,))
    foreign = replace(binding, **{field: value})
    check(policy, foreign, op, snapshot, 'binding_mismatch')
    check(policy, binding, op, replace(snapshot, binding=foreign), 'binding_mismatch')


def test_policy_digest_mismatch_unknown_tools_and_incomplete_operations():
    policy, binding = context([files_rule()])
    target = FileTarget(ROOT, '/work/demo/src/a.py')
    op = OperationDescriptor(binding, 'read_file', 'read', files=(target,))
    snapshot = file_snapshot(binding, (target,))
    other, _ = context([files_rule('other')])
    check(other, binding, op, snapshot, 'policy_mismatch')
    check(policy, binding, replace(op, tool='new_unknown'), snapshot, 'tool_not_allowed')
    check(policy, binding, replace(op, files=()), snapshot, 'invalid_operation')
    check(policy, binding, replace(op, args=('smuggled',)), snapshot, 'invalid_operation')
    check(policy, binding, replace(op, tool='manage_folder', action='rename'), snapshot, 'manual_required')
    for tool in ('secret_reveal', 'request_root', 'prepare_project', 'knowledge_save'):
        check(policy, binding, replace(op, tool=tool), snapshot, 'manual_required')


def test_knowledge_source_and_whole_search_scope_are_pinned():
    rule = {'id': 'knowledge', 'kind': 'knowledge', 'tools': ['knowledge_search', 'knowledge_get'],
            'bundle': asdict(BUNDLE), 'scope': {'kind': 'tree', 'path': 'guide'}}
    policy, binding = context([rule])
    target = ResourceTarget(BUNDLE, Scope('file', 'guide/start.md'))
    op = OperationDescriptor(binding, 'knowledge_get', 'read', targets=(target,))
    snapshot = AccessSnapshot(binding, resources=(resource_access(BUNDLE),))
    check(policy, binding, op, snapshot)
    search = replace(op, tool='knowledge_search', action='search',
                     targets=(ResourceTarget(BUNDLE, Scope('tree', '.')),))
    check(policy, binding, search, snapshot, 'scope_mismatch')
    changed = replace(BUNDLE, source_digest='d' * 64)
    check(policy, binding, replace(op, targets=(ResourceTarget(changed, target.scope),)),
          AccessSnapshot(binding, resources=(resource_access(changed),)), 'resource_changed')


def test_skill_run_requires_actual_trust_exact_arguments_and_execution_identity():
    rule = {'id': 'skill', 'kind': 'skill_run', 'tools': ['run_skill_script'], 'skill': asdict(SKILL),
            'script': 'scripts/read.py', 'args': ['--limit', '5'], 'max_timeout_s': 250}
    policy, binding = context([rule])
    op = OperationDescriptor(binding, 'run_skill_script', 'run', resource=SKILL,
                             script='scripts/read.py', args=('--limit', '5'), timeout_s=240)
    row = resource_access(SKILL, trusted=True, approval_required=False)
    snapshot = AccessSnapshot(binding, resources=(row,))
    check(policy, binding, op, snapshot)
    check(policy, binding, replace(op, args=('--limit', '50')), snapshot, 'arguments_mismatch')
    check(policy, binding, replace(op, timeout_s=251), snapshot, 'timeout_exceeded')
    for change in ({'trusted': False}, {'approval_required': True}):
        check(policy, binding, op, replace(snapshot, resources=(replace(row, **change),)), 'trust_required')
    changed = replace(SKILL, execution_digest='a' * 64)
    check(policy, binding, replace(op, resource=changed),
          AccessSnapshot(binding, resources=(resource_access(changed, trusted=True, approval_required=False),)),
          'resource_changed')


def test_skill_resources_and_activation_do_not_require_execution_trust():
    rule = {'id': 'references', 'kind': 'skill_read', 'tools': ['activate_skill', 'read_skill_resource'],
            'skill': asdict(SKILL), 'scope': {'kind': 'tree', 'path': '.'}}
    policy, binding = context([rule])
    snapshot = AccessSnapshot(binding, resources=(resource_access(SKILL),))
    op = OperationDescriptor(binding, 'activate_skill', 'activate',
                             targets=(ResourceTarget(SKILL, Scope('file', 'SKILL.md')),))
    check(policy, binding, op, snapshot)
    resource = ResourceTarget(SKILL, Scope('file', 'references/guide.md'))
    check(policy, binding, replace(op, tool='read_skill_resource', action='read', targets=(resource,)), snapshot)
    check(policy, binding, replace(op, targets=(resource,)), snapshot, 'invalid_operation')


def mcp_context():
    arguments = {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 5}},
                 'required': ['limit']}
    rule = {'id': 'mcp', 'kind': 'mcp_call', 'tools': ['call_external_mcp_tool'], 'profile': asdict(PROFILE),
            'tool_name': 'find', 'schema_digest': 'd' * 64, 'arguments': arguments, 'max_timeout_s': 300}
    policy, binding = context([rule])
    op = OperationDescriptor(binding, 'call_external_mcp_tool', 'call', resource=PROFILE,
                             tool_name='find', schema_digest='d' * 64, arguments={'limit': 3}, timeout_s=300)
    row = resource_access(PROFILE, enabled=True, trusted=True, trusted_tools=(('find', 'd' * 64),))
    return policy, binding, op, AccessSnapshot(binding, resources=(row,))


def test_mcp_requires_named_schema_trust_and_closed_arguments():
    policy, binding, op, snapshot = mcp_context()
    check(policy, binding, op, snapshot)
    for arguments in ({'limit': True}, {'limit': 6}, {'limit': 3, 'extra': 'PRIVATE'}):
        check(policy, binding, replace(op, arguments=arguments), snapshot, 'arguments_mismatch')
    for changes, code in [({'enabled': False}, 'access_denied'), ({'trusted': False}, 'trust_required'),
                          ({'restricted': True}, 'manual_required'),
                          ({'trusted_tools': (('other', 'd' * 64),)}, 'trust_required')]:
        row = replace(snapshot.resources[0], **changes)
        check(policy, binding, op, replace(snapshot, resources=(row,)), code)
    check(policy, binding, replace(op, schema_digest='e' * 64), snapshot, 'trust_required')
    assert 'limit' not in repr(op) and 'PRIVATE' not in repr(snapshot)


def test_mcp_discovery_does_not_need_tool_trust_but_does_need_enabled_profile():
    rule = {'id': 'catalog', 'kind': 'mcp_discovery', 'tools': ['list_external_mcp_tools'],
            'profile': asdict(PROFILE), 'max_timeout_s': 300}
    policy, binding = context([rule])
    op = OperationDescriptor(binding, 'list_external_mcp_tools', 'discover', resource=PROFILE, timeout_s=250)
    row = resource_access(PROFILE, enabled=True)
    check(policy, binding, op, AccessSnapshot(binding, resources=(row,)))
    check(policy, binding, op, AccessSnapshot(binding, resources=(replace(row, enabled=False),)), 'access_denied')


def test_catalog_requires_every_returned_resource_and_no_ambiguous_snapshot():
    rule = {'id': 'roots', 'kind': 'catalog', 'tools': ['list_roots'], 'resources': [asdict(ROOT)]}
    policy, binding = context([rule])
    other = replace(ROOT, path='/work/other', epoch='b' * 32)
    op = OperationDescriptor(binding, 'list_roots', 'list', targets=(ResourceTarget(ROOT),))
    snapshot = AccessSnapshot(binding, resources=(resource_access(ROOT), resource_access(other)))
    check(policy, binding, op, snapshot)
    check(policy, binding, replace(op, targets=(*op.targets, ResourceTarget(other))), snapshot, 'scope_mismatch')
    with pytest.raises(OperationError, match='^ambiguous_access$'):
        AccessSnapshot(binding, resources=(resource_access(ROOT), resource_access(replace(ROOT, epoch='c' * 32))))


@pytest.mark.parametrize('ref,tool,field,value', [
    (ROOT, 'list_roots', 'epoch', 'b' * 32),
    (BUNDLE, 'knowledge_bundles', 'source_digest', 'd' * 64),
    (BUNDLE, 'knowledge_bundles', 'epoch', 'd' * 32),
    (SKILL, 'list_skills', 'skill_hash', 'a' * 64),
    (SKILL, 'list_skills', 'execution_digest', 'a' * 64),
    (SKILL, 'list_skills', 'epoch', 'a' * 32),
    (PROFILE, 'list_external_mcp', 'connection_digest', 'd' * 64),
    (PROFILE, 'list_external_mcp', 'epoch', 'd' * 32),
])
def test_resource_fingerprints_and_generations_are_not_replaced_by_catalog_refresh(ref, tool, field, value):
    rule = {'id': 'catalog', 'kind': 'catalog', 'tools': [tool], 'resources': [asdict(ref)]}
    policy, binding = context([rule])
    changed = replace(ref, **{field: value})
    op = OperationDescriptor(binding, tool, 'list', targets=(ResourceTarget(changed),))
    check(policy, binding, op, AccessSnapshot(binding, resources=(resource_access(changed),)), 'resource_changed')


def test_operation_json_is_copied_and_snapshot_defaults_deny():
    policy, binding, op, snapshot = mcp_context()
    raw = {'limit': 3}
    copied = replace(op, arguments=raw)
    raw['limit'] = 999
    check(policy, binding, copied, snapshot)
    check(policy, binding, op, AccessSnapshot(binding, resources=(ResourceAccess(PROFILE),)), 'access_denied')
    assert evaluate(None, op, snapshot, binding).code == 'invalid_operation'
    with pytest.raises(ValueError):
        OperationDescriptor(binding, 'call_external_mcp_tool', 'call', args=['not-a-tuple'])
