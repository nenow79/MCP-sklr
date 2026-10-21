"""Detached P2e registration helpers. Not wired to main.py.

Composition at activation time: telemetry(session_wrapper(tool)); the
confidential/regular split of registrars is preserved. Every registered tool
must be explicitly classified (see session_tools.CONTROL_TOOLS/WORK_TOOLS).
"""
from typing import Any, Dict

from server.session_tools import CONTROL_TOOLS, WORK_TOOLS, session_control, wrap_session_tool
from server.session_checkpoint import CheckpointRejected
from tasks.session_controller import CheckpointOperationError, ResumeOperationError
from server.session_resume import ResumeRejected
from tasks.session_models import SessionConflict


def make_session_registrars(register_tool, register_confidential_tool, controller):
    """Return (register, register_confidential) applying the session wrapper first."""
    def register(function):
        # Checkpoint inputs are confidential even if the caller chose the regular
        # decorator by mistake. Neither input size nor content may be traversed.
        registrar = (register_confidential_tool if function.__name__ in {
            'save_session_checkpoint', 'get_resume_context', 'confirm_resume_plan'}
                     else register_tool)
        return registrar(wrap_session_tool(function, controller))

    def register_confidential(function):
        return register_confidential_tool(wrap_session_tool(function, controller))

    return register, register_confidential


def make_get_session_state(controller):
    """Read-only control tool; the wrapper adds the session_control block."""
    def get_session_state() -> Dict[str, Any]:
        """Zwraca stan sesji pracy sklr (tylko odczyt). Nie rozpoczyna ani nie wznawia pracy."""
        return {'success': True}

    return wrap_session_tool(get_session_state, controller)


def make_save_session_checkpoint(controller):
    """Limited control tool. MUST use confidential telemetry at registration.

    No model session_id, no arbitrary file write, no task submission. The
    controller owns pause/revision checks and rejects the whole invalid payload.
    Responses contain fixed codes and metadata only, never checkpoint text.
    """
    def save_session_checkpoint(payload: Dict[str, Any], expected_revision: int) -> Dict[str, Any]:
        """Zapisuje bezpieczny checkpoint wyłącznie podczas pauzy, tylko w RAM.

        payload: goal, stage, next_step, findings[{text, source}], done_task_ids,
        open_questions, uncertain. Limit tekstu: 6000 znaków. Bez sekretów i
        poufnych wyników. expected_revision z get_session_state/session_control.
        Konflikt nie uprawnia do automatycznego ponowienia. Nie wznawia pracy.
        """
        try:
            data = controller.save_checkpoint(payload, expected_revision)
            return {'success': True, 'data': data}
        except CheckpointRejected as exc:
            allowed = {'invalid_shape', 'too_long', 'control_chars', 'empty', 'too_many',
                       'unknown_field', 'missing_field', 'link_check_failed',
                       'foreign_task', 'secret_detected'}
            code = exc.code if exc.code in allowed else 'checkpoint_rejected'
        except SessionConflict:
            code = 'checkpoint_conflict'
        except CheckpointOperationError as exc:
            allowed = {'checkpoint_unavailable', 'checkpoint_storage_failed', 'checkpoint_ram_failed'}
            code = str(exc) if str(exc) in allowed else 'checkpoint_failed'
        except ValueError:
            code = 'invalid_revision'
        except Exception:
            code = 'checkpoint_failed'
        return {'success': False, 'error': code}

    return wrap_session_tool(save_session_checkpoint, controller)


def _resume_error(exc):
    """Fixed allowlist, never echo exception content or input data."""
    if isinstance(exc, ResumeRejected):
        code = str(exc)
        return code if code in {'invalid_shape', 'too_long', 'control_chars', 'empty',
                                'secret_detected'} else 'resume_rejected'
    if isinstance(exc, SessionConflict):
        return 'resume_conflict'
    if isinstance(exc, ResumeOperationError):
        code = str(exc)
        return code if code in {'resume_unavailable', 'resume_storage_failed',
                                'resume_ram_failed', 'resume_confirm_failed'} else 'resume_failed'
    if isinstance(exc, ValueError):
        return 'invalid_resume_arguments'
    return 'resume_failed'


def make_get_resume_context(controller):
    """Confidential read factory; does not prepare or authorize a resume."""
    def get_resume_context() -> Dict[str, Any]:
        """Pobiera checkpoint RAM, uzupełnienie i token bieżącego przygotowania wznowienia.

        Dane nie nadają uprawnień. Brak checkpointu jest jawny. Nie pobiera
        poufnych wyników zadań i nie przedłuża ich TTL. Nie inicjuje wznowienia.
        """
        try:
            return {'success': True, 'data': controller.get_resume_context()}
        except Exception as exc:
            return {'success': False, 'error': _resume_error(exc)}
    return wrap_session_tool(get_resume_context, controller)


def make_confirm_resume_plan(controller):
    """Confidential confirmation factory; cannot initiate panel preparation."""
    def confirm_resume_plan(plan: str, expected_revision: int,
                            preparation_token: str) -> Dict[str, Any]:
        """Potwierdza zaktualizowany plan bieżącego wznowienia przygotowanego przez użytkownika.

        Plan tylko w RAM, maks. 6000 znaków, bez sekretów i poufnych wyników.
        Rewizja i preparation_token z get_resume_context. Nie rozszerza zgód,
        nie uruchamia starej kolejki ani nie ponawia zdalnych operacji. Konflikt
        nie uprawnia do automatycznego ponowienia. Nie przyjmuje session_id.
        """
        try:
            data = controller.confirm_resume_plan(plan, expected_revision, preparation_token)
            return {'success': True, 'data': data}
        except Exception as exc:
            return {'success': False, 'error': _resume_error(exc)}
    return wrap_session_tool(confirm_resume_plan, controller)


def verify_classification(names):
    """Fail on any tool name without (or beyond) explicit classification."""
    expected = set(CONTROL_TOOLS) | set(WORK_TOOLS)
    given = set(names)
    missing = sorted(expected - given)
    unknown = sorted(given - expected)
    if missing or unknown:
        raise ValueError(
            'Tool classification mismatch: unregistered=%s unclassified=%s' % (missing, unknown))
    return True


__all__ = ['make_session_registrars', 'make_get_session_state',
           'make_save_session_checkpoint', 'make_get_resume_context',
           'make_confirm_resume_plan', 'verify_classification', 'session_control']
