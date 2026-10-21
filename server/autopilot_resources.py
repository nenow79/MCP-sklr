"""Panel resource choices and server-owned pinning; no discovery or execution."""
from dataclasses import asdict

from autopilot.rules import CATALOG_TYPES
from autopilot.adapters.restrictions import restricted_name


def resource_choices(runtime):
    """Caller holds config lock. Return references privately, labels publicly."""
    refs, choices = {}, []

    def add(kind, name, ref, **details):
        key = kind + ':' + name
        refs[key] = ref
        choices.append(dict(key=key, kind=kind, name=name, **details))

    for root in runtime.files.roots.list():
        ref, available = runtime.files.root_ref(root)
        if available:
            add('root', root.alias, ref, path=ref.path, mode=ref.mode)
    service = runtime.knowledge.service
    if service is not None:
        with service._lock:
            service._sync()
            for key in sorted(service._states):
                state = service._current(key)
                if state.available:
                    add('bundle', state.id, runtime.knowledge.reference(state))
    registry = runtime.skills.registry
    if registry is not None:
        registry.rescan()
        for record in registry.list_active():
            if record.source != 'central':
                continue
            try:
                ref, access = runtime.skills.reference(record)
            except (ValueError, OSError):
                continue
            add('skill', record.name, ref, trusted=access.trusted,
                approval_required=access.approval_required)
    tools = runtime.mcp.tools
    if tools is not None:
        listing = tools.registry.list()
        if not listing['configuration_valid']:
            raise ValueError('resources_unavailable')
        for profile in listing['profiles']:
            if not profile['enabled']:
                continue
            entries = []
            with tools.lock:
                catalog = tools.catalogs.get(profile['id'])
                if (catalog is not None and catalog['expires'] > tools.clock()
                        and catalog['revision'] == listing['revision']):
                    entries = [dict(name=row['name'], schema_digest=row['schemaSha256'])
                               for row in catalog['tools']]
            add('profile', profile['alias'], runtime.mcp.reference(profile), tools=entries)
    return refs, choices


def pin_rules(document, refs, choices, runtime):
    """Replace user choices with current server references, never client epochs."""
    if type(document) is not dict or set(document) != {'version', 'rules'}:
        raise ValueError('invalid_policy')
    if type(document['rules']) is not list or len(document['rules']) > 128:
        raise ValueError('invalid_policy')
    rules = []
    for raw in document['rules']:
        if type(raw) is not dict:
            raise ValueError('invalid_rule')
        rule = dict(raw)
        kind = rule.get('kind')
        field = {'files': 'root', 'knowledge': 'bundle', 'skill_read': 'skill',
                 'skill_run': 'skill', 'mcp_discovery': 'profile', 'mcp_call': 'profile'}.get(kind)
        if field:
            key = rule.get(field)
            if type(key) is not str or not key.startswith(field + ':') or key not in refs:
                raise ValueError('resource_changed')
            if kind == 'skill_run':
                choice = next(row for row in choices if row['key'] == key)
                if type(rule.get('script')) is not str or restricted_name(choice['name'], rule['script']):
                    raise ValueError('manual_required')
                if not choice['trusted'] or choice['approval_required']:
                    raise ValueError('trust_required')
            if kind == 'mcp_call':
                if 'schema_digest' in rule or type(rule.get('tool_name')) is not str or restricted_name(rule['tool_name']):
                    raise ValueError('invalid_rule')
                choice = next(row for row in choices if row['key'] == key)
                entry = next((row for row in choice['tools'] if row['name'] == rule.get('tool_name')), None)
                if entry is None:
                    raise ValueError('catalog_required')
                if not runtime.mcp.tools.registry.trust_decision(
                        choice['name'], entry['name'], entry['schema_digest'])['trusted']:
                    raise ValueError('trust_required')
                rule['schema_digest'] = entry['schema_digest']
            rule[field] = asdict(refs[key])
        elif kind == 'catalog':
            names = rule.get('resources')
            tools = rule.get('tools')
            if type(names) is not list or type(tools) is not list or len(tools) != 1:
                raise ValueError('invalid_rule')
            expected = CATALOG_TYPES.get(tools[0]) if type(tools[0]) is str else None
            if expected is None or any(type(key) is not str or key not in refs
                                       or type(refs[key]) is not expected for key in names):
                raise ValueError('resource_changed')
            rule['resources'] = [asdict(refs[key]) for key in names]
        rules.append(rule)
    return {'version': document['version'], 'rules': rules}
