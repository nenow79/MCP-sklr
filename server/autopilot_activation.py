"""Strict opt-in composition, without import-time effects or authority recovery."""
from autopilot.audit_store import AuditStore
from autopilot.controller import AutopilotController
from autopilot.runtime import AutopilotRuntime
from server.autopilot_admin import AutopilotAdmin


def configured_autopilot(config, db_path, work_session, store, roots, manager, **dependencies):
    section = config.get('autopilot', {'enabled': False})
    if type(section) is not dict or set(section) - {'enabled'} or type(section.get('enabled', False)) is not bool:
        raise ValueError('Invalid autopilot configuration.')
    if not section.get('enabled', False):
        return None
    if work_session is None:
        raise ValueError('Autopilot requires work_sessions.enabled=true.')
    audit = None
    try:
        audit = AuditStore(db_path)
        controller = AutopilotController(work_session.controller, audit)
        runtime = AutopilotRuntime(controller, store, roots, manager, **dependencies)
        return AutopilotAdmin(runtime)
    except Exception:
        # Startup is aborted, never continue with a partially composed controller.
        if audit is not None:
            try:
                audit.close()
            except Exception:
                pass
        raise ValueError('Autopilot bootstrap failed.') from None
