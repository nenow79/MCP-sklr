"""Process-local generations. Observation never grants access or calls controllers.

ConfigStore listeners run under its lock and only invalidate these leaf records.
Every committed mutation invalidates observations, including change-and-restore.
"""
import hashlib
import hmac
import json
import secrets
import threading


class ResourceGenerations:
    def __init__(self, store=None, *, key=None):
        self._lock = threading.RLock()
        self._key = key if key is not None else secrets.token_bytes(32)
        if type(self._key) is not bytes or len(self._key) < 32:
            raise ValueError('invalid_generation_key')
        self._rows = {}
        if store is not None:
            store.add_change_listener(self.configuration_changed)

    def digest(self, value):
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                         allow_nan=False).encode('utf-8')
        return hmac.new(self._key, raw, hashlib.sha256).hexdigest()

    def observe(self, identity, fingerprint, *, available=True):
        if type(identity) is not tuple or type(fingerprint) is not str or type(available) is not bool:
            raise ValueError('invalid_resource_observation')
        with self._lock:
            previous = self._rows.get(identity)
            if previous is None or previous[:2] != (fingerprint, available):
                previous = (fingerprint, available, secrets.token_hex(16))
                self._rows[identity] = previous
            return previous[2]

    def invalidate(self, identity=None):
        with self._lock:
            if identity is None:
                self._rows.clear()
            else:
                self._rows.pop(identity, None)

    def configuration_changed(self):
        # Conservative: no missed dependency between roots/bundles/skills/profiles.
        self.invalidate()
