"""Panel administration of external MCP profiles (M4a). No HTTP, no FastMCP.

A user action in the panel authorizes the matching operation (spec section 4), so inspection
tasks are queued WITHOUT an approval window, yet still pass verify() right before running.
Trust is never taken from the browser: the panel names a server-held inspection token and
the pinned schema digests come from the snapshot taken by sklr itself.
Everything kept here (last test, inspections) lives in RAM only and is forgotten on restart.
No secret values are read, stored or returned: only the NAMES of referenced variables.
"""
import copy
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from external_mcp.limits import ExternalError
from external_mcp.profiles import ProfileError, ProfileNotFound, connection_digest
from tasks.manager import EXTERNAL_KIND, QueueFullError, TaskStateError
from tasks.volatile_results import VolatileResult

INSPECTION_TTL_S = 300.0
MAX_INSPECTIONS = 20
INSPECT_TIMEOUT_S = 60
CHANGED_MESSAGE = 'Zewnętrzne MCP: ustawienia połączenia zmieniły się od zgłoszenia.'


class OperationError(Exception):
    """Only `safe_error` (a machine code in a fixed sentence) may reach the task record."""

    def __init__(self, code):
        super().__init__(code)
        self.safe_error = 'Zewnętrzne MCP: ' + code


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class ExternalMCPAdmin:
    def __init__(self, registry, manager, *, client=None, environ=None, clock=time.monotonic):
        self.registry, self.manager = registry, manager
        self._client_module = client
        self._environ = environ  # None = os.environ at call time (never copied)
        self._clock = clock
        self._lock = threading.RLock()
        self._tests = {}
        self._inspections = {}

    # --- helpers ---

    def _client(self):
        if self._client_module is not None:
            return self._client_module
        import external_mcp.client as module
        return module

    def _env(self):
        return os.environ if self._environ is None else self._environ

    def _ports(self):
        return self.registry._ports(self.registry.store.snapshot())

    def _find(self, profile_id):
        listing = self.registry.list()
        if not listing['configuration_valid']:
            raise ProfileError('configuration_invalid')
        for row in listing['profiles']:
            if row['id'] == profile_id:
                return row
        raise ProfileNotFound('profile_not_found')

    def _sweep(self):
        now = self._clock()
        with self._lock:
            for token in [t for t, rec in self._inspections.items() if now >= rec['expires']]:
                del self._inspections[token]

    def _forget(self, profile_id):
        with self._lock:
            self._tests.pop(profile_id, None)
            for token in [t for t, rec in self._inspections.items()
                          if rec['profile_id'] == profile_id]:
                del self._inspections[token]

    def _missing_env(self, row):
        names = set(row.get('headers_env', {}).values()) | set(row.get('env_refs', {}).values())
        if row.get('bearer_env'):
            names.add(row['bearer_env'])
        env = self._env()
        return sorted(name for name in names if not env.get(name))

    # --- listing and edits ---

    def list(self):
        listing = self.registry.list()
        if not listing['configuration_valid']:
            return listing
        views = []
        with self._lock:
            self._sweep()
            for row in listing['profiles']:
                view = copy.deepcopy(row)
                test = self._tests.get(row['id'])
                if test and test['digest'] == connection_digest(row):
                    view['last_test'] = {k: v for k, v in test.items() if k != 'digest'}
                else:
                    view['last_test'] = None
                view['env_missing'] = self._missing_env(row)
                view['trusted'] = bool(row.get('trust'))
                views.append(view)
        return {**listing, 'profiles': views}

    def add(self, proposal, expected_revision):
        self.registry.add(proposal, expected_revision)
        return self.list()

    def update(self, profile_id, proposal, expected_revision):
        self.registry.update(profile_id, proposal, expected_revision)
        return self.list()

    def set_enabled(self, profile_id, enabled, expected_revision):
        self.registry.set_enabled(profile_id, enabled, expected_revision)
        return self.list()

    def remove(self, profile_id, expected_revision):
        self.registry.remove(profile_id, expected_revision)
        self._forget(profile_id)
        return self.list()

    def revoke_trust(self, profile_id, expected_revision):
        self.registry.revoke_trust(profile_id, expected_revision)
        return self.list()

    # --- inspection (connection test + tool catalog) ---

    def start_inspection(self, profile_id, acknowledge_launch=False):
        row = self._find(profile_id)
        if row['transport'] == 'stdio' and acknowledge_launch is not True:
            raise ProfileError('launch_not_acknowledged')
        digest = connection_digest(row)
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._sweep()
            if len(self._inspections) >= MAX_INSPECTIONS:
                raise ProfileError('too_many_inspections')
            self._inspections[token] = {
                'profile_id': profile_id, 'digest': digest, 'state': 'pending', 'code': '',
                'tools': [], 'tools_map': {}, 'tested_at': None, 'task_id': None,
                'expires': self._clock() + INSPECTION_TTL_S,
            }
        fields = dict(kind=EXTERNAL_KIND, sensitive=True, offer_trust=False,
                      timeout=INSPECT_TIMEOUT_S, title='Zewnętrzne MCP: ' + row['alias'] + ' / test połączenia',
                      payload={'alias': row['alias'], 'op': 'inspect'})
        try:
            task = self.manager.submit(
                fields, self._executor(token, profile_id, digest), False,
                verify=self._verifier(profile_id, digest))
        except QueueFullError:
            self._drop(token)
            raise ProfileError('queue_full') from None
        except TaskStateError:
            self._drop(token)
            raise ProfileError('task_state') from None
        with self._lock:
            if token in self._inspections:
                self._inspections[token]['task_id'] = task.id
        return {'token': token, 'task_id': task.id}

    def _drop(self, token):
        with self._lock:
            self._inspections.pop(token, None)

    def _verifier(self, profile_id, digest):
        def verify():
            try:
                current = self._find(profile_id)
            except ProfileError:
                return CHANGED_MESSAGE
            return None if connection_digest(current) == digest else CHANGED_MESSAGE
        return verify

    def _executor(self, token, profile_id, digest):
        def executor(cancel_event):
            try:
                current = self._find(profile_id)
                if connection_digest(current) != digest:
                    raise ExternalError('profile_changed')
                result = self._client().fetch_catalog(
                    current, timeout=INSPECT_TIMEOUT_S, cancel_event=cancel_event,
                    environ=self._environ, blocked_ports=self._ports())
                tools = result['tools']
            except ProfileError as exc:  # includes ExternalError; code only, never a value
                self._finish(token, profile_id, digest, code=exc.code)
                raise OperationError(exc.code) from None
            except Exception:
                self._finish(token, profile_id, digest, code='client_failed')
                raise OperationError('client_failed') from None
            self._finish(token, profile_id, digest, tools=tools)
            # The task record and result channel carry only a summary; the catalog stays here.
            return VolatileResult({'content': json.dumps({'ok': True, 'tools': len(tools)}),
                                   'op': 'inspect'})
        return executor

    def _finish(self, token, profile_id, digest, *, code='', tools=None):
        at = _now()
        with self._lock:
            if tools is None:
                self._tests[profile_id] = {'state': 'failed', 'code': code, 'at': at,
                                           'tools': 0, 'digest': digest}
            else:
                self._tests[profile_id] = {'state': 'ok', 'code': '', 'at': at,
                                           'tools': len(tools), 'digest': digest}
            record = self._inspections.get(token)
            if record is None:
                return
            record['tested_at'] = at
            if tools is None:
                record.update(state='failed', code=code)
            else:
                record.update(state='ready', tools=copy.deepcopy(tools),
                              tools_map={tool['name']: tool['schemaSha256'] for tool in tools})

    def get_inspection(self, token):
        with self._lock:
            self._sweep()
            record = self._inspections.get(token) if isinstance(token, str) else None
            if record is None:
                raise ProfileNotFound('inspection_not_found')
            if record['state'] == 'pending' and record['task_id'] is not None:
                task = self.manager.get(record['task_id'])
                if task is not None and task.is_terminal:
                    # The task ended without the executor reporting (e.g. failed verify()).
                    record.update(state='failed', code='task_' + task.status.value,
                                  tested_at=_now())
            return {'state': record['state'], 'code': record['code'],
                    'profile_id': record['profile_id'], 'tested_at': record['tested_at'],
                    'tools': copy.deepcopy(record['tools']) if record['state'] == 'ready' else []}

    # --- trust (the only path that grants it) ---

    def grant_trust(self, profile_id, token, tool_names, expected_revision):
        with self._lock:
            self._sweep()
            record = self._inspections.get(token) if isinstance(token, str) else None
            if (record is None or record['profile_id'] != profile_id
                    or record['state'] != 'ready'):
                raise ProfileError('inspection_unavailable')
            available = dict(record['tools_map'])
            digest = record['digest']
        if connection_digest(self._find(profile_id)) != digest:
            raise ProfileError('inspection_stale')
        if tool_names is None:
            chosen = available
        else:
            if (not isinstance(tool_names, list) or not tool_names
                    or any(not isinstance(name, str) for name in tool_names)
                    or len(set(tool_names)) != len(tool_names)
                    or any(name not in available for name in tool_names)):
                raise ProfileError('trust_tools')
            chosen = {name: available[name] for name in tool_names}
        self.registry.set_trust(profile_id, chosen, expected_revision)
        return self.list()
