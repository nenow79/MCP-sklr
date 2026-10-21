"""A2b4 panel decisions through the real optional execution composition."""
import json
import threading

import pytest

from autopilot.controller import AutopilotBlocked
from autopilot_fixtures import environment
from server.autopilot_admin import AutopilotAdmin
from server.session_tools import wrap_session_tool
from server import edit_tools
from tasks.session_models import SessionConflict
from tasks.session_controller import SessionBlocked


@pytest.fixture
def env(tmp_path):
    with environment(tmp_path) as e:
        e.admin = AutopilotAdmin(e.runtime)
        e.pause()
        yield e


def identity(e):
    state = e.admin.status()
    return {key: state[key] for key in ('session_id', 'revision', 'decision_revision')}


def draft(e, **changes):
    body = dict(identity(e), goal='Create a test document.', plan='Create only approved files.',
                limits={'duration_s': 1800, 'max_operations': 100, 'max_running': 1},
                policy={'version': 1, 'rules': [dict(id='write', kind='files', root='root:app',
                    tools=['create_file'], scope={'kind': 'tree', 'path': '.'}, overwrite=False)]})
    body.update(changes)
    return body


def approve(e):
    body = draft(e)
    preview = e.admin.preview(body)
    e.admin.action('approve', dict(identity(e), confirmed=True, preview_id=preview['preview_id']))
    return preview


def test_preview_has_server_bindings_and_cannot_open_gate(env):
    preview = env.admin.preview(draft(env))
    assert preview['bindings'][0]['resources'][0]['path'] == env.app.as_posix()
    assert preview['limits']['duration_s'] == 1800
    assert env.sessions.snapshot()['execution_blocked']
    assert not env.autopilot.engaged
    assert env.audit.history() == ()


def test_preview_response_cannot_mutate_the_prepared_decision(env):
    preview = env.admin.preview(draft(env))
    preview['policy']['rules'][0]['scope']['path'] = 'other'
    preview['limits']['max_operations'] = 500
    env.admin.action('approve', dict(identity(env), confirmed=True, preview_id=preview['preview_id']))
    approved = env.admin.status()
    assert approved['policy']['rules'][0]['scope']['path'] == '.'
    assert approved['limits']['max_operations'] == 100


def test_panel_approval_handshake_and_real_write_without_allow(env):
    approve(env)
    assert env.autopilot.snapshot()['state'] == 'prepared'
    assert env.sessions.snapshot()['execution_blocked']
    env.manager.prepare_session_resume('', env.sessions.snapshot()['revision'], allow_missing_checkpoint=True)
    context = env.sessions.get_resume_context()
    assert context['autopilot_scope']['rules'][0]['resource'] == 'root:app'
    context['autopilot_scope']['rules'].clear()
    assert env.sessions.get_resume_context()['autopilot_scope']['rules']
    env.sessions.confirm_resume_plan('Create approved files.', context['revision'], context['preparation_token'])
    def create_file(filepath, content, overwrite=False, root=None):
        return edit_tools.create_file_impl(env.roots, env.manager, env.transaction, filepath, content, overwrite, root)
    result = wrap_session_tool(create_file, env.sessions)('new.txt', 'hello', root='app')
    task = env.settle(result)
    assert task.status.value == 'done'
    assert (env.app / 'new.txt').read_text() == 'hello'
    assert env.approvals == []
    assert env.autopilot.snapshot()['used'] == 1


def test_preview_consumed_and_stale_tab_cannot_override(env):
    body = draft(env)
    preview = env.admin.preview(body)
    decision = dict(identity(env), confirmed=True, preview_id=preview['preview_id'])
    env.admin.action('approve', decision)
    with pytest.raises(SessionConflict):
        env.admin.action('approve', decision)
    assert env.autopilot.snapshot()['state'] == 'prepared'


def test_preview_replaced_by_other_tab_and_expiry(env, monkeypatch):
    first = env.admin.preview(draft(env))
    env.admin.preview(draft(env, goal='Another accepted goal.'))
    with pytest.raises(SessionConflict):
        env.admin.action('approve', dict(identity(env), confirmed=True, preview_id=first['preview_id']))
    latest = env.admin.preview(draft(env))
    expired_at = env.admin._draft['expires'] + 1
    monkeypatch.setattr('server.autopilot_admin.time.monotonic', lambda: expired_at)
    with pytest.raises(SessionConflict):
        env.admin.action('approve', dict(identity(env), confirmed=True, preview_id=latest['preview_id']))
    assert not env.autopilot.engaged


def test_resource_change_and_restore_requires_new_preview(env):
    preview = env.admin.preview(draft(env))
    env.store.update(lambda data: data.update(unrelated_setting=True))
    env.store.update(lambda data: data.pop('unrelated_setting'))
    with pytest.raises(SessionConflict):
        env.admin.action('approve', dict(identity(env), confirmed=True, preview_id=preview['preview_id']))
    assert not env.autopilot.engaged


@pytest.mark.parametrize('changes', [
    {'limits': {'duration_s': 7201}}, {'limits': {'max_operations': True}},
    {'goal': 'PASSWORD=private-value'}, {'plan': 'https://user:private@example.test'},
    {'policy': {'version': 1, 'rules': []}},
    {'policy': {'version': 1, 'rules': [dict(id='x', kind='files', root={'path': '/fake'},
        tools=['create_file'], scope={'kind': 'tree', 'path': '.'}, overwrite=False)]}},
])
def test_invalid_or_secret_delegation_is_rejected_without_journal(env, changes):
    with pytest.raises(ValueError):
        env.admin.preview(draft(env, **changes))
    assert env.audit.history() == () and not env.autopilot.engaged


def test_nested_secret_rejected(env):
    body = draft(env)
    body['policy']['rules'][0]['args'] = ['hello\nPASSWORD=private-value']
    with pytest.raises(ValueError):
        env.admin.preview(body)
    assert env.admin._draft is None


def test_pause_requires_panel_reconfirmation_and_preserves_budget(env):
    approve(env)
    env.resume()
    env.pause()
    before = env.autopilot.snapshot()
    with pytest.raises(AutopilotBlocked):
        env.autopilot.resume_prepared_internal(env.sessions.snapshot()['revision'] + 1)
    env.admin.action('reconfirm', dict(identity(env), confirmed=True))
    after = env.autopilot.snapshot()
    assert after['used'] == before['used'] and after['remaining_s'] <= before['remaining_s']
    env.resume()
    assert env.autopilot.snapshot()['state'] == 'active'


def test_revoke_invalidates_pending_handshake_and_manual_is_explicit(env):
    approve(env)
    env.manager.prepare_session_resume('', env.sessions.snapshot()['revision'], allow_missing_checkpoint=True)
    context = env.sessions.get_resume_context()
    env.admin.action('revoke', dict(identity(env), confirmed=True))
    with pytest.raises(SessionConflict):
        env.sessions.confirm_resume_plan('Old plan.', context['revision'], context['preparation_token'])
    assert env.sessions.snapshot()['execution_blocked']
    env.admin.action('manual', dict(identity(env), confirmed=True))
    assert not env.autopilot.engaged
    assert env.sessions.snapshot()['execution_blocked']
    env.resume()
    assert not env.sessions.snapshot()['execution_blocked']


def test_revoke_while_action_is_started_preserves_result_and_closes_gate(env):
    approve(env)
    env.resume()
    token = env.sessions.begin_action()
    env.admin.action('revoke', dict(identity(env), confirmed=True))
    assert env.sessions.snapshot()['state'] == 'pause_requested'
    env.sessions.finish_action(token)
    assert env.sessions.snapshot()['state'] == 'paused'
    assert env.autopilot.snapshot()['state'] == 'revoked'


def test_decision_race_only_one_tab_can_commit(env):
    preview = env.admin.preview(draft(env))
    body = dict(identity(env), confirmed=True, preview_id=preview['preview_id'])
    barrier = threading.Barrier(3)
    results = []
    def decide():
        barrier.wait()
        try:
            env.admin.action('approve', body)
            results.append('approved')
        except SessionConflict:
            results.append('stale')
    workers = [threading.Thread(target=decide) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=3)
    assert sorted(results) == ['approved', 'stale']


def test_storage_failure_closes_gate_and_does_not_persist_private_policy(env, monkeypatch):
    preview = env.admin.preview(draft(env, goal='PRIVATE-GOAL', plan='PRIVATE-PLAN'))
    def fail(*args):
        raise RuntimeError('PRIVATE-STORAGE-DETAIL')
    monkeypatch.setattr(env.audit, 'append', fail)
    with pytest.raises(AutopilotBlocked):
        env.admin.action('approve', dict(identity(env), confirmed=True, preview_id=preview['preview_id']))
    assert env.sessions.snapshot()['execution_blocked']
    assert env.autopilot.snapshot()['state'] == 'recovery_required'
    assert 'PRIVATE-GOAL' not in json.dumps(env.audit.history())


def test_explicit_resolution_never_retries_or_opens_gate(env):
    (env.app / 'a.txt').write_text('hello')
    body = draft(env)
    body['policy']['rules'][0]['tools'] = ['read_file']
    preview = env.admin.preview(body)
    env.admin.action('approve', dict(identity(env), confirmed=True, preview_id=preview['preview_id']))
    env.resume()
    action = env.sessions.begin_action()
    ticket = env.autopilot.reserve(lambda binding: env.runtime.build(
        binding, 'read_file', {'filepath': 'a.txt', 'root': 'app'})[:2])
    with env.autopilot.coordination():
        env.autopilot.start_internal(ticket)
    env.autopilot.finish(ticket, 'uncertain')
    env.sessions.finish_action(action)
    with pytest.raises(AutopilotBlocked):
        env.admin.action('manual', dict(identity(env), confirmed=True))
    env.admin.action('resolve', dict(identity(env), confirmed=True, operation_id=ticket.operation_id))
    assert env.audit.unresolved() == ()
    assert env.sessions.snapshot()['execution_blocked']
    assert env.autopilot.snapshot()['used'] == 1
    assert env.manager.list() == []
    env.admin.action('manual', dict(identity(env), confirmed=True))
    env.resume()
    assert not env.sessions.snapshot()['execution_blocked']


def test_shutdown_closes_direct_admission_even_without_delegation(env):
    env.resume()
    env.manager.shutdown()
    with pytest.raises(SessionBlocked):
        env.sessions.begin_action()
    assert env.sessions.snapshot()['execution_blocked']
    with pytest.raises(SessionConflict):
        env.admin.preview(draft(env))


def test_shutdown_journal_failure_still_stops_workers_and_direct_admissions(env, monkeypatch):
    approve(env)
    env.resume()
    def fail(*args):
        raise RuntimeError('PRIVATE-AUDIT-PATH')
    monkeypatch.setattr(env.audit, 'append', fail)
    env.manager.shutdown()
    assert env.manager._stopped
    with pytest.raises(SessionBlocked):
        env.sessions.begin_action()
    assert env.autopilot.snapshot()['execution_blocked']


def test_mcp_schema_is_pinned_by_server_and_requires_discovery(env):
    from external_mcp.client import schema_digest
    registry = env.external.registry
    class NoNetwork:
        def fetch_catalog(self, *args, **kwargs):
            pytest.fail('Panel must not discover a tool while creating authority.')
        def call_tool(self, *args, **kwargs):
            pytest.fail('Panel must not execute a tool while creating authority.')
    env.external.client = NoNetwork()
    registry.add(dict(alias='demo', transport='http', url='https://example.test/mcp', enabled=True),
                 registry.list()['revision'])
    profile = registry.resolve('demo')['profile']
    digest = schema_digest({'type': 'object'})
    registry.set_trust(profile['id'], {'lookup': digest}, registry.list()['revision'])
    env.external.catalogs[profile['id']] = dict(tools=[dict(name='lookup', schemaSha256=digest)],
        revision=registry.list()['revision'], expires=env.external.clock() + 300)
    body = draft(env)
    body['policy']['rules'] = [dict(id='call', kind='mcp_call', tools=['call_external_mcp_tool'],
        profile='profile:demo', tool_name='lookup', arguments={'type': 'literal', 'value': {}}, max_timeout_s=60)]
    with pytest.raises(ValueError, match='discovery_rule_required'):
        env.admin.preview(body)
    body['policy']['rules'].append(dict(id='discovery', kind='mcp_discovery',
        tools=['list_external_mcp_tools'], profile='profile:demo', max_timeout_s=60))
    preview = env.admin.preview(body)
    assert preview['bindings'][0]['schema_digest'] == digest
    registry.revoke_trust(profile['id'], registry.list()['revision'])
    with pytest.raises(ValueError):
        env.admin.action('approve', dict(identity(env), confirmed=True, preview_id=preview['preview_id']))
    assert not env.autopilot.engaged
