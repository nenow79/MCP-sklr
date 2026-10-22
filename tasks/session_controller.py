"""Single-instance admission foundation; not wired to production yet.

Only authenticated panel integration may bind/pause. No model-facing mutation.
One controller must be shared by tools and TaskManager. Admission tokens mark
local action start, not completion or authorization. Callers must release each
accepted token in finally. No lock may be held across actual tool execution.
P2 integration must define lock ordering with TaskManager before using this API.
"""
import copy
import threading
from contextlib import contextmanager

from tasks.session_models import CheckpointState, SessionConflict, SessionNotFound, SessionState


class SessionBlocked(RuntimeError):
    """Fixed control signal, never foreign tool contents."""


class StaleSessionTask(SessionBlocked):
    """Old generation of this session; reject task without closing its gate."""


class CheckpointOperationError(RuntimeError):
    """Fixed operational error; never checkpoint contents or storage details."""


class ResumeOperationError(RuntimeError):
    """Fixed resume error; no user/model text or storage details."""


class SessionController:
    def __init__(self, store, checkpoints=None, resumes=None):
        self._store = store
        self._lock = threading.RLock()
        self._selected = None
        self._blocked = True
        self._tokens = set()
        self._checkpoints = checkpoints
        # Accepted durable revision of the local RAM record. Finalizing the
        # same pause may advance this without changing its original save revision.
        self._checkpoint_current_revision = None
        self._resumes = resumes
        self._resume_pending_revision = None
        self._resume_recovery_required = False
        self._resume_coordination_active = False
        self._confirmed_plan_revision = None
        self._autopilot = None
        self._stopping = False
        self._end_ready = False

    def _invalidate_resume(self):
        """Logical invalidation precedes best-effort RAM cleanup.

        Leftover RAM never authorizes a token; a new preparation must clear it
        successfully before publishing another generation.
        """
        self._blocked = True
        self._resume_pending_revision = None
        self._confirmed_plan_revision = None
        if self._resumes is not None:
            try:
                self._resumes.clear()
            except Exception:
                pass

    def bind(self, sid, expected_revision):
        """Panel-only integration: bind once. Switching/resuming is P3 work.

        Does not recover a store or select a session automatically on reconnect.
        Authentication and valid root registry entries must be checked by caller.
        """
        with self._lock:
            item = self._store.get(sid)
            if item is None:
                raise SessionNotFound('Session not found.')
            if type(expected_revision) is not int or expected_revision < 1:
                raise ValueError('Expected a positive revision.')
            if item.revision != expected_revision:
                raise SessionConflict('Session revision changed.')
            if item.lifecycle != 'open':
                raise SessionConflict('Session has ended.')
            if self._selected is not None:
                if sid != self._selected:
                    raise SessionConflict('Session switching is not supported.')
                # Rebinding/reconnecting can never clear the pause latch.
                return self.snapshot()
            self._selected = sid
            self._blocked = item.state != SessionState.ACTIVE or (
                self._autopilot is not None and self._autopilot._recovery)
            return self.snapshot()

    def recover_selection(self, sid, expected_revision):
        """Explicit panel selection after bootstrap recovery, never ACTIVE bind.

        Any already selected session prevents switching. No RAM is restored and
        no task is scheduled. Roots/auth are checked by the panel adapter.
        """
        with self._lock:
            if self._selected is not None:
                raise SessionConflict('A session is already selected.')
            item = self._store.get(sid)
            if (type(expected_revision) is not int or expected_revision < 1):
                raise ValueError('Expected a positive revision.')
            if (item is None or item.revision != expected_revision
                    or item.lifecycle != 'open'
                    or item.state != SessionState.PAUSED or item.reason != 'server_restarted'):
                raise SessionConflict('Session is not a current restart recovery candidate.')
            self._invalidate_resume()
            self._checkpoint_current_revision = None
            self._resume_recovery_required = False
            self._selected = item.id
            return self.snapshot()

    def begin_action(self):
        """Atomic local start. No session ID is accepted from the model.

        Each actual execution needs its own token: admission of a tool that
        submits a queued task does not authorize that task's later dispatch.
        """
        with self._lock:
            if self._selected is None or self._blocked or self._stopping:
                raise SessionBlocked('Session execution is blocked.')
            try:
                item = self._store.get(self._selected)
            except Exception:
                self._blocked = True
                raise SessionBlocked('Session state is unavailable.') from None
            if item is None or item.lifecycle != 'open' or item.state != SessionState.ACTIVE:
                self._blocked = True
                raise SessionBlocked('Session execution is blocked.')
            token = object()
            self._tokens.add(token)
            return token

    @contextmanager
    def task_submission(self):
        """Internal manager only; manager lock precedes controller lock.

        Keep assignment and task creation on the same side of the pause gate.
        This is synchronization, NOT a transaction spanning the two databases.
        No manager callbacks may be invoked while acquiring locks in reverse.
        """
        with self._lock:
            if self._selected is None or self._blocked:
                raise SessionBlocked('Session execution is blocked.')
            try:
                item = self._store.get(self._selected)
            except Exception:
                self._blocked = True
                raise SessionBlocked('Session state is unavailable.') from None
            if item is None or item.lifecycle != 'open' or item.state != SessionState.ACTIVE:
                self._blocked = True
                raise SessionBlocked('Session execution is blocked.')

            def associate(task_id):
                try:
                    self._store.link_task(item.id, task_id, item.revision)
                except Exception:
                    self._blocked = True
                    raise SessionBlocked('Task association could not be saved.') from None

            yield associate

    def begin_task(self, task_id):
        """Internal dispatch: validate immutable association before admission.

        TaskManager holds its condition first. The controller never calls back
        into TaskManager. Paused/invalid tasks remain queued but unscheduled.
        """
        with self._lock:
            if self._selected is None or self._blocked:
                raise SessionBlocked('Session execution is blocked.')
            try:
                item = self._store.get(self._selected)
                link = self._store.get_task_link(task_id)
            except Exception:
                self._blocked = True
                raise SessionBlocked('Session state is unavailable.') from None
            if (item is not None and item.state == SessionState.ACTIVE and link is not None
                    and link.session_id == self._selected and link.session_revision < item.revision):
                raise StaleSessionTask('Task belongs to an old session generation.')
            if (item is None or item.lifecycle != 'open' or item.state != SessionState.ACTIVE or link is None
                    or link.session_id != self._selected
                    or link.session_revision != item.revision):
                self._blocked = True
                raise SessionBlocked('Task session association is not current.')
            return self.begin_action()

    def stop_admissions(self):
        """Irreversible process shutdown latch; started actions may still finish."""
        with self._lock:
            self._stopping = self._blocked = True
            self._invalidate_resume()
            if self._selected is not None:
                try:
                    state = self.snapshot()
                    if state['state'] == 'active':
                        self.request_pause(state['revision'])
                except Exception:
                    # Keep the RAM latch closed even if durable pause fails.
                    return False
            return True

    def finish_action(self, token):
        """Release exactly once, including failed/cancelled actions.

        Invalid releases never decrease the count. A persistence failure leaves
        the latch blocked and the durable state unfinalized; it is not success.
        """
        with self._lock:
            if token not in self._tokens:
                raise ValueError('Unknown action token.')
            self._tokens.remove(token)
            if self._blocked and not self._tokens and self._selected is not None:
                item = self._store.get(self._selected)
                if item.lifecycle in ('ending', 'ended'):
                    self.finish_end_internal()
                else:
                    self._finalize_pause()

    def request_end_internal(self, sid, revision):
        """Manager holds its lock and this controller lock throughout cancellation."""
        if self._stopping or sid != self._selected:
            raise SessionConflict('Session selection changed.')
        from tasks.session_models import positive_int
        positive_int(revision)
        item = self._store.get(sid)
        if item is None or item.revision != revision or item.lifecycle not in ('open', 'ending', 'ended'):
            raise SessionConflict('Session revision changed.')
        self._blocked = True
        self._end_ready = False
        self._store.request_end(sid, revision)
        self._invalidate_resume()
        if self._resumes is not None:
            self._resumes.clear()
        self._checkpoint_current_revision = None
        if self._checkpoints is not None:
            self._checkpoints.discard(sid)
        if self._autopilot is not None:
            self._autopilot.session_ended_internal()

    def finish_end_internal(self):
        if not self._end_ready or self._tokens or self._selected is None:
            return
        item = self._store.get(self._selected)
        self._store.finish_end(item.id, item.revision)
        self._selected = None
        self._resume_recovery_required = False
        self._end_ready = False

    def request_pause(self, expected_revision):
        """Panel-only integration. Persistence errors do not reopen admission."""
        with self._lock:
            if self._selected is None:
                raise SessionConflict('No session is selected.')
            # Close admission before I/O. Even conflicts leave admission closed;
            # user must inspect status and retry with the current revision.
            self._blocked = True
            if self._autopilot is not None:
                self._autopilot.session_paused_internal()
            self._resume_pending_revision = None
            self._confirmed_plan_revision = None
            if self._resumes is not None:
                try:
                    self._resumes.clear()
                except Exception:
                    raise ResumeOperationError('resume_ram_failed') from None
            self._store.request_pause(self._selected, expected_revision)
            if not self._tokens:
                self._finalize_pause()
            return self.snapshot()

    def _finalize_pause(self):
        item = self._store.get(self._selected)
        if item is None:
            raise SessionNotFound('Session not found.')
        if item.state == SessionState.PAUSE_REQUESTED:
            finalized = self._store.mark_paused(item.id, item.revision)
            if self._checkpoint_current_revision == item.revision:
                self._checkpoint_current_revision = finalized.revision

    def save_checkpoint(self, payload, expected_revision):
        """Limited checkpoint operation; server-selected session, no model sid.

        Lock order: controller -> session store/RAM. Never calls TaskManager.
        SQLite and RAM are NOT one transaction: a failed publication can leave
        durable saved metadata, but availability stays false and no success is
        returned. A stale request cannot invalidate the previous checkpoint.
        """
        from server.session_checkpoint import validate_checkpoint

        with self._lock:
            if self._checkpoints is None:
                raise CheckpointOperationError('checkpoint_unavailable')
            if self._selected is None:
                raise SessionConflict('No session is selected.')
            try:
                item = self._store.get(self._selected)
            except Exception:
                raise CheckpointOperationError('checkpoint_storage_failed') from None
            if (item is None or item.lifecycle != 'open' or item.state not in
                    (SessionState.PAUSE_REQUESTED, SessionState.PAUSED)):
                raise SessionConflict('Checkpoint requires a paused session.')
            if type(expected_revision) is not int or expected_revision < 1:
                raise ValueError('Expected a positive revision.')
            if item.revision != expected_revision:
                raise SessionConflict('Session revision changed.')

            def owns_task(task_id):
                link = self._store.get_task_link(task_id)
                return link is not None and link.session_id == item.id

            checkpoint = validate_checkpoint(payload, owns_task)
            # Only a validated, current save invalidates the old handshake.
            # Even an uncertain commit must not leave a usable old token.
            self._invalidate_resume()
            try:
                saved = self._store.mark_checkpoint_saved(item.id, expected_revision)
            except SessionConflict:
                raise
            except Exception:
                # The commit outcome may be uncertain. Do not advertise an old
                # RAM record as matching the current durable metadata.
                self._checkpoint_current_revision = None
                raise CheckpointOperationError('checkpoint_storage_failed') from None
            self._checkpoint_current_revision = None
            try:
                record = self._checkpoints.put(saved.id, saved.revision, checkpoint)
            except Exception:
                # Keep the error independent from payload, paths and RAM internals.
                # Availability remains false even if discard itself fails.
                try:
                    self._checkpoints.discard(saved.id)
                except Exception:
                    pass
                raise CheckpointOperationError('checkpoint_ram_failed') from None
            self._checkpoint_current_revision = saved.revision
            return {'saved': True, 'revision': saved.revision,
                    'size': checkpoint.size, 'saved_at': record.saved_at}

    def _checkpoint_record(self, item):
        """Read under controller lock; mismatch/restart never revives old RAM."""
        if (self._checkpoints is None or item is None
                or item.state not in (SessionState.PAUSE_REQUESTED, SessionState.PAUSED)
                or item.checkpoint_state != CheckpointState.SAVED
                or self._checkpoint_current_revision != item.revision):
            return None
        try:
            return self._checkpoints.get(item.id)
        except Exception:
            raise CheckpointOperationError('checkpoint_ram_failed') from None

    def get_checkpoint(self):
        """User-panel data, only during pause; caller must enforce panel auth.

        Fresh structured copy; no persistence, logging or confidential result
        lookup. Entry revision is its save revision, not necessarily the current
        session revision after finalization of the same pause.
        """
        with self._lock:
            try:
                item = self._store.get(self._selected) if self._selected else None
            except Exception:
                raise CheckpointOperationError('checkpoint_storage_failed') from None
            record = self._checkpoint_record(item)
            if record is None:
                return {'available': False}
            return {'available': True, 'revision': record.revision,
                    'session_revision': item.revision, 'saved_at': record.saved_at,
                    'checkpoint': record.checkpoint.to_public()}

    @contextmanager
    def resume_coordination(self, supplement, expected_revision, *, allow_missing_checkpoint=False,
                            expected_session_id=None):
        """Internal manager scope: caller already holds manager condition.

        Holds controller lock across preflight, cancellation and preparation.
        Validates text/acknowledgement BEFORE any cancellation. Never calls
        back into TaskManager; pause/confirmation cannot race this scope.
        """
        from server.session_resume import validate_supplement

        with self._lock:
            if self._resumes is None:
                raise ResumeOperationError('resume_unavailable')
            if expected_session_id is not None:
                from tasks.session_models import session_id
                session_id(expected_session_id)
                if self._selected != expected_session_id:
                    raise SessionConflict('Panel session is not selected.')
            if type(allow_missing_checkpoint) is not bool:
                raise ValueError('Expected a boolean acknowledgement.')
            item = self._resume_item(expected_revision)
            if self._tokens or (item.state != SessionState.PAUSED
                                and not self._resume_recovery_required):
                raise SessionConflict('Resume requires a fully paused session.')
            if self._resume_pending_revision is not None:
                raise SessionConflict('Resume preparation already exists.')
            validate_supplement(supplement)
            if self._checkpoint_record(item) is None and not allow_missing_checkpoint:
                raise SessionConflict('Missing checkpoint requires acknowledgement.')
            self._blocked = True
            try:
                links = self._store.list_task_links(item.id)
            except Exception:
                raise ResumeOperationError('resume_links_failed') from None
            if self._resume_coordination_active:
                raise SessionConflict('Resume coordination already exists.')
            self._resume_coordination_active = True
            try:
                yield links
            finally:
                self._resume_coordination_active = False

    def normalize_resume_internal(self, expected_revision):
        """Manager-only after task preflight, before cancellation/preparation.

        An uncertain ACTIVE commit is not authorization. Only a fresh explicit
        coordinated preparation may normalize it; failures keep recovery latched.
        No external operation or old confirmation is retried here.
        """
        with self._lock:
            if not self._resume_coordination_active:
                raise SessionConflict('Resume recovery requires TaskManager coordination.')
            item = self._resume_item(expected_revision)
            if not self._resume_recovery_required:
                return item.revision
            if self._tokens or not self._blocked:
                raise SessionConflict('Resume recovery requires closed admission.')
            self._invalidate_resume()
            self._checkpoint_current_revision = None
            try:
                self._store.request_pause(item.id, item.revision)
                self._finalize_pause()
                recovered = self._store.get(item.id)
                if recovered is None or recovered.state != SessionState.PAUSED:
                    raise ResumeOperationError('resume_recovery_failed')
            except Exception:
                raise ResumeOperationError('resume_recovery_failed') from None
            self._resume_recovery_required = False
            return recovered.revision

    def prepare_resume_internal(self, supplement, expected_revision, *, allow_missing_checkpoint=False):
        """DETACHED internal handshake foundation, NOT a panel/model endpoint.

        Future TaskManager coordinator MUST first cancel old pending tasks under
        manager -> controller lock ordering. This method alone cannot certify
        cancellation or panel authorization. Never expose it directly to model.
        Durable preparation remains PAUSED; no ordinary rebind opens the gate.
        """
        from server.session_resume import validate_supplement

        with self._lock:
            if self._resumes is None:
                raise ResumeOperationError('resume_unavailable')
            if type(allow_missing_checkpoint) is not bool:
                raise ValueError('Expected a boolean acknowledgement.')
            item = self._resume_item(expected_revision)
            if self._resume_recovery_required:
                raise SessionConflict('Resume recovery requires TaskManager coordination.')
            if item.state != SessionState.PAUSED or self._tokens:
                raise SessionConflict('Resume requires a fully paused session.')
            if self._resume_pending_revision is not None:
                raise SessionConflict('Resume preparation already exists.')
            text = validate_supplement(supplement)
            record = self._checkpoint_record(item)
            if record is None and not allow_missing_checkpoint:
                raise SessionConflict('Missing checkpoint requires acknowledgement.')
            self._blocked = True
            try:
                self._resumes.clear()
            except Exception:
                self._invalidate_resume()
                raise ResumeOperationError('resume_prepare_failed') from None
            try:
                prepared = self._store.prepare_resume(item.id, expected_revision)
            except SessionConflict:
                raise
            except Exception:
                self._checkpoint_current_revision = None
                self._invalidate_resume()
                raise ResumeOperationError('resume_prepare_failed') from None
            # The same checkpoint is still valid after a known PAUSED commit,
            # including when subsequent RAM publication fails.
            if record is not None:
                self._checkpoint_current_revision = prepared.revision
            try:
                binding = (self._autopilot.resume_prepared_internal(prepared.revision)
                           if self._autopilot is not None else None)
                if binding is None:
                    self._resumes.prepare(prepared.id, prepared.revision, text,
                                          record.checkpoint if record else None)
                else:
                    self._resumes.prepare(prepared.id, prepared.revision, text,
                                          record.checkpoint if record else None, contract_binding=binding)
            except Exception:
                self._invalidate_resume()
                raise ResumeOperationError('resume_prepare_failed') from None
            self._resume_pending_revision = prepared.revision
            return {'prepared': True, 'revision': prepared.revision,
                    'checkpoint_missing': record is None}

    def _resume_item(self, expected_revision):
        if self._selected is None:
            raise SessionConflict('No session is selected.')
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('Expected a positive revision.')
        try:
            item = self._store.get(self._selected)
        except Exception:
            self._blocked = True
            raise ResumeOperationError('resume_storage_failed') from None
        if item is None or item.lifecycle != 'open' or item.revision != expected_revision:
            raise SessionConflict('Session revision changed.')
        return item

    def get_resume_context(self):
        """Confidential read, no model session_id; only the current pending handshake."""
        with self._lock:
            if self._resumes is None or self._resume_pending_revision is None:
                return {'available': False}
            item = self._resume_item(self._resume_pending_revision)
            if item.state != SessionState.PAUSED or not self._blocked:
                return {'available': False}
            try:
                prepared = self._resumes.get()
            except Exception:
                raise ResumeOperationError('resume_ram_failed') from None
            if (prepared is None or prepared.session_id != item.id
                    or prepared.revision != item.revision or prepared.plan is not None):
                return {'available': False}
            response = {'available': True, **prepared.to_context()}
            if self._autopilot is not None:
                response['autopilot_control'] = self._autopilot.snapshot()
                response['autopilot_scope'] = copy.deepcopy(self._autopilot.resume_scope)
            return response

    def confirm_resume_plan(self, plan, expected_revision, preparation_token):
        """Confirm the user-prepared generation; cannot initiate a resume.

        RAM plan is recorded before durable ACTIVE commit. Any failure keeps
        admission closed and invalidates the pending handshake. No task is
        rescheduled here. Only the final assignment opens the gate.
        """
        from server.session_resume import validate_resume_plan

        with self._lock:
            if (self._resumes is None or self._resume_pending_revision is None
                    or self._resume_pending_revision != expected_revision):
                raise SessionConflict('No current resume preparation.')
            item = self._resume_item(expected_revision)
            if item.state != SessionState.PAUSED or self._tokens or not self._blocked:
                raise SessionConflict('Resume preparation is not paused.')
            validated = validate_resume_plan(plan)
            try:
                preparation = self._resumes.get()
                self._resumes.confirm(item.id, item.revision, preparation_token, validated)
                if self._autopilot is not None:
                    self._autopilot.confirm_internal(
                        preparation.contract_binding if preparation is not None else None)
            except (SessionConflict, ValueError):
                raise
            except Exception:
                self._invalidate_resume()
                if self._autopilot is not None:
                    self._autopilot.session_paused_internal()
                raise ResumeOperationError('resume_ram_failed') from None
            try:
                confirmed = self._store.confirm_resume(item.id, item.revision)
            except Exception:
                self._resume_recovery_required = True
                self._checkpoint_current_revision = None
                self._invalidate_resume()
                if self._autopilot is not None:
                    self._autopilot.stop('recovery_required', uncertain=True)
                raise ResumeOperationError('resume_confirm_failed') from None
            self._resume_pending_revision = None
            self._checkpoint_current_revision = None
            self._confirmed_plan_revision = confirmed.revision
            self._blocked = False
            return {'confirmed': True, 'revision': confirmed.revision, 'plan_size': validated.size}

    def get_confirmed_resume_plan(self):
        """Panel-only RAM data without token/supplement; successful commit only."""
        with self._lock:
            if (self._resumes is None or self._confirmed_plan_revision is None
                    or self._blocked or self._selected is None):
                return {'available': False}
            item = self._resume_item(self._confirmed_plan_revision)
            if item.state != SessionState.ACTIVE:
                return {'available': False}
            try:
                prepared = self._resumes.get()
            except Exception:
                raise ResumeOperationError('resume_ram_failed') from None
            if (prepared is None or prepared.session_id != item.id or prepared.plan is None
                    or prepared.revision + 1 != item.revision):
                return {'available': False}
            return {'available': True, 'session_id': item.id, 'revision': item.revision,
                    'plan': prepared.plan.text}

    def snapshot(self):
        """Detached structured state; no task outputs or checkpoint contents.

        execution_blocked is separate from durable state in case persistence
        failed. A selected session applies to ALL clients of this instance.
        """
        with self._lock:
            item = self._store.get(self._selected) if self._selected else None
            checkpoint_fields = {}
            if self._checkpoints is not None:
                checkpoint_fields = {
                    'checkpoint_available': True,
                    'checkpoint_content_available': self._checkpoint_record(item) is not None,
                }
            resume_fields = {}
            if self._resumes is not None:
                resume_fields = {
                    'resume_recovery_required': self._resume_recovery_required,
                    'resume_pending': bool(
                        item and item.state == SessionState.PAUSED and self._blocked
                        and self._resume_pending_revision == item.revision),
                }
            return {
                **checkpoint_fields,
                **resume_fields,
                'scope': 'instance',
                'session_id': self._selected,
                'execution_blocked': self._blocked or self._stopping,
                'running_actions': len(self._tokens),
                'state': ('ending' if item.lifecycle != 'open' else item.state.value) if item else None,
                'reason': item.reason if item else '',
                'revision': item.revision if item else None,
                'checkpoint_state': item.checkpoint_state.value if item else None,
            }
