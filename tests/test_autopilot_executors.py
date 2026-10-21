"""Skills/MCP/knowledge integration with fake executors, no actual process/network."""
import copy
from dataclasses import asdict
import json

import pytest

from autopilot.context import recheck_current
from autopilot_fixtures import environment
from external_mcp.client import schema_digest
from external_mcp.limits import ExternalError
from server import knowledge_tools, task_tools
from server.session_tools import wrap_session_tool
from skills_engine.trust import compute_skill_hash
from tasks.models import TaskStatus

SCHEMA = {'type': 'object', 'properties': {'q': {'type': 'string'}},
          'required': ['q'], 'additionalProperties': False}
DIGEST = schema_digest(SCHEMA)
TOOL = {'name': 'lookup', 'description': 'Lookup', 'inputSchema': SCHEMA, 'schemaSha256': DIGEST}


@pytest.fixture
def env(tmp_path):
    with environment(tmp_path) as e:
        yield e


class Client:
    def __init__(self):
        self.calls = []
        self.discoveries = 0
        self.before_guard = None
        self.failure = None

    def fetch_catalog(self, profile, **kwargs):
        self.discoveries += 1
        return {'tools': [copy.deepcopy(TOOL)]}

    def call_tool(self, profile, name, arguments, *, before_dispatch, **kwargs):
        if self.before_guard:
            self.before_guard()
        before_dispatch(name, DIGEST)
        self.calls.append(copy.deepcopy(arguments))
        if self.failure:
            raise self.failure
        return {'result': {'content': [{'type': 'text', 'text': 'PRIVATE_MCP_RESULT'}], 'isError': False},
                'schemaSha256': DIGEST}


def mcp_setup(e, *, discovery=True):
    registry = e.external.registry
    registry.add({'alias': 'demo', 'transport': 'http', 'url': 'https://example.test/mcp', 'enabled': True},
                 registry.list()['revision'])
    profile = registry.resolve('demo')['profile']
    registry.set_trust(profile['id'], {'lookup': DIGEST}, registry.list()['revision'])
    profile = registry.resolve('demo')['profile']
    e.external.catalogs[profile['id']] = {'tools': [copy.deepcopy(TOOL)],
        'revision': registry.list()['revision'], 'expires': e.external.clock() + 300}
    client = Client()
    e.external.client = client
    ref = asdict(e.runtime.mcp.reference(profile))
    rules = [{'id': 'call', 'kind': 'mcp_call', 'tools': ['call_external_mcp_tool'], 'profile': ref,
        'tool_name': 'lookup', 'schema_digest': DIGEST, 'max_timeout_s': 60,
        'arguments': {'type': 'object', 'properties': {'q': {'type': 'string', 'max_length': 30}},
                      'required': ['q']}}]
    if discovery:
        rules.append({'id': 'discovery', 'kind': 'mcp_discovery', 'tools': ['list_external_mcp_tools'],
                      'profile': ref, 'max_timeout_s': 60})
    e.approve(rules)
    return client, profile


def mcp_call(e, arguments=None):
    def call_external_mcp_tool(alias, tool_name, arguments, timeout=60):
        return e.external.call_tool(alias, tool_name, arguments, timeout)
    return wrap_session_tool(call_external_mcp_tool, e.sessions)(
        'demo', 'lookup', arguments if arguments is not None else {'q': 'PRIVATE_MCP_ARGUMENT'})


def test_mcp_call_and_embedded_discovery_use_one_charge_and_keep_private_channel(env):
    client, profile = mcp_setup(env)
    task = env.settle(mcp_call(env))
    assert task.status == TaskStatus.DONE and len(client.calls) == 1
    assert env.autopilot.snapshot()['used'] == 1 and env.approvals == []
    result = env.manager.get_external_result(task.id)
    assert result['state'] == 'available'
    assert 'PRIVATE_MCP_RESULT' in json.dumps(result)
    persisted = json.dumps(task.to_dict()) + json.dumps(env.audit.history())
    assert 'PRIVATE_MCP_ARGUMENT' not in persisted and 'PRIVATE_MCP_RESULT' not in persisted
    for path in env.app.parent.glob('*.db*'):
        assert b'PRIVATE_MCP_ARGUMENT' not in path.read_bytes()
        assert b'PRIVATE_MCP_RESULT' not in path.read_bytes()


def test_mcp_call_without_discovery_authority_stops_before_transport(env):
    client, _ = mcp_setup(env, discovery=False)
    result = mcp_call(env)
    assert not result['success'] and client.calls == [] and env.manager.list() == []
    assert env.autopilot.snapshot()['used'] == 0


def test_trust_revoked_during_discovery_blocks_tools_call_without_approval_fallback(env):
    client, profile = mcp_setup(env)
    registry = env.external.registry
    client.before_guard = lambda: registry.revoke_trust(profile['id'], registry.list()['revision'])
    task = env.settle(mcp_call(env))
    assert task.status == TaskStatus.FAILED and client.calls == []
    assert env.approvals == [] and env.sessions.snapshot()['execution_blocked']
    assert env.autopilot.snapshot()['used'] == 1


def test_failed_task_closes_gate_before_releasing_running_slot(env, monkeypatch):
    client, profile = mcp_setup(env)
    registry = env.external.registry
    client.before_guard = lambda: registry.revoke_trust(profile['id'], registry.list()['revision'])
    original = env.autopilot.finish
    observed = []
    def finish(ticket, code='ok'):
        if code == 'failed':
            observed.append(env.sessions.snapshot()['execution_blocked'])
        return original(ticket, code)
    monkeypatch.setattr(env.autopilot, 'finish', finish)
    task = env.settle(mcp_call(env))
    assert task.status == TaskStatus.FAILED and client.calls == []
    assert observed == [True]


def test_limiter_suspends_before_call_without_returning_to_awaiting_approval(env):
    client, _ = mcp_setup(env)
    class Limiter:
        def allow(self, profile_id):
            return False
    env.external.limiter = Limiter()
    task = env.settle(mcp_call(env))
    assert task.status == TaskStatus.FAILED and client.calls == [] and env.approvals == []
    assert env.sessions.snapshot()['execution_blocked']
    assert env.autopilot.snapshot()['state'] == 'suspended'


def test_uncertain_remote_timeout_has_one_call_and_no_automatic_retry(env):
    client, _ = mcp_setup(env)
    client.failure = ExternalError('call_timeout_uncertain')
    task = env.settle(mcp_call(env))
    assert task.status == TaskStatus.FAILED and len(client.calls) == 1
    assert env.autopilot.snapshot()['state'] == 'recovery_required'
    assert len(env.audit.unresolved()) == 1
    assert not mcp_call(env)['success'] and len(client.calls) == 1


def test_mcp_argument_mismatch_is_rejected_before_task_or_transport(env):
    client, _ = mcp_setup(env)
    result = mcp_call(env, {'q': 'x', 'unexpected': True})
    assert not result['success'] and result['code'] == 'arguments_mismatch'
    assert env.manager.list() == [] and client.calls == []


def test_mutating_original_arguments_after_reservation_cannot_change_dispatch(env, monkeypatch):
    client, _ = mcp_setup(env)
    arguments = {'q': 'approved'}
    original = env.autopilot.reserve
    def reserve(refresh):
        ticket = original(refresh)
        arguments['q'] = 'changed-outside-the-copy'
        return ticket
    monkeypatch.setattr(env.autopilot, 'reserve', reserve)
    task = env.settle(mcp_call(env, arguments))
    assert task.status == TaskStatus.DONE and client.calls == [{'q': 'approved'}]


def make_skill(e, *, confirm='false', trusted=True):
    folder = e.central / 'demo'
    (folder / 'scripts').mkdir(parents=True)
    (folder / 'SKILL.md').write_text(
        '---\nname: demo\ndescription: Example\nmetadata:\n  vibecoder-confirm: "' + confirm +
        '"\n---\nInstructions.\n', encoding='utf-8')
    (folder / 'scripts' / 'run.py').write_text('print("example")\n', encoding='utf-8')
    e.skills.rescan()
    if trusted:
        e.trust.trust('demo', compute_skill_hash(folder))
    record = e.skills.get('demo')
    ref, access = e.runtime.skills.reference(record)
    rule = {'id': 'skill', 'kind': 'skill_run', 'tools': ['run_skill_script'], 'skill': asdict(ref),
            'script': 'scripts/run.py', 'args': ['allowed'], 'max_timeout_s': 60}
    return folder, rule


def skill_call(e, args=None):
    def run_skill_script(name, script, args=None, timeout=60, wait=0):
        return task_tools.run_skill_script_impl(e.skills, e.store.data, e.trust, e.manager,
                                               name, script, args, timeout, wait)
    return wrap_session_tool(run_skill_script, e.sessions)(
        'demo', 'scripts/run.py', ['allowed'] if args is None else args)


def test_central_trusted_skill_is_rechecked_and_charged_once(env, monkeypatch):
    _, rule = make_skill(env)
    calls = []
    def run(command, cwd, timeout, environment, event):
        recheck_current()
        calls.append(command)
        assert not env.sessions._lock._is_owned()
        assert not env.autopilot._lock._is_owned()
        return {'exit_code': 0, 'stdout': 'done'}
    monkeypatch.setattr(task_tools, 'run_command', run)
    env.approve([rule])
    task = env.settle(skill_call(env))
    assert task.status == TaskStatus.DONE and len(calls) == 1 and env.approvals == []
    assert env.autopilot.snapshot()['used'] == 1


@pytest.mark.parametrize('confirm,trusted', [('true', True), ('false', False)])
def test_skill_that_needs_manual_consent_stops_without_creating_approval_task(env, confirm, trusted):
    _, rule = make_skill(env, confirm=confirm, trusted=trusted)
    env.approve([rule])
    result = skill_call(env)
    assert not result['success'] and env.manager.list() == [] and env.approvals == []
    assert env.autopilot.snapshot()['used'] == 0


def test_changed_skill_in_queue_does_not_spawn_or_consume(env, monkeypatch):
    folder, rule = make_skill(env)
    env.approve([rule])
    monkeypatch.setattr(env.manager, '_ensure_workers', lambda: None)
    calls = []
    monkeypatch.setattr(task_tools, 'run_command', lambda *args: calls.append(args))
    response = skill_call(env)
    (folder / 'scripts' / 'run.py').write_text('print("changed")\n', encoding='utf-8')
    env.manager._run(response['data']['task_id'])
    assert calls == [] and env.autopilot.snapshot()['used'] == 0
    assert env.sessions.snapshot()['execution_blocked']


def test_skill_arguments_are_exact_and_do_not_fall_back_to_trust(env):
    _, rule = make_skill(env)
    env.approve([rule])
    result = skill_call(env, ['different'])
    assert not result['success'] and env.manager.list() == []


def knowledge_setup(e, *, scope='.'):
    docs = e.app / 'docs'
    docs.mkdir()
    (docs / 'note.md').write_text('# Note\nPRIVATE_KNOWLEDGE_BODY\n', encoding='utf-8')
    e.knowledge.bundles()
    state = next(state for state in e.knowledge._states.values() if state.entry.root == 'app')
    ref = e.runtime.knowledge.reference(state)
    rule = {'id': 'knowledge', 'kind': 'knowledge', 'tools': ['knowledge_search', 'knowledge_get'],
            'bundle': asdict(ref), 'scope': {'kind': 'tree', 'path': scope}}
    return state.id, rule


def test_knowledge_refresh_does_not_charge_a_second_operation(env):
    bundle_id, rule = knowledge_setup(env)
    env.approve([rule])
    def knowledge_search(query='', bundle_id=None, scope='all', refresh='force'):
        return knowledge_tools.knowledge_search_impl(env.knowledge, query, bundle_id, scope, refresh=refresh)
    result = wrap_session_tool(knowledge_search, env.sessions)('note', bundle_id)
    assert result['success'], result
    for row in result['data']['refresh_tasks']:
        task = env.manager.wait(row['task_id'], 5)
        assert task.status == TaskStatus.DONE
    assert env.autopilot.snapshot()['used'] == 1
    assert len([row for row in env.audit.history() if row['event'] == 'started']) == 1


def test_knowledge_refresh_enters_manager_and_session_before_config(env, monkeypatch):
    bundle_id, rule = knowledge_setup(env)
    env.approve([rule])
    original = env.manager.submit
    observed = []
    def submit(*args, **kwargs):
        assert env.store.lock._is_owned()
        assert env.sessions._lock._is_owned()
        assert env.manager._cond._is_owned()
        assert not env.autopilot._lock._is_owned()
        observed.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(env.manager, 'submit', submit)
    def knowledge_search(query='', bundle_id=None, scope='all', refresh='force'):
        return knowledge_tools.knowledge_search_impl(env.knowledge, query, bundle_id, scope, refresh=refresh)
    result = wrap_session_tool(knowledge_search, env.sessions)('note', bundle_id)
    assert result['success'] and observed == [True]
    for row in result['data']['refresh_tasks']:
        assert env.manager.wait(row['task_id'], 5).is_terminal


def test_knowledge_search_requires_full_bundle_scope_before_index_read(env, monkeypatch):
    bundle_id, rule = knowledge_setup(env, scope='allowed')
    env.approve([rule])
    def knowledge_search(query='', bundle_id=None, scope='all'):
        return knowledge_tools.knowledge_search_impl(env.knowledge, query, bundle_id, scope)
    result = wrap_session_tool(knowledge_search, env.sessions)('note', bundle_id)
    assert not result['success'] and env.manager.list() == []
    assert env.autopilot.snapshot()['used'] == 0


def test_unknown_manual_tool_cannot_use_an_existing_file_rule(env):
    env.approve([env.file_rule('read_file')])
    called = []
    def secret_reveal(filepath):
        called.append(filepath)
        return {'success': True}
    result = wrap_session_tool(secret_reveal, env.sessions)('.env')
    assert not result['success'] and called == [] and env.manager.list() == []
