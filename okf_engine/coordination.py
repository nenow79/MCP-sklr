"""Optional outer coordination for local knowledge operations that submit tasks.

Installed only by the autopilot execution bridge. Acquire manager/session BEFORE
the service's configuration lock, including nested after-write notifications and
panel refresh. No contract lock, network or subprocess is involved in this scope.
"""
from contextlib import nullcontext
from functools import wraps


def coordinate_refresh(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        service = getattr(self, 'service', self)
        factory = getattr(service, '_dispatch_context', nullcontext)
        with factory():
            return function(self, *args, **kwargs)
    return call
