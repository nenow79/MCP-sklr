"""M1 first adapter tranche: 9 cases after parametrization.

Missing persistent_supervisor injection is setup RED, not behavior RED.
No test execution claimed. Real TaskManager/WorkSession with fake supervisor;
no SDK/network/process. Full lifecycle/commit races and main API wiring remain
separate tests. This suite alone cannot authorize enabling persistent.
"""
from types import SimpleNamespace

import pytest

from external_mcp_persistent_manual_helpers import ManualRig
from tasks.models import TaskStatus


@pytest.fixture
def rig(tmp_path):
    instance = ManualRig(tmp_path / 'manual')
    try:
        yield instance
    finally:
        instance.close()


def test_discovery_routes_to_persistent_and_returns_server_generation(rig):
    task = rig.discover()
    assert task.decision == '' and not task.offer_trust
    assert rig.supervisor.discoveries == [(rig.session.id, rig.profile['id'])]
    assert rig.supervisor.calls == [] and rig.legacy.calls == []


def test_each_call_requires_distinct_allow_even_with_trust(rig):
    rig.trust()
    rig.discover()
    ids = []
    for count in range(2):
        response = rig.call()
        assert response['success'], response
        tid = response['data']['task_id']
        ids.append(tid)
        assert rig.manager.get(tid).status == TaskStatus.AWAITING_APPROVAL
        assert len(rig.supervisor.calls) == count
        assert rig.controller.snapshot()['running_actions'] == 0
        rig.manager.resolve_approval(tid, 'allow')
        done = rig.manager.wait(tid, 5)
        assert done.status == TaskStatus.DONE, done.error
        assert len(rig.supervisor.calls) == count + 1
    assert ids[0] != ids[1]
    assert rig.legacy.calls == []


def test_denial_never_invokes_supervisor_call(rig):
    rig.discover()
    response = rig.call()
    assert response['success'], response
    rig.manager.resolve_approval(response['data']['task_id'], 'deny')
    assert rig.manager.wait(response['data']['task_id'], 5).is_terminal
    assert rig.supervisor.calls == [] and rig.legacy.calls == []


@pytest.mark.parametrize('generation', [None, 'foreign-generation'])
def test_missing_or_foreign_generation_refused_before_submit(rig, generation):
    rig.discover()
    response = rig.tools.call_tool('fake', 'lookup', {},
                                  expected_runtime_generation=generation)
    assert response['success'] is False, response
    assert 'task_id' not in response.get('data', {})
    assert rig.supervisor.calls == [] and rig.legacy.calls == []


def test_generation_lost_during_approval_does_not_reconnect(rig):
    rig.discover()
    response = rig.call()
    assert response['success'], response
    tid = response['data']['task_id']
    rig.supervisor.generation = 'replacement-generation'
    rig.manager.resolve_approval(tid, 'allow')
    assert rig.manager.wait(tid, 5).status == TaskStatus.FAILED
    assert len(rig.supervisor.discoveries) == 1
    assert rig.supervisor.calls == [] and rig.legacy.calls == []


def test_profile_disabled_during_approval_refuses_call(rig):
    rig.discover()
    response = rig.call()
    assert response['success'], response
    rig.registry.set_enabled(rig.profile['id'], False, rig.registry.list()['revision'])
    rig.manager.resolve_approval(response['data']['task_id'], 'allow')
    assert rig.manager.wait(response['data']['task_id'], 5).status == TaskStatus.FAILED
    assert rig.supervisor.calls == [] and rig.legacy.calls == []


def test_missing_selected_session_refuses_without_discovery(tmp_path):
    instance = ManualRig(tmp_path / 'unselected', selected=False)
    try:
        response = instance.tools.list_tools('fake')
        assert response['success'] is False, response
        assert instance.supervisor.discoveries == []
        assert instance.legacy.calls == []
    finally:
        instance.close()


def test_engaged_delegation_blocks_before_submit_even_without_contextvar(rig):
    # Control-state double only. No real delegation/accounting is accepted.
    # The adapter must reject before submit, so no autopilot executor is needed.
    rig.controller._autopilot = SimpleNamespace(engaged=True)
    try:
        response = rig.tools.list_tools('fake')
        assert response['success'] is False, response
        assert 'task_id' not in response.get('data', {})
        assert rig.supervisor.discoveries == [] and rig.legacy.calls == []
    finally:
        rig.controller._autopilot = None
