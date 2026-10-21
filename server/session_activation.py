"""Work-session composition, testable without importing production main.

No application databases or servers are created at import time. Startup must
abort on invalid configuration/bootstrap, never fall back to unguarded work.
"""
import time

from server.session_bootstrap import build_work_session
from server.session_registration import (
    make_session_registrars, make_get_session_state, make_save_session_checkpoint,
    make_get_resume_context, make_confirm_resume_plan, verify_classification,
)


def configured_work_session(config, db_path, roots):
    section = config.get('work_sessions', {'enabled': False})
    if type(section) is not dict or set(section) - {'enabled'}:
        raise ValueError('Invalid work-session configuration.')
    enabled = section.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('Work-session enabled must be a boolean.')
    return build_work_session(db_path, roots, enabled=enabled)


def session_registrars(register_tool, register_confidential_tool, runtime):
    """Track actual registration names; wrapping happens before telemetry.

    register_tool/register_confidential_tool are the original telemetry/MCP
    registrars. Factories already have a session wrapper, so use originals for
    their registration; ordinary functions get exactly one wrapper below.
    """
    names = set()

    def tracked(registrar):
        def register(function):
            if function.__name__ in names:
                raise ValueError('Duplicate work-session tool registration.')
            result = registrar(function)
            names.add(function.__name__)
            return result
        return register

    regular = tracked(register_tool)
    confidential = tracked(register_confidential_tool)
    if runtime is None:
        return regular, confidential, names
    controller = runtime.controller
    regular(make_get_session_state(controller))
    for factory in (make_save_session_checkpoint, make_get_resume_context, make_confirm_resume_plan):
        confidential(factory(controller))
    wrapped, wrapped_confidential = make_session_registrars(regular, confidential, controller)
    return wrapped, wrapped_confidential, names


def verify_session_registration(runtime, names):
    if runtime is not None:
        verify_classification(names)


def stop_work_session(manager, runtime, budget=5):
    """Stop submissions, wait boundedly, never close stores beneath execution.

    No cancellation/retry of remote operations. False means RAM/store closure
    was deliberately withheld because a worker/action still needs them.
    Application exit may interrupt the daemon workers as before.
    """
    manager.shutdown()
    if runtime is None:
        return True
    deadline = time.monotonic() + budget
    for worker in manager._workers:
        worker.join(timeout=max(0, deadline - time.monotonic()))
    if any(worker.is_alive() for worker in manager._workers):
        return False
    with runtime.controller._lock:
        if runtime.controller._tokens:
            return False
        runtime.controller._blocked = True
        runtime.close()
    return True
