"""Profile/trust/catalog snapshots; never discover a tool to construct authority."""
from autopilot.operations import AccessSnapshot, OperationDescriptor, ResourceAccess, ResourceTarget
from autopilot.rules import ProfileRef
from external_mcp.profiles import connection_digest
from autopilot.adapters.restrictions import restricted_name


class MCPAdapter:
    def __init__(self, tools, generations):
        self.tools, self.generations = tools, generations

    def reference(self, profile):
        digest = connection_digest(profile)
        fingerprint = self.generations.digest([digest, profile['enabled'], profile.get('trust'), profile['revision']])
        epoch = self.generations.observe(('profile', profile['id']), fingerprint)
        return ProfileRef(profile['id'].replace('-', ''), digest, epoch)

    def build(self, binding, tool, args):
        if self.tools is None:
            raise ValueError('access_denied')
        if tool == 'list_external_mcp':
            listing = self.tools.registry.list()
            if not listing['configuration_valid']:
                raise ValueError('access_denied')
            references = [self.reference(row) for row in listing['profiles']]
            return OperationDescriptor(binding, tool, 'list', targets=tuple(
                ResourceTarget(ref) for ref in references)), AccessSnapshot(binding, tuple(
                    ResourceAccess(ref, checked=True, available=True) for ref in references)), not references
        profile = self.tools.registry.resolve(args['alias'])['profile']
        ref = self.reference(profile)
        access = ResourceAccess(ref, checked=True, available=True, enabled=profile['enabled'])
        if tool == 'list_external_mcp_tools':
            operation = OperationDescriptor(binding, tool, 'discover', resource=ref,
                                            timeout_s=args.get('timeout', 60))
        else:
            if restricted_name(args['tool_name']):
                raise ValueError('manual_required')
            with self.tools.lock:
                catalog = self.tools.catalogs.get(profile['id'])
                if catalog is None or catalog['expires'] <= self.tools.clock():
                    raise ValueError('resource_changed')
                entry = next((row for row in catalog['tools'] if row['name'] == args['tool_name']), None)
                if entry is None:
                    raise ValueError('resource_changed')
                digest = entry['schemaSha256']
            decision = self.tools.registry.trust_decision(args['alias'], args['tool_name'], digest)
            access = ResourceAccess(ref, checked=True, available=True, enabled=True,
                                    trusted=decision['trusted'], trusted_tools=((args['tool_name'], digest),))
            operation = OperationDescriptor(binding, tool, 'call', resource=ref, tool_name=args['tool_name'],
                schema_digest=digest, arguments=args['arguments'], timeout_s=args.get('timeout', 60))
        return operation, AccessSnapshot(binding, (access,)), False
