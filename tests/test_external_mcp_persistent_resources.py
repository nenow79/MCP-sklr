"""P3c-C1: 16 cases after parametrization, partial contract P05/P06/P07.

Missing ResourceDispatchAdmission is setup RED, not executed behavior assertions.
Tmp config/SQLite, real registry/limiter/control, fake transport/session/scope.
No production routing, SDK/network/process or actual autopilot accounting.
"""
import copy
import threading
from contextlib import contextmanager

import pytest

from external_mcp.limits import ExternalError
from external_mcp_persistent_admission_helpers import Scope, until, wait
from external_mcp_persistent_dispatch_helpers import Gate, refusal
from external_mcp_persistent_resources_helpers import DIGEST, ResourceRig, resource_type
from tasks.manager import ExternalApprovalRequired
from tasks.models import TaskStatus
from tasks.volatile_results import VolatileResult


@pytest.fixture
def rig(tmp_path):
    instance = ResourceRig(tmp_path / 'resources', resource_type())
    try:
        yield instance
    finally:
        instance.close()


def ready(rig, approved=True):
    attempt = rig.start(approved=approved)
    rig.discover(attempt)
    return attempt


def assert_refused(rig, result, admission, scope):
    refusal(result,admission,scope)
    assert rig.backend.calls == [] and rig.commits == [] and rig.hits() == 0


@pytest.mark.parametrize('change',['connection','mode','disable','delete','trust'])
def test_resource_changed_after_validation_before_commit_refuses(rig,change):
    attempt = ready(rig,approved=False)
    entered,release = threading.Event(),attempt.barrier()
    def prepared():
        entered.set()
        wait(release,'resource preparation')
    admission,scope = rig.resource_admission(attempt,before=prepared)
    result = rig.call(attempt,admission)
    wait(entered,'prepared operation')
    rig.mutate(change)
    release.set()
    assert_refused(rig,result,admission,scope)
    rig.finish_attempt(attempt)
    rig.drained()


@pytest.mark.parametrize('mismatch',['name','digest'])
def test_name_schema_pins_are_not_rewritten(rig,mismatch):
    attempt = ready(rig)
    admission,scope = rig.resource_admission(attempt)
    pin = copy.deepcopy(rig.pin)
    def operation(event):
        # Call the real boundary with a different catalog claim. No fake
        # validation or helper-owned resource policy decides the outcome.
        with admission.dispatch_boundary('other' if mismatch == 'name' else 'lookup',
                'b'*64 if mismatch == 'digest' else DIGEST) as commit:
            commit(lambda: None)
    result = attempt.command(operation)
    assert_refused(rig,result,admission,scope)
    assert rig.pin == pin
    rig.finish_attempt(attempt)


@pytest.mark.parametrize('approved',[True,False])
def test_manual_consent_skips_only_trust_and_limiter(rig,approved):
    rig.registry.revoke_trust(rig.profile['id'],rig.registry.list()['revision'])
    rig.refresh_pin()
    attempt = ready(rig,approved=approved)
    admission,scope = rig.resource_admission(attempt)
    result = rig.call(attempt,admission)
    if approved:
        assert result.result(timeout=5)['result']['isError'] is False
        assert admission.admitted and scope.dispatched and not scope.uncertain
        assert len(rig.backend.calls) == 1 and rig.hits() == 0
    else:
        assert_refused(rig,result,admission,scope)
    rig.finish_attempt(attempt)


def test_panel_unavailable_refuses_even_manual_call(rig):
    attempt = ready(rig)
    admission,scope = rig.resource_admission(attempt)
    rig.panel = False
    assert_refused(rig,rig.call(attempt,admission),admission,scope)
    rig.finish_attempt(attempt)


def test_thirty_calls_then_refusal_without_limiter_reset_on_new_runtime(rig):
    attempt = ready(rig,approved=False)
    for _ in range(30):
        admission,scope = rig.resource_admission(attempt)
        assert rig.call(attempt,admission).result(timeout=5)['result']['isError'] is False
        assert admission.admitted and scope.dispatched and not scope.uncertain
    assert rig.hits() == 30 and len(rig.backend.calls) == 30
    blocked,scope = rig.resource_admission(attempt)
    with pytest.raises(ExternalError) as exc:
        rig.call(attempt,blocked).result(timeout=5)
    assert exc.value.code == 'approval_required'
    assert not blocked.admitted and not scope.dispatched and not scope.uncertain
    assert len(rig.backend.calls) == 30 and rig.hits() == 30
    rig.invalidate('idle')
    until(lambda: rig.backend.closed.is_set(),'first runtime cleanup')
    old = rig.generation
    rig.discover(attempt)
    assert rig.generation != old
    again,scope = rig.resource_admission(attempt)
    with pytest.raises(ExternalError) as exc:
        rig.call(attempt,again).result(timeout=5)
    assert exc.value.code == 'approval_required'
    assert rig.backend.calls == [] and not again.admitted and not scope.uncertain
    assert rig.hits() == 30
    rig.finish_attempt(attempt)


def test_rate_refusal_returns_same_task_to_approval_then_one_manual_call(rig):
    discovery = ready(rig)
    rig.finish_attempt(discovery)
    for _ in range(30):
        assert rig.limiter.allow(rig.profile['id'])
    scopes,admissions,signals = [],[],[]
    entered = threading.Event()
    def executor(event):
        scope = Scope()
        admission = rig.admission_type(rig.manager,event,scope)
        scopes.append(scope)
        admissions.append(admission)
        entered.set()
        try:
            rig.raw_call(event,admission)
        except ExternalError as exc:
            admission.mark_uncertain_if_admitted()
            if exc.code == 'approval_required' and not admission.admitted:
                signals.append(exc.code)
                raise ExternalApprovalRequired() from None
            raise
        return VolatileResult({'content':'local fake result'})
    task = rig.manager.submit({'kind':'external_mcp','sensitive':True,
        'offer_trust':False,'timeout':60},executor,False,allow_external_reapproval=True)
    wait(entered,'authorized executor')
    until(lambda: rig.manager.get(task.id).status == TaskStatus.AWAITING_APPROVAL,'same-task reapproval')
    assert signals == ['approval_required'] and rig.backend.calls == []
    assert not admissions[0].admitted and not scopes[0].dispatched and not scopes[0].uncertain
    assert rig.manager.get(task.id).id == task.id
    rig.approval.resolve(task.id,'allow')
    assert rig.manager.wait(task.id,5).status == TaskStatus.DONE
    assert len(admissions) == 2 and admissions[1].admitted and scopes[1].dispatched
    assert not scopes[1].uncertain and len(rig.backend.calls) == 1 and rig.hits() == 30
    rig.drained()


@pytest.mark.parametrize('order',['change_first','commit_first'])
def test_config_change_and_commit_have_controlled_order(rig,order):
    attempt = ready(rig,approved=False)
    entered,release = threading.Event(),attempt.barrier()
    change_started,change_finished = threading.Event(),threading.Event()
    def barrier():
        entered.set()
        wait(release,'ordered commit')
    admission,scope = rig.resource_admission(attempt,
        before=barrier if order == 'change_first' else None,
        before_commit=barrier if order == 'commit_first' else None)
    result = rig.call(attempt,admission)
    wait(entered,'ordering barrier')
    def change():
        change_started.set()
        # Nonblocking acquisition gives a deterministic observation of config
        # exclusion, without treating a short sleep as a race proof.
        acquired = rig.config.lock.acquire(blocking=False)
        if order == 'commit_first':
            assert not acquired,'configuration unprotected before commit'
        else:
            assert acquired,'config held during preparation'
        if acquired:
            rig.config.lock.release()
        rig.mutate('disable')
        change_finished.set()
    thread,future = rig.thread(change)
    wait(change_started,'configuration contender')
    if order == 'change_first':
        rig.join(thread,future)
        release.set()
        assert_refused(rig,result,admission,scope)
    else:
        assert not change_finished.is_set()
        release.set()
        assert result.result(timeout=5)['result']['isError'] is False
        rig.join(thread,future)
        assert admission.admitted and scope.dispatched and not scope.uncertain
        assert rig.commits == [attempt.task.id] and rig.hits() == 1
    rig.finish_attempt(attempt)


def test_lost_generation_before_commit_does_not_charge_limiter(rig):
    attempt = ready(rig,approved=False)
    entered,release = threading.Event(),attempt.barrier()
    def boundary():
        entered.set()
        wait(release,'runtime loss boundary')
    admission,scope = rig.resource_admission(attempt,before_commit=boundary)
    result = rig.call(attempt,admission)
    wait(entered,'protected resource boundary')
    # Invalidation is a supervisor leaf operation, not a controller callback.
    rig.invalidate('end')
    release.set()
    assert_refused(rig,result,admission,scope)
    rig.finish_attempt(attempt)


def test_config_and_session_controls_do_not_wait_for_business_io(rig):
    attempt = ready(rig,approved=False)
    admission,scope = rig.resource_admission(attempt)
    rig.backend.call = Gate(blocked=True)
    result = rig.call(attempt,admission)
    wait(rig.backend.call.entered,'business I/O')
    def controls():
        assert rig.config.lock.acquire(blocking=False),'config spans business I/O'
        rig.config.lock.release()
        rig.mutate('disable')
        state = rig.lifecycle('pause')
        assert not rig.backend.call.release.is_set()
        return state
    thread,future = rig.thread(controls)
    assert rig.join(thread,future)['execution_blocked']
    assert not result.done()
    rig.backend.call.release.set()
    assert result.result(timeout=5)['result']['isError'] is False
    assert admission.admitted and scope.dispatched and not scope.uncertain
    assert rig.hits() == 1 and len(rig.backend.calls) == 1
    rig.finish_attempt(attempt)
    rig.drained()
