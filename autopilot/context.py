"""Private per-call bridge; never accepted through MCP arguments."""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps

CURRENT = ContextVar('sklr_autopilot_invocation', default=None)
MAINTENANCE = object()


def recheck_current():
    scope = CURRENT.get()
    if scope is not None:
        scope.recheck()


def mark_uncertain():
    scope = CURRENT.get()
    if scope is not None:
        scope.uncertain = True


def mark_dispatched():
    scope = CURRENT.get()
    if scope is not None:
        scope.dispatched = True


def check_knowledge_scope():
    """Leaf check under knowledge/config lock; never acquires control locks."""
    scope = CURRENT.get()
    if scope is not None:
        scope.verify_only()


@contextmanager
def local_effect(cancel_event=None, *, verify=None):
    scope = CURRENT.get()
    if scope is None or scope.ticket is None:
        yield
    else:
        with scope.runtime.effect(scope.ticket, retain_config=True, cancel_event=cancel_event, verify=verify):
            yield


def local_write(function=None, *, verify=None):
    if function is None:
        return lambda target: local_write(target, verify=verify)
    @wraps(function)
    def execute(cancel_event):
        with local_effect(cancel_event, verify=verify):
            return function(cancel_event)
    return execute
