"""Thread-safe persistent profiles. All writes use ConfigStore.update; no runtime clients."""
import copy
import hashlib
import hmac
import json
from datetime import datetime, timezone
from uuid import uuid4

from external_mcp.profiles import (
    MAX_PROFILES, ProfileConflict, ProfileError, ProfileNotFound,
    connection_digest, identifier, validate_profile, validate_tools_map,
)
from external_mcp.trust import trusted_decision


class ExternalMCPRegistry:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _ports(config):
        ports = []
        for section, default in (('server', 8101), ('panel', 8102)):
            item = config.get(section, {})
            port = item.get('port', default) if isinstance(item, dict) else default
            if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535:
                ports.append(port)
            else:
                ports.append(default)
        return tuple(ports)

    def _section(self, config):
        raw = config.get('external_mcp', {})
        if isinstance(raw, dict) and not raw:
            return {'version': 1, 'generation': None, 'profiles': []}
        if not isinstance(raw, dict) or set(raw) != {'version', 'generation', 'profiles'}:
            raise ProfileError('section_format')
        if type(raw['version']) is not int or raw['version'] != 1:
            raise ProfileError('section_version')
        identifier(raw['generation'], 'generation')
        rows = raw['profiles']
        if not isinstance(rows, list) or len(rows) > MAX_PROFILES:
            raise ProfileError('profiles_limit')
        aliases, ids, parsed = set(), set(), []
        for row in rows:
            profile = validate_profile(row, stored=True, blocked_ports=self._ports(config))
            if profile['alias'] in aliases or profile['id'] in ids:
                raise ProfileError('duplicate_profile')
            aliases.add(profile['alias'])
            ids.add(profile['id'])
            parsed.append(profile)
        return {'version': 1, 'generation': raw['generation'], 'profiles': parsed}

    def _revision(self, config):
        # generation prevents ABA after undo/delete/recreate; hash detects out-of-band edits.
        encoded = json.dumps([config.get('external_mcp', {}), self._ports(config)],
                             sort_keys=True, ensure_ascii=True).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    def _check(self, config, expected_revision):
        if (not isinstance(expected_revision, str) or
                not hmac.compare_digest(expected_revision.encode('utf-8'),
                                        self._revision(config).encode('ascii'))):
            raise ProfileConflict('revision_conflict')

    def list(self):
        """Validated, detached profiles for administration; never resolves env reference values."""
        with self.store.lock:
            config = self.store.snapshot()
            try:
                section = self._section(config)
            except ProfileError as exc:
                return {'profiles': [], 'count': 0, 'configuration_valid': False,
                        'error': exc.code, 'revision': None}
            return {'profiles': section['profiles'], 'count': len(section['profiles']),
                    'configuration_valid': True, 'error': '', 'revision': self._revision(config)}

    def resolve(self, alias, *, expected_revision=None, require_enabled=True):
        """Internal operation snapshot. Later adapters must re-check it immediately before execution."""
        with self.store.lock:
            config = self.store.snapshot()
            section = self._section(config)
            if expected_revision is not None:
                self._check(config, expected_revision)
            if not isinstance(alias, str):
                raise ProfileError('alias')
            profile = next((row for row in section['profiles'] if row['alias'] == alias), None)
            if profile is None:
                raise ProfileNotFound('profile_not_found')
            if require_enabled and not profile['enabled']:
                raise ProfileError('profile_disabled')
            return {'profile': copy.deepcopy(profile), 'revision': self._revision(config)}

    @staticmethod
    def _find(section, profile_id):
        if not isinstance(profile_id, str):
            raise ProfileNotFound('profile_not_found')
        for index, row in enumerate(section['profiles']):
            if row['id'] == profile_id:
                return index
        raise ProfileNotFound('profile_not_found')

    def _change(self, expected_revision, change):
        def mutate(config):
            section = self._section(config)
            self._check(config, expected_revision)
            change(section, config)
            section['generation'] = str(uuid4())
            config['external_mcp'] = section
            # Revalidate uniqueness/limits before the shared store commits.
            self._section(config)
        with self.store.lock:
            self.store.update(mutate)
            return self.list()

    def add(self, proposal, expected_revision):
        proposal = copy.deepcopy(proposal)
        def change(section, config):
            row = validate_profile(proposal, blocked_ports=self._ports(config))
            row.update(id=str(uuid4()), revision=str(uuid4()))
            section['profiles'].append(row)
        return self._change(expected_revision, change)

    def update(self, profile_id, proposal, expected_revision):
        """Replace user-editable fields; stable id survives alias/transport changes."""
        proposal = copy.deepcopy(proposal)
        def change(section, config):
            index = self._find(section, profile_id)
            old = section['profiles'][index]
            row = validate_profile(proposal, blocked_ports=self._ports(config))
            row.update(id=old['id'], revision=str(uuid4()))
            trust = old.get('trust')
            # Trust survives only edits that leave the connection identical.
            if trust and trust['connection_sha256'] == connection_digest(row):
                row['trust'] = trust
            section['profiles'][index] = row
        return self._change(expected_revision, change)

    def set_enabled(self, profile_id, enabled, expected_revision):
        if not isinstance(enabled, bool):
            raise ProfileError('enabled')
        def change(section, config):
            row = section['profiles'][self._find(section, profile_id)]
            row.update(enabled=enabled, revision=str(uuid4()))
        return self._change(expected_revision, change)

    def remove(self, profile_id, expected_revision):
        def change(section, config):
            del section['profiles'][self._find(section, profile_id)]
        return self._change(expected_revision, change)

    def set_trust(self, profile_id, tools, expected_revision):
        """Panel-only user decision. tools: {tool name: inputSchema sha256}; the caller (panel)
        fetched and showed the catalog. Pinned to the current connection settings."""
        tools = validate_tools_map(copy.deepcopy(tools))
        def change(section, config):
            row = section['profiles'][self._find(section, profile_id)]
            row['trust'] = {
                'tools': tools, 'connection_sha256': connection_digest(row),
                'granted_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            }
            row['revision'] = str(uuid4())
        return self._change(expected_revision, change)

    def revoke_trust(self, profile_id, expected_revision):
        def change(section, config):
            row = section['profiles'][self._find(section, profile_id)]
            row.pop('trust', None)
            row['revision'] = str(uuid4())
        return self._change(expected_revision, change)

    def trust_decision(self, alias, tool_name, schema_sha256):
        """Fresh snapshot for the moment right before executing a call. 'Not trusted' is a
        normal answer (ordinary approval needed), not an exception."""
        with self.store.lock:
            config = self.store.snapshot()
            section = self._section(config)
            if not isinstance(alias, str):
                raise ProfileError('alias')
            profile = next((row for row in section['profiles'] if row['alias'] == alias), None)
            if profile is None:
                raise ProfileNotFound('profile_not_found')
            decision = trusted_decision(profile, tool_name, schema_sha256)
            decision.update(profile_id=profile['id'], revision=self._revision(config))
            return decision
