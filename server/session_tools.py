"""Detached P2c wrapper. No production registration or panel mutation here.

Compose as: telemetry(session_wrapper(tool)), with the existing confidential
flag unchanged. MCP sees the original signature through functools.wraps.
Only synchronous dict-returning tools are supported by this version.
"""
import inspect
import logging
from functools import wraps

from tasks.session_controller import SessionBlocked

logger = logging.getLogger(__name__)
CONTROL_TOOLS = frozenset({
    'ping', 'get_session_state', 'get_task', 'list_tasks', 'wait_task', 'cancel_task',
})
WORK_TOOLS = frozenset({
    'list_skills', 'activate_skill', 'read_skill_resource', 'run_skill_script',
    'list_external_mcp', 'list_external_mcp_tools', 'call_external_mcp_tool',
    'list_roots', 'request_root', 'list_directory', 'read_file', 'read_files_batch',
    'search_code', 'edit_file', 'create_file', 'create_tree', 'manage_folder',
    'secret_reveal', 'knowledge_bundles', 'knowledge_search', 'knowledge_get',
    'knowledge_save', 'prepare_project', 'knowledge_audit', 'knowledge_convert',
})

STOP_INSTRUCTION = (
    'Praca jest zablokowana przez sklr. Nie ponawiaj wywołań, nie używaj innych '
    'narzędzi ani nowej sesji do kontynuowania zadania. Zakończ odpowiedź i czekaj '
    'na użytkownika. Narzędzie zapisu checkpointu nie jest jeszcze dostępne; '
    'nie deklaruj, że checkpoint został zapisany.'
)


def tool_role(name):
    if name in CONTROL_TOOLS:
        return 'control'
    if name in WORK_TOOLS:
        return 'work'
    raise ValueError('Tool has no explicit session classification.')


def session_control(controller):
    """Reads only controller metadata, never tool inputs/results.

    Snapshot failures produce a fixed stop signal, not an exception containing
    storage details. This does not invent a confirmed durable pause state.
    """
    try:
        state = controller.snapshot()
    except Exception:
        return {
            'code': 'session_state_unavailable', 'reason': 'state_unavailable',
            'scope': 'instance', 'execution_blocked': True,
            'checkpoint_available': False, 'instruction': STOP_INSTRUCTION,
        }
    if state['execution_blocked']:
        if state['session_id'] is None:
            code, reason = 'session_not_selected', 'user_selection_required'
        elif state['state'] in ('pause_requested', 'paused'):
            code = 'session_paused'
            reason = ('server_restarted' if state['reason'] == 'server_restarted'
                      else 'user_requested_pause')
        else:
            code, reason = 'session_blocked', 'state_conflict_or_storage_error'
    else:
        code, reason = 'session_active', ''
    return {
        **state, 'code': code, 'reason': reason,
        'checkpoint_available': False,
        'instruction': STOP_INSTRUCTION if state['execution_blocked'] else '',
    }


def wrap_session_tool(function, controller):
    """No model-controlled session identifier; all clients share controller.

    Work admission is separate from queued-task admission in TaskManager.
    Finish errors do not replace a valid result or the tool's own exception.
    session_control is a reserved top-level key, never merged with foreign data.
    """
    role = tool_role(function.__name__)
    if inspect.iscoroutinefunction(function) or inspect.isasyncgenfunction(function):
        raise ValueError('Async session tools are not supported by this wrapper.')

    @wraps(function)
    def wrapped(*args, **kwargs):
        token = None
        if role == 'work':
            try:
                token = controller.begin_action()
            except SessionBlocked:
                return {
                    'success': False, 'error': 'Wykonanie zablokowane przez sesję sklr.',
                    'session_control': session_control(controller),
                }
        try:
            result = function(*args, **kwargs)
        finally:
            if token is not None:
                try:
                    controller.finish_action(token)
                except Exception:
                    logger.error('Nie udało się domknąć stanu sesji narzędzia.')
        if type(result) is not dict:
            raise TypeError('Session tool must return a plain dictionary.')
        # Shallow copy only: confidential payload is not traversed, serialized
        # or mutated. Caller-owned result retains identity of nested data.
        response = dict(result)
        response['session_control'] = session_control(controller)
        return response

    return wrapped
