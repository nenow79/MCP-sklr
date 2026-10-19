"""Resolve runtime credentials only when requested, without saving or logging them."""
import os
import ssl
from dataclasses import dataclass, field
from pathlib import Path

from external_mcp.limits import ExternalError
from external_mcp.profiles import validate_profile

# No PYTHONPATH, NODE_OPTIONS, proxy credentials or arbitrary parent variables.
# The actual stdio launcher MUST replace, not merge, its process environment.
SYSTEM_ENV_ALLOWLIST = frozenset({
    'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC',
    'TEMP', 'TMP', 'TMPDIR', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
    'LANG', 'LC_ALL', 'LC_CTYPE',
})
MAX_ENV_VALUE_BYTES = 32768
MAX_HEADER_VALUE_BYTES = 8192
MAX_RUNTIME_ENV_BYTES = 131072


@dataclass(repr=False)
class RuntimeConfig:
    transport: str
    url: str = ''
    command: str = ''
    args: list = field(default_factory=list)
    cwd: str = None
    headers: dict = field(default_factory=dict, repr=False)
    env: dict = field(default_factory=dict, repr=False)
    tls: object = field(default=None, repr=False)

    def __repr__(self):
        return 'RuntimeConfig(<private>)'

    def clear(self):
        """Drop references after use. Python strings are not securely zeroed."""
        self.headers.clear()
        self.env.clear()


def _env_value(environ, name, *, header=False):
    value = environ.get(name)
    if not isinstance(value, str) or not value:
        raise ExternalError('environment_missing')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError:
        raise ExternalError('environment_invalid') from None
    if '\x00' in value or size > (MAX_HEADER_VALUE_BYTES if header else MAX_ENV_VALUE_BYTES):
        raise ExternalError('environment_invalid')
    if header and (any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ExternalError('header_invalid')
    return value


def prepare_runtime(profile, *, environ=None, blocked_ports=()):
    """Does not connect or spawn. CA/cwd checks occur outside the ConfigStore lock.

    environ injection is for testing, not an API accepting model-supplied credentials.
    Raw values must never appear in task payloads, history or exception messages.
    """
    row = validate_profile(profile, stored=True, blocked_ports=blocked_ports)
    source = os.environ if environ is None else environ
    runtime = RuntimeConfig(transport=row['transport'])
    try:
        if row['transport'] == 'stdio':
            runtime.command, runtime.args, runtime.cwd = row['command'], row['args'], row['cwd']
            if runtime.cwd is not None:
                try:
                    available = Path(runtime.cwd).is_dir()
                except (OSError, ValueError):
                    available = False
                if not available:
                    raise ExternalError('cwd_unavailable')
            for name, value in source.items():
                if isinstance(name, str) and name.upper() in SYSTEM_ENV_ALLOWLIST:
                    runtime.env[name] = _env_value(source, name)
            for target, reference in row['env_refs'].items():
                # Case-insensitive replacement also avoids duplicate keys on Windows.
                for existing in list(runtime.env):
                    if existing.upper() == target.upper():
                        del runtime.env[existing]
                runtime.env[target] = _env_value(source, reference)
            total = sum(len(key.encode('utf-8')) + len(value.encode('utf-8'))
                        for key, value in runtime.env.items())
            if total > MAX_RUNTIME_ENV_BYTES:
                raise ExternalError('environment_limit')
        else:
            runtime.url = row['url']
            runtime.headers = {name: _env_value(source, reference, header=True)
                               for name, reference in row['headers_env'].items()}
            if row['bearer_env']:
                runtime.headers['Authorization'] = 'Bearer ' + _env_value(source, row['bearer_env'], header=True)
            try:
                runtime.tls = ssl.create_default_context(cafile=row['ca_file'])
            except (OSError, ValueError, ssl.SSLError):
                raise ExternalError('tls_configuration') from None
        return runtime
    except BaseException:
        runtime.clear()
        raise
