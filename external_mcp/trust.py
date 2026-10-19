"""Trust decisions and call-rate limiting for external MCP profiles. Pure: no I/O, no network.

Trust is granted ONLY by the user in the panel (registry.set_trust). Nothing here grants it.
A decision is a snapshot: callers must ask again immediately before executing a call.
"""
import collections
import hmac
import threading
import time

from external_mcp.profiles import SHA256

DEFAULT_LIMIT = 30
DEFAULT_WINDOW_S = 60.0
MAX_TRACKED_PROFILES = 256


def trusted_decision(profile, tool_name, schema_sha256):
    """Return {'trusted': bool, 'reason': code}. `profile` is a validated stored row.

    validate_profile drops a trust record whose connection digest no longer matches, so a
    row edited outside the registry is simply 'no_trust'. Annotations such as readOnlyHint
    are deliberately not consulted: only the user's pinned tool set decides.
    """
    if not profile.get('enabled'):
        return {'trusted': False, 'reason': 'profile_disabled'}
    trust = profile.get('trust')
    if not trust:
        return {'trusted': False, 'reason': 'no_trust'}
    if (not isinstance(tool_name, str) or not tool_name or not isinstance(schema_sha256, str)
            or not SHA256.fullmatch(schema_sha256)):
        return {'trusted': False, 'reason': 'invalid_request'}
    pinned = trust['tools'].get(tool_name)
    if pinned is None:
        return {'trusted': False, 'reason': 'tool_not_trusted'}
    if not hmac.compare_digest(pinned.encode('ascii'), schema_sha256.encode('ascii')):
        return {'trusted': False, 'reason': 'schema_changed'}
    return {'trusted': True, 'reason': ''}


class TrustRateLimiter:
    """Sliding window per profile id. In memory only; restart resets it.

    A slot is consumed when allow() returns True, even if the call later fails before
    execution. Once exceeded, the caller must fall back to ordinary approval.
    """

    def __init__(self, limit=DEFAULT_LIMIT, window_s=DEFAULT_WINDOW_S, clock=time.monotonic):
        if (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or
                isinstance(window_s, bool) or not isinstance(window_s, (int, float)) or
                window_s <= 0 or not callable(clock)):
            raise ValueError('rate_limiter')
        self._limit, self._window, self._clock = limit, float(window_s), clock
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, profile_id):
        if not isinstance(profile_id, str) or not profile_id:
            return False
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(profile_id, collections.deque())
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            if len(self._hits) > MAX_TRACKED_PROFILES:
                for key in [k for k, v in self._hits.items()
                            if not v or now - v[-1] >= self._window]:
                    del self._hits[key]
            return True

    def reset(self, profile_id):
        with self._lock:
            self._hits.pop(profile_id, None)
