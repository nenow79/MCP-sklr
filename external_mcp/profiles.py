"""Pure validation of external MCP profiles; no filesystem, environment or network I/O."""
import copy
import hashlib
import ipaddress
import json
import re
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit
from uuid import UUID

MAX_PROFILES = 50
MAX_ARGS = 100
MAX_ENV_REFS = 50
MAX_HEADERS = 30
MAX_PROFILE_BYTES = 32768
ALIAS = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}\Z")
BLOCKED_HEADERS = frozenset({
    'host', 'cookie', 'set-cookie', 'content-length', 'connection',
    'transfer-encoding', 'proxy-authorization', 'upgrade',
    'mcp-session-id', 'mcp-protocol-version', 'last-event-id',
})


class ProfileError(ValueError):
    """Safe errors contain field names/codes, never submitted values."""

    def __init__(self, code):
        self.code = code
        super().__init__("Niepoprawna konfiguracja MCP: " + code)


class ProfileConflict(ProfileError):
    pass


class ProfileNotFound(ProfileError):
    pass


def text(value, field, limit=4096, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not value and not empty):
        raise ProfileError(field)
    # Reject controls, surrogates and bidi/format characters, including in paths/arguments.
    if any(unicodedata.category(char).startswith('C') for char in value):
        raise ProfileError(field)
    return value


def identifier(value, field):
    text(value, field, 36)
    try:
        parsed = UUID(value)
    except ValueError:
        raise ProfileError(field) from None
    if str(parsed) != value or parsed.version != 4:
        raise ProfileError(field)
    return value


def absolute_path(value, field):
    text(value, field)
    windows = PureWindowsPath(value)
    if windows.is_absolute():
        if value.startswith(('\\\\?\\', '\\\\.\\')) or any(
                ':' in part for part in windows.parts[1:]):
            raise ProfileError(field)
        return value
    if not PurePosixPath(value).is_absolute() or '\\' in value:
        raise ProfileError(field)
    return value


def env_refs(value, field, *, headers=False):
    if not isinstance(value, dict) or len(value) > (MAX_HEADERS if headers else MAX_ENV_REFS):
        raise ProfileError(field)
    result, seen = {}, set()
    for name, source in value.items():
        text(name, field, 128)
        text(source, field, 128)
        pattern = HEADER_NAME if headers else ENV_NAME
        normalized = name.lower() if headers else name.upper()
        if not pattern.fullmatch(name) or not ENV_NAME.fullmatch(source) or normalized in seen:
            raise ProfileError(field)
        if headers and normalized in BLOCKED_HEADERS:
            raise ProfileError(field)
        seen.add(normalized)
        result[name] = source
    return result


SHA256 = re.compile(r'[0-9a-f]{64}\Z')
MAX_TRUST_TOOLS = 200
MAX_TRUST_BYTES = 49152
TRUST_KEYS = {'tools', 'connection_sha256', 'granted_at'}
CONNECTION_FIELDS = {
    'stdio': ('transport', 'command', 'args', 'cwd', 'env_refs'),
    'http': ('transport', 'url', 'headers_env', 'bearer_env', 'ca_file'),
    'sse': ('transport', 'url', 'headers_env', 'bearer_env', 'ca_file'),
}


def connection_digest(profile):
    """Digest of settings that define WHERE/HOW we connect. Alias, description and enabled
    are excluded on purpose: renaming or toggling a profile must not revoke trust."""
    keys = CONNECTION_FIELDS[profile['transport']]
    encoded = json.dumps({key: profile.get(key) for key in keys}, sort_keys=True,
                         separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def validate_tools_map(tools):
    """{tool name: inputSchema sha256}. Empty sets are refused: trust must name tools."""
    if not isinstance(tools, dict) or not 1 <= len(tools) <= MAX_TRUST_TOOLS:
        raise ProfileError('trust_tools')
    result = {}
    for name, digest in tools.items():
        text(name, 'trust_tools', 256)
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ProfileError('trust_tools')
        result[name] = digest
    return result


def validate_trust(value, expected_connection):
    """Strict shape check. Returns None (trust silently dropped) when the connection digest
    no longer matches the profile: trust never survives a changed connection."""
    if not isinstance(value, dict) or set(value) != TRUST_KEYS:
        raise ProfileError('trust')
    tools = validate_tools_map(value['tools'])
    connection = value['connection_sha256']
    if not isinstance(connection, str) or not SHA256.fullmatch(connection):
        raise ProfileError('trust')
    granted = text(value['granted_at'], 'trust', 40)
    try:
        datetime.fromisoformat(granted)
    except ValueError:
        raise ProfileError('trust') from None
    record = {'tools': tools, 'connection_sha256': connection, 'granted_at': granted}
    if len(json.dumps(record, ensure_ascii=True).encode('utf-8')) > MAX_TRUST_BYTES:
        raise ProfileError('trust_limit')
    if connection != expected_connection:
        return None
    return record


def validate_url(value, blocked_ports=(), allow_query=False):
    """Profile URLs never allow a query. allow_query is only for same-origin request URLs
    of legacy SSE, whose server-issued message endpoint carries a session id."""
    text(value, 'url')
    if value != value.strip() or any(char.isspace() for char in value) or '\\' in value:
        raise ProfileError('url')
    try:
        parsed = urlsplit(value)
        explicit_port = parsed.port
        port = explicit_port if explicit_port is not None else (443 if parsed.scheme == 'https' else 80)
        host = parsed.hostname
    except ValueError:
        raise ProfileError('url') from None
    if (parsed.scheme not in ('http', 'https') or not host or
            parsed.username is not None or parsed.password is not None or
            ('?' in value and not allow_query) or '#' in value or '%' in parsed.netloc):
        raise ProfileError('url')
    decoded = unquote(value)
    if any(unicodedata.category(char).startswith('C') for char in decoded):
        raise ProfileError('url')
    if not 1 <= port <= 65535:
        raise ProfileError('url')
    try:
        address = ipaddress.ip_address(host)
        local = address.is_loopback or address.is_unspecified
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            local = local or address.ipv4_mapped.is_loopback or address.ipv4_mapped.is_unspecified
    except ValueError:
        local = host.lower().rstrip('.') in ('localhost', 'localhost.localdomain') or host.lower().endswith('.localhost')
    if local and port in blocked_ports:
        raise ProfileError('self_or_panel_endpoint')
    return value


def validate_profile(proposal, *, stored=False, blocked_ports=()):
    if not isinstance(proposal, dict):
        raise ProfileError('profile')
    transport = proposal.get('transport')
    if not isinstance(transport, str) or transport not in ('http', 'sse', 'stdio'):
        raise ProfileError('transport')
    common = {'alias', 'description', 'enabled', 'transport'}
    fields = {'command', 'args', 'cwd', 'env_refs'} if transport == 'stdio' else {
        'url', 'headers_env', 'bearer_env', 'ca_file'}
    metadata = {'id', 'revision'} if stored else set()
    # 'trust' exists only in stored rows written by the registry, never in a proposal.
    optional = {'trust'} if stored else set()
    if set(proposal) - common - fields - metadata - optional:
        raise ProfileError('unknown_fields')
    if stored and not common.union(fields, metadata).issubset(proposal):
        raise ProfileError('missing_fields')
    alias = text(proposal.get('alias'), 'alias', 64)
    if not ALIAS.fullmatch(alias):
        raise ProfileError('alias')
    description = text(proposal.get('description', ''), 'description', 2000, empty=True)
    enabled = proposal.get('enabled', False)
    if not isinstance(enabled, bool):
        raise ProfileError('enabled')
    result = dict(alias=alias, description=description, enabled=enabled, transport=transport)
    if transport == 'stdio':
        command = text(proposal.get('command'), 'command')
        if command != command.strip():
            raise ProfileError('command')
        args = proposal.get('args', [])
        if not isinstance(args, list) or len(args) > MAX_ARGS:
            raise ProfileError('args')
        args = [text(arg, 'args', empty=True) for arg in args]
        cwd = proposal.get('cwd')
        if cwd is not None:
            absolute_path(cwd, 'cwd')
        result.update(command=command, args=args, cwd=cwd,
                      env_refs=env_refs(proposal.get('env_refs', {}), 'env_refs'))
    else:
        bearer = proposal.get('bearer_env')
        if bearer is not None:
            text(bearer, 'bearer_env', 128)
            if not ENV_NAME.fullmatch(bearer):
                raise ProfileError('bearer_env')
        headers = env_refs(proposal.get('headers_env', {}), 'headers_env', headers=True)
        if bearer and any(name.lower() == 'authorization' for name in headers):
            raise ProfileError('duplicate_authorization')
        ca_file = proposal.get('ca_file')
        if ca_file is not None:
            absolute_path(ca_file, 'ca_file')
        result.update(url=validate_url(proposal.get('url'), blocked_ports),
                      bearer_env=bearer, headers_env=headers, ca_file=ca_file)
    if stored:
        result.update(id=identifier(proposal.get('id'), 'id'),
                      revision=identifier(proposal.get('revision'), 'revision'))
    if len(json.dumps(result, ensure_ascii=True).encode('utf-8')) > MAX_PROFILE_BYTES:
        raise ProfileError('profile_too_large')
    if stored and 'trust' in proposal:
        trusted = validate_trust(proposal['trust'], connection_digest(result))
        if trusted is not None:
            result['trust'] = trusted
    return copy.deepcopy(result)
