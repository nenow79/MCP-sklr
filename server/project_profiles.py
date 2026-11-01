"""Named root configurations; never chat history, execution or autopilot consent.

All persistence uses ConfigStore. Selection is panel-only and requires no selected
work session. The opaque view revision binds both the saved profiles and live roots.
No migration or filesystem mutation occurs merely by constructing this service.
"""
import copy
import hashlib
import json
import os
import threading
import unicodedata
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from roots import MAX_ROOTS, Root, check_new_root, validate_root
from tasks.models import TaskStatus
from tasks.session_models import SessionConflict

MAX_PROFILES = 100


class ProfileConflict(ValueError):
    pass


def profile_name(value):
    if (type(value) is not str or not value.strip() or len(value) > 100
            or any(unicodedata.category(ch) in ('Cc', 'Cf', 'Cs', 'Zl', 'Zp') for ch in value)):
        raise ValueError('Nazwa projektu musi mieć 1–100 znaków, bez znaków sterujących.')
    return value.strip()


def _version(value):
    if type(value) is not int or value < 1:
        raise ValueError('Nieprawidłowa wersja profilu.')
    return value


class ProjectProfiles:
    def __init__(self, config_store, roots, sessions=None, manager=None, autopilot=None):
        self._config = config_store
        self._roots = roots
        self._sessions = sessions
        self._manager = manager
        self._autopilot = autopilot
        self._lock = threading.RLock()

    def _state(self):
        config = self._config.snapshot()
        value = copy.deepcopy(config.get('project_profiles', {
            'schema': 1, 'profiles': [], 'selected': None}))
        if (type(value) is not dict or set(value) != {'schema', 'profiles', 'selected'}
                or type(value['schema']) is not int or value['schema'] != 1
                or type(value['profiles']) is not list or len(value['profiles']) > MAX_PROFILES):
            raise ValueError('Nieprawidłowa konfiguracja profili projektów.')
        seen, names = set(), set()
        for item in value['profiles']:
            if type(item) is not dict or set(item) != {'id', 'name', 'version', 'roots'}:
                raise ValueError('Nieprawidłowy profil projektu.')
            pid = item['id']
            if (type(pid) is not str or len(pid) != 32
                    or any(ch not in '0123456789abcdef' for ch in pid) or pid in seen):
                raise ValueError('Nieprawidłowy identyfikator projektu.')
            name = profile_name(item['name'])
            if name != item['name'] or name.casefold() in names:
                raise ValueError('Powtórzona lub nieprawidłowa nazwa projektu.')
            _version(item['version'])
            self._parse_roots(item['roots'])
            seen.add(pid)
            names.add(name.casefold())
        if value['selected'] is not None and value['selected'] not in seen:
            raise ValueError('Nieprawidłowy wybór projektu.')
        revision = hashlib.sha256(json.dumps(
            {'profiles': value, 'roots': config.get('roots')},
            sort_keys=True, ensure_ascii=True).encode('utf-8')).hexdigest()
        return config, value, revision

    @staticmethod
    def _parse_roots(values):
        if type(values) is not list or not 1 <= len(values) <= MAX_ROOTS:
            raise ValueError('Projekt musi zawierać 1–20 folderów.')
        result = []
        for entry in values:
            if type(entry) is not dict or set(entry) != {'alias', 'path', 'mode', 'description', 'default'}:
                raise ValueError('Nieprawidłowa konfiguracja folderu projektu.')
            root = Root.from_dict(entry)
            if root.to_dict() != entry:
                raise ValueError('Nieprawidłowy opis folderu.')
            check_new_root(root, result)
            result.append(root)
        if sum(root.default for root in result) != 1:
            raise ValueError('Wybierz dokładnie jeden folder domyślny.')
        return result

    def _validate_roots(self, values):
        result = []
        for root in self._parse_roots(values):
            path = validate_root(root.path, root.mode,
                                 zones=self._roots._zones(), central_dir=self._roots._central())
            root = Root(root.alias, str(path), root.mode, root.description, root.default)
            check_new_root(root, result)
            result.append(root)
        return [root.to_dict() for root in result]

    def list(self):
        with self._lock, self._config.lock:
            config, state, revision = self._state()
        profiles = []
        for item in state['profiles']:
            row = copy.deepcopy(item)
            row['matches_current'] = item['roots'] == config.get('roots')
            row['unavailable'] = [root['alias'] for root in item['roots']
                                  if not Path(root['path']).is_dir()]
            profiles.append(row)
        return {'profiles': profiles, 'selected': state['selected'], 'revision': revision,
                'current_roots': copy.deepcopy(config.get('roots') or [])}

    def start(self, aliases, expected_revision):
        """Panel-only fresh Start, pinned to the exact root/profile view."""
        if self._sessions is None or self._manager is None:
            raise ValueError('Sesje pracy są niedostępne.')
        with ExitStack() as stack:
            stack.enter_context(self._lock)
            stack.enter_context(self._sessions._lock)
            if self._autopilot is not None:
                stack.enter_context(self._autopilot._lock)
            stack.enter_context(self._manager._cond)
            stack.enter_context(self._sessions._controller._lock)
            stack.enter_context(self._config.lock)
            if self._manager._stopped or self._sessions._controller._stopping:
                raise SessionConflict('Serwer jest zatrzymywany.')
            _, _, revision = self._state()
            if type(expected_revision) is not str or expected_revision != revision:
                raise ProfileConflict('Projekt lub foldery zmieniły się. Odśwież przed Startem.')
            available = [row['alias'] for row in self._roots.list_status() if row.get('valid') is True]
            if type(aliases) is not list or aliases != available:
                raise ProfileConflict('Lista folderów zmieniła się. Odśwież przed Startem.')
            return self._sessions.start(aliases)

    def _backup(self):
        # Called under ConfigStore.lock before the first write. Existing backup is
        # never overwritten. Its contents are not returned to the panel/model.
        path = self._config._path
        if path is None or not path.is_file():
            return
        target = path.with_name(path.name + '.pre-project-profiles.bak')
        if target.exists():
            if target.is_symlink() or not target.is_file():
                raise ValueError('Nieprawidłowa kopia konfiguracji.')
            return
        try:
            with path.open('rb') as source:
                fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    with os.fdopen(fd, 'wb') as output:
                        while True:
                            chunk = source.read(65536)
                            if not chunk:
                                break
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                except BaseException:
                    target.unlink()
                    raise
        except FileExistsError:
            raise ProfileConflict('Kopia konfiguracji zmieniła się. Sprawdź stan.') from None

    def change(self, action, body):
        if type(body) is not dict or action not in ('create', 'rename', 'update', 'delete', 'select'):
            raise ValueError('Nieprawidłowa operacja projektu.')
        fields = {'revision', 'confirmed'}
        if action != 'create':
            fields.add('id')
        if action in ('create', 'rename'):
            fields.add('name')
        if (set(body) != fields or body['confirmed'] is not True
                or type(body['revision']) is not str or len(body['revision']) != 64
                or (action != 'create' and type(body['id']) is not str)):
            raise ValueError('Wymagane aktualna rewizja i jawne potwierdzenie.')
        with ExitStack() as stack:
            stack.enter_context(self._lock)
            if self._sessions is not None:
                # Same ordering as SessionAdmin.change_folders/end: admin ->
                # autopilot -> manager -> controller -> config.
                stack.enter_context(self._sessions._lock)
            if self._autopilot is not None:
                stack.enter_context(self._autopilot._lock)
            if self._manager is not None:
                stack.enter_context(self._manager._cond)
            if self._sessions is not None:
                stack.enter_context(self._sessions._controller._lock)
                status = self._sessions._controller.snapshot()
                if status['session_id'] is not None or self._sessions._controller._stopping:
                    raise SessionConflict('Zakończ bieżącą sesję przed zmianą projektu.')
            if self._manager is not None:
                if self._manager._stopped:
                    raise SessionConflict('Serwer jest zatrzymywany.')
                if self._sessions is None and any(self._manager.list(status=status, limit=1)
                        for status in (TaskStatus.RUNNING, TaskStatus.QUEUED, TaskStatus.AWAITING_APPROVAL)):
                    raise SessionConflict('Zakończ lub anuluj bieżące zadania przed zmianą projektu.')
            stack.enter_context(self._config.lock)
            config, state, revision = self._state()
            if body['revision'] != revision:
                raise ProfileConflict('Projekt lub foldery zmieniły się. Odśwież stan.')
            item = None
            if action != 'create':
                item = next((row for row in state['profiles'] if row['id'] == body['id']), None)
                if item is None:
                    raise ProfileConflict('Projekt nie istnieje. Odśwież stan.')
            if action in ('create', 'rename'):
                name = profile_name(body['name'])
                if any(row['name'].casefold() == name.casefold() and row is not item
                       for row in state['profiles']):
                    raise ProfileConflict('Projekt o tej nazwie już istnieje.')
            if action == 'create':
                if len(state['profiles']) >= MAX_PROFILES:
                    raise ValueError('Osiągnięto limit 100 projektów.')
                item = {'id': uuid4().hex, 'name': name, 'version': 1,
                        'roots': self._validate_roots(config.get('roots'))}
                state['profiles'].append(item)
                state['selected'] = item['id']
            elif action == 'rename':
                item['name'] = name  # Name is presentation, not an authorization version.
            elif action == 'update':
                values = self._validate_roots(config.get('roots'))
                if values != item['roots']:
                    item['roots'] = values
                    item['version'] += 1
                state['selected'] = item['id']
            elif action == 'delete':
                state['profiles'].remove(item)
                if state['selected'] == item['id']:
                    state['selected'] = None
            elif action == 'select':
                values = self._validate_roots(item['roots'])
                if values != item['roots']:
                    raise ProfileConflict('Ścieżki projektu zmieniły się. Popraw konfigurację.')
                state['selected'] = item['id']
            old_default = self._roots.default()
            self._backup()
            def mutate(current):
                current['project_profiles'] = state
                if action == 'select':
                    current['roots'] = copy.deepcopy(values)
            self._config.update(mutate)
            new_default = self._roots.default()
        # Listener may rescan skills; never run it under manager/controller locks.
        if action == 'select':
            self._roots._notify(old_default, new_default)
        return self.list()
