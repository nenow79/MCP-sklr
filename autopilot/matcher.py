"""Deterministic scope decisions, never authorization of an actual effect."""
import hmac
from dataclasses import dataclass

from autopilot.arguments import matches
from autopilot.models import ContractBinding
from autopilot.operations import AccessSnapshot, OperationDescriptor
from autopilot.paths import contains, relative_target
from autopilot.rules import (
    BundleRef, CATALOG_TYPES, Policy, ProfileRef, SkillRef, TOOLS, resource_key,
)

_WRITES = frozenset({'edit_file', 'create_file', 'create_tree', 'manage_folder'})
_MANUAL = frozenset({'secret_reveal', 'request_root', 'prepare_project',
                      'knowledge_audit', 'knowledge_save', 'knowledge_convert'})
_ACTIONS = {
    'list_directory': 'list', 'read_file': 'read', 'read_files_batch': 'read',
    'search_code': 'search', 'edit_file': 'edit', 'create_file': 'create',
    'create_tree': 'create_tree', 'manage_folder': 'create',
    'knowledge_search': 'search', 'knowledge_get': 'read',
    'activate_skill': 'activate', 'read_skill_resource': 'read',
    'run_skill_script': 'run', 'list_external_mcp_tools': 'discover',
    'call_external_mcp_tool': 'call',
    **{tool: 'list' for tool in CATALOG_TYPES},
}
_KIND = {tool: kind for kind, tools in TOOLS.items() for tool in tools}


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    code: str
    matched_rule_ids: tuple = ()


def _deny(code):
    return Decision(False, code)


def _shape(op, kind):
    if op.action != _ACTIONS[op.tool]:
        return False
    if bool(op.files) != (kind == 'files'):
        return False
    if bool(op.targets) != (kind in ('knowledge', 'skill_read', 'catalog')):
        return False
    model = {'skill_run': SkillRef, 'mcp_discovery': ProfileRef, 'mcp_call': ProfileRef}.get(kind)
    if (model is None and op.resource is not None) or (model is not None and type(op.resource) is not model):
        return False
    if op.overwrite and op.tool != 'create_file':
        return False
    if kind != 'skill_run' and (op.script or op.args):
        return False
    if kind not in ('skill_run', 'mcp_discovery', 'mcp_call') and op.timeout_s is not None:
        return False
    if kind in ('skill_run', 'mcp_discovery', 'mcp_call') and op.timeout_s is None:
        return False
    if kind != 'mcp_call' and (op.tool_name or op.schema_digest or op.arguments.kind != 'null'):
        return False
    if kind == 'mcp_call' and (not op.tool_name or not op.schema_digest or op.arguments.kind != 'object'):
        return False
    if kind == 'skill_run' and not op.script.startswith('scripts/'):
        return False
    if kind == 'files':
        for target in op.files:
            if op.tool in ('read_file', 'read_files_batch', 'edit_file', 'create_file') and (
                    target.directory or target.recursive):
                return False
            if op.tool == 'list_directory' and not target.directory:
                return False
            if op.tool in ('create_tree', 'manage_folder') and target.recursive:
                return False
            if op.tool == 'manage_folder' and not target.directory:
                return False
    if kind in ('knowledge', 'skill_read'):
        model = BundleRef if kind == 'knowledge' else SkillRef
        if any(type(target.resource) is not model or target.scope is None for target in op.targets):
            return False
        if kind == 'skill_read' and any(target.scope.kind != 'file' for target in op.targets):
            return False
        if op.tool == 'knowledge_get' and any(target.scope.kind != 'file'
                                              or not target.scope.path.endswith('.md') for target in op.targets):
            return False
        if op.tool == 'activate_skill' and any(target.scope.kind != 'file'
                                              or target.scope.path != 'SKILL.md' for target in op.targets):
            return False
    if kind == 'catalog' and any(type(target.resource) is not CATALOG_TYPES[op.tool]
                                  or target.scope is not None for target in op.targets):
        return False
    return True


def _access(snapshot, resource):
    row = next((row for row in snapshot.resources if resource_key(row.resource) == resource_key(resource)), None)
    if row is None or row.resource != resource:
        return None, 'resource_changed'
    if not row.checked or not row.available:
        return None, 'access_denied'
    if row.restricted:
        return None, 'manual_required'
    return row, ''


def _changed(rules, resource):
    return any(rule.resource is not None and resource_key(rule.resource) == resource_key(resource)
               and rule.resource != resource for rule in rules)


def _protected_file(path):
    parts = path.casefold().split('/')
    return any(part in {'.git', '.ssh', '.aws', '.azure', '.kube', '.gnupg'} for part in parts) or parts[-1] in {
        'agents.md', 'skill.md', 'config.json', '.env', '.npmrc', 'pip.ini', 'pip.conf',
        'autopilot-contract.md', 'autopilot-rules.md',
    }


def _evaluate(policy, op, access, binding):
    if (type(policy) is not Policy or type(op) is not OperationDescriptor
            or type(access) is not AccessSnapshot or type(binding) is not ContractBinding):
        return _deny('invalid_operation')
    if op.binding != binding or access.binding != binding:
        return _deny('binding_mismatch')
    if not hmac.compare_digest(policy.digest, binding.rules_digest):
        return _deny('policy_mismatch')
    if op.tool in _MANUAL or op.tool == 'manage_folder' and op.action != 'create':
        return _deny('manual_required')
    kind = _KIND.get(op.tool)
    if kind is None:
        return _deny('tool_not_allowed')
    if not _shape(op, kind):
        return _deny('invalid_operation')
    rules = tuple(rule for rule in policy.rules if rule.kind == kind and op.tool in rule.tools)
    if not rules:
        return _deny('tool_not_allowed')
    matched = set()
    if kind == 'files':
        for target in op.files:
            _, code = _access(access, target.root)
            if code:
                return _deny(code)
            current = next((row for row in access.files if row.root == target.root and row.path == target.path), None)
            if current is None or not current.checked or not current.available:
                return _deny('access_denied')
            if current.restricted:
                return _deny('manual_required')
            path = relative_target(target.root, target.path)
            if op.tool in _WRITES:
                if target.root.mode != 'rw' or current.owner_mode != 'rw':
                    return _deny('access_denied')
                if current.sensitive or _protected_file(target.path):
                    return _deny('sensitive_operation')
            candidates = [rule for rule in rules if rule.resource == target.root
                          and (not op.overwrite or rule.overwrite)
                          and contains(rule.scope, path, directory=target.directory, recursive=target.recursive)]
            if not candidates:
                return _deny('resource_changed' if _changed(rules, target.root) else 'scope_mismatch')
            matched.add(min(rule.id for rule in candidates))
    elif kind in ('knowledge', 'skill_read', 'catalog'):
        for target in op.targets:
            _, code = _access(access, target.resource)
            if code:
                return _deny(code)
            if kind == 'catalog':
                candidates = [rule for rule in rules if target.resource in rule.resources]
                changed = any(resource_key(ref) == resource_key(target.resource) and ref != target.resource
                              for rule in rules for ref in rule.resources)
            else:
                candidates = [rule for rule in rules if rule.resource == target.resource
                              and contains(rule.scope, target.scope.path,
                                           directory=target.scope.kind == 'tree',
                                           recursive=target.scope.kind == 'tree')]
                changed = _changed(rules, target.resource)
            if not candidates:
                return _deny('resource_changed' if changed else 'scope_mismatch')
            matched.add(min(rule.id for rule in candidates))
    else:
        row, code = _access(access, op.resource)
        if code:
            return _deny(code)
        if kind == 'skill_run' and (op.resource.source != 'central' or not row.trusted or row.approval_required):
            return _deny('trust_required')
        if kind.startswith('mcp_') and not row.enabled:
            return _deny('access_denied')
        if kind == 'mcp_call' and (not row.trusted
                                  or (op.tool_name, op.schema_digest) not in row.trusted_tools):
            return _deny('trust_required')
        candidates = [rule for rule in rules if rule.resource == op.resource]
        if not candidates:
            return _deny('resource_changed' if _changed(rules, op.resource) else 'scope_mismatch')
        if kind == 'skill_run':
            candidates = [rule for rule in candidates if rule.script == op.script and rule.args == op.args]
        elif kind == 'mcp_call':
            candidates = [rule for rule in candidates if rule.tool_name == op.tool_name
                          and rule.schema_digest == op.schema_digest and matches(rule.arguments, op.arguments)]
        if not candidates:
            return _deny('arguments_mismatch')
        candidates = [rule for rule in candidates if op.timeout_s <= rule.max_timeout_s]
        if not candidates:
            return _deny('timeout_exceeded')
        matched.add(min(rule.id for rule in candidates))
    return Decision(True, 'allowed', tuple(sorted(matched)))


def evaluate(policy, operation, access, binding):
    """A server-only snapshot decision; adapter must still recheck before effects."""
    try:
        return _evaluate(policy, operation, access, binding)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError, RecursionError):
        return _deny('invalid_operation')
