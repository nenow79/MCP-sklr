"""Closed policy parsing and keyed fingerprints. No key storage or effects."""
import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from autopilot.arguments import freeze_json, parse_selector, thaw_json
from autopilot.paths import RootRef, Scope, closed, hex_value, relative_path, text


class PolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class BundleRef:
    bundle_id: str
    source_digest: str
    epoch: str

    def __post_init__(self):
        text(self.bundle_id)
        hex_value(self.source_digest, 64)
        hex_value(self.epoch, 32)


@dataclass(frozen=True, slots=True, repr=False)
class SkillRef:
    name: str
    source: str
    skill_hash: str
    execution_digest: str
    epoch: str

    def __post_init__(self):
        text(self.name)
        if type(self.source) is not str or self.source not in ('central', 'project', 'dev'):
            raise PolicyError('invalid_resource')
        hex_value(self.skill_hash, 64)
        hex_value(self.execution_digest, 64)
        hex_value(self.epoch, 32)


@dataclass(frozen=True, slots=True, repr=False)
class ProfileRef:
    profile_id: str
    connection_digest: str
    epoch: str

    def __post_init__(self):
        hex_value(self.profile_id, 32)
        hex_value(self.connection_digest, 64)
        hex_value(self.epoch, 32)


RESOURCE_TYPES = (RootRef, BundleRef, SkillRef, ProfileRef)
TOOLS = {
    'files': frozenset({'list_directory', 'read_file', 'read_files_batch', 'search_code',
                        'edit_file', 'create_file', 'create_tree', 'manage_folder'}),
    'knowledge': frozenset({'knowledge_search', 'knowledge_get'}),
    'skill_read': frozenset({'activate_skill', 'read_skill_resource'}),
    'skill_run': frozenset({'run_skill_script'}),
    'mcp_discovery': frozenset({'list_external_mcp_tools'}),
    'mcp_call': frozenset({'call_external_mcp_tool'}),
    'catalog': frozenset({'list_roots', 'knowledge_bundles', 'list_skills', 'list_external_mcp'}),
}
CATALOG_TYPES = {'list_roots': RootRef, 'knowledge_bundles': BundleRef,
                 'list_skills': SkillRef, 'list_external_mcp': ProfileRef}


def resource_key(resource):
    if type(resource) is RootRef:
        return ('root', resource.platform, resource.path)
    if type(resource) is BundleRef:
        return ('bundle', resource.bundle_id)
    if type(resource) is SkillRef:
        return ('skill', resource.name, resource.source)
    if type(resource) is ProfileRef:
        return ('profile', resource.profile_id)
    raise PolicyError('invalid_resource')


def _reference(model, raw):
    if model is RootRef:
        return RootRef.from_dict(raw)
    fields = {
        BundleRef: ('bundle_id', 'source_digest', 'epoch'),
        SkillRef: ('name', 'source', 'skill_hash', 'execution_digest', 'epoch'),
        ProfileRef: ('profile_id', 'connection_digest', 'epoch'),
    }[model]
    closed(raw, fields)
    return model(**raw)


@dataclass(frozen=True, slots=True, repr=False)
class Rule:
    id: str
    kind: str
    tools: tuple
    resource: object = None
    scope: object = None
    overwrite: bool = False
    script: str = ''
    args: tuple = ()
    max_timeout_s: int = 0
    tool_name: str = ''
    schema_digest: str = ''
    arguments: object = None
    resources: tuple = ()


@dataclass(frozen=True, slots=True, repr=False)
class Policy:
    rules: tuple
    digest: str


def _parse_rule(raw):
    if type(raw) is not dict or type(raw.get('kind')) is not str or raw['kind'] not in TOOLS:
        raise PolicyError('invalid_rule')
    kind = raw['kind']
    extras = {
        'files': ('root', 'scope', 'overwrite'),
        'knowledge': ('bundle', 'scope'),
        'skill_read': ('skill', 'scope'),
        'skill_run': ('skill', 'script', 'args', 'max_timeout_s'),
        'mcp_discovery': ('profile', 'max_timeout_s'),
        'mcp_call': ('profile', 'tool_name', 'schema_digest', 'arguments', 'max_timeout_s'),
        'catalog': ('resources',),
    }[kind]
    closed(raw, ('id', 'kind', 'tools', *extras))
    if type(raw['id']) is not str or re.fullmatch(r'[a-z0-9_-]{1,64}', raw['id']) is None:
        raise PolicyError('invalid_rule')
    tools = raw['tools']
    if (type(tools) is not list or not tools or any(type(tool) is not str for tool in tools)
            or len(set(tools)) != len(tools) or set(tools) - TOOLS[kind]):
        raise PolicyError('invalid_tools')
    values = {'id': raw['id'], 'kind': kind, 'tools': tuple(tools)}
    if kind == 'catalog':
        if len(tools) != 1 or type(raw['resources']) is not list or not raw['resources']:
            raise PolicyError('invalid_rule')
        resources = tuple(_reference(CATALOG_TYPES[tools[0]], item) for item in raw['resources'])
        if len({resource_key(item) for item in resources}) != len(resources):
            raise PolicyError('invalid_resource')
        values['resources'] = resources
    else:
        model, key = {'files': (RootRef, 'root'), 'knowledge': (BundleRef, 'bundle'),
                      'skill_read': (SkillRef, 'skill'), 'skill_run': (SkillRef, 'skill'),
                      'mcp_discovery': (ProfileRef, 'profile'),
                      'mcp_call': (ProfileRef, 'profile')}[kind]
        values['resource'] = _reference(model, raw[key])
    if kind in ('skill_read', 'skill_run') and values['resource'].source != 'central':
        raise PolicyError('invalid_resource')
    if 'scope' in raw:
        values['scope'] = Scope.from_dict(raw['scope'])
        if kind == 'knowledge' and values['scope'].kind == 'file' and not values['scope'].path.endswith('.md'):
            raise PolicyError('invalid_scope')
    if kind == 'files':
        if type(raw['overwrite']) is not bool:
            raise PolicyError('invalid_rule')
        values['overwrite'] = raw['overwrite']
    if 'max_timeout_s' in raw:
        timeout = raw['max_timeout_s']
        if type(timeout) is not int or not 1 <= timeout <= 300:
            raise PolicyError('invalid_timeout')
        values['max_timeout_s'] = timeout
    if kind == 'skill_run':
        script = relative_path(raw['script'])
        args = raw['args']
        if (not script.startswith('scripts/') or type(args) is not list or len(args) > 100
                or any(type(arg) is not str or '\x00' in arg for arg in args)):
            raise PolicyError('invalid_skill_rule')
        values.update(script=script, args=tuple(args))
    if kind == 'mcp_call':
        values.update(tool_name=text(raw['tool_name']), schema_digest=hex_value(raw['schema_digest'], 64),
                      arguments=parse_selector(raw['arguments']))
    return Rule(**values)


def parse_policy(document, *, hmac_key):
    """Return a detached policy. No key is retained; input is never authorized."""
    if type(hmac_key) is not bytes or not 32 <= len(hmac_key) <= 4096:
        raise PolicyError('invalid_key')
    try:
        raw = thaw_json(freeze_json(document))
        closed(raw, ('version', 'rules'))
        if type(raw['version']) is not int or raw['version'] != 1:
            raise PolicyError('invalid_version')
        if type(raw['rules']) is not list or len(raw['rules']) > 128:
            raise PolicyError('invalid_rules')
        rules = tuple(_parse_rule(item) for item in raw['rules'])
        if len({rule.id for rule in rules}) != len(rules):
            raise PolicyError('duplicate_rule')
        pinned = {}
        for rule in rules:
            for ref in ((rule.resource,) if rule.resource is not None else rule.resources):
                key = resource_key(ref)
                if key in pinned and pinned[key] != ref:
                    raise PolicyError('inconsistent_resource')
                pinned[key] = ref
        encoded = json.dumps(raw, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False).encode('utf-8')
        return Policy(rules, hmac.new(hmac_key, encoded, hashlib.sha256).hexdigest())
    except PolicyError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise PolicyError('invalid_policy') from None
