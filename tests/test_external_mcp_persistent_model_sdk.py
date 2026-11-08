"""L3 real model API -> approvals -> TaskManager -> SDK -> local stdio peer.

Explicitly approved fake processes and in-memory MCP/ASGI communication only.
Not Windows/FlowAgent acceptance or a full production-main startup test.
"""
import json
from types import SimpleNamespace

import anyio
import pytest

from external_mcp.client import schema_digest
from external_mcp_model_sdk_helpers import ModelSDKRig, business
from external_mcp_persistent_sdk_helpers import alive, eventually


@pytest.fixture
def rig(tmp_path):
    instance = ModelSDKRig(tmp_path)
    try:
        yield instance
    finally:
        instance.finish()


def test_model_preview_apply_share_generation_and_ram_token_with_distinct_allows(rig):
    async def scenario():
        async with rig.clients() as (client, panel):
            catalog = await rig.discover(client)
            tools = json.loads(catalog['content'])['tools']
            profile = rig.registry.resolve('fake')['profile']
            rig.registry.set_trust(profile['id'], {t['name']: schema_digest(t['inputSchema'])
                for t in tools if t['name'] in ('preview', 'apply')}, rig.registry.list()['revision'])
            refreshed = await rig.discover(client)
            generation = refreshed['runtime_generation']
            assert generation == catalog['runtime_generation']
            preview = await rig.submit(client, 'preview', generation=generation)
            assert rig.number('call.count') == 0
            first = await rig.decision(panel, preview)
            preview_done = await rig.result(client, preview)
            token = business(preview_done)['content'][0]['text']
            apply = await rig.submit(client, 'apply', {'token': token}, generation)
            assert rig.number('call.count') == 1
            second = await rig.decision(panel, apply)
            applied = await rig.result(client, apply)
            result = business(applied)
            assert result['isError'] is False and result['content'][0]['text'] == 'applied'
            assert first != second and rig.number('call.count') == 2
            assert rig.number('initialize.count') == 1
            assert applied['data']['external_result']['data']['runtime_generation'] == generation
            assert alive(rig.number('server.pid'))
            for path in rig.folder.glob('*.db*'):
                assert token.encode() not in path.read_bytes()
            assert token not in (rig.folder / 'config.json').read_text()
    anyio.run(scenario)


def test_model_one_shot_is_control_for_lost_token(rig):
    async def scenario():
        async with rig.clients() as (client, panel):
            catalog = await rig.discover(client, 'one-shot')
            assert 'runtime_generation' not in catalog
            first = await rig.submit(client, 'preview', alias='one-shot')
            await rig.decision(panel, first)
            token = business(await rig.result(client, first))['content'][0]['text']
            preview_pid = rig.number('server.pid', 'one-shot')
            assert not alive(preview_pid)
            second = await rig.submit(client, 'apply', {'token': token}, alias='one-shot')
            await rig.decision(panel, second)
            result = business(await rig.result(client, second))
            assert result['isError'] is True and result['content'][0]['text'] == 'token-rejected'
            assert rig.number('server.pid', 'one-shot') != preview_pid
            assert rig.tools.persistent_supervisor.thread is None
    anyio.run(scenario)


@pytest.mark.parametrize('generation', [None, 'foreign-generation'])
def test_model_missing_or_foreign_generation_refuses_before_submit(rig, generation):
    async def scenario():
        async with rig.clients() as (client, panel):
            await rig.discover(client)
            response = await rig.submit(client, 'preview', generation=generation)
            assert response['success'] is False and 'task_id' not in response.get('data', {})
            assert rig.number('call.count') == 0
            assert rig.number('initialize.count') == 1
    anyio.run(scenario)


def test_model_denial_never_reaches_stdio_call(rig):
    async def scenario():
        async with rig.clients() as (client, panel):
            generation = (await rig.discover(client))['runtime_generation']
            response = await rig.submit(client, 'preview', generation=generation)
            await rig.decision(panel, response, 'deny')
            done = await rig.result(client, response)
            assert done['data']['status'] != 'done'
            assert rig.number('call.count') == 0
            assert rig.number('initialize.count') == 1
    anyio.run(scenario)


def test_model_generation_lost_during_approval_never_spawns_replacement(rig):
    async def scenario():
        async with rig.clients() as (client, panel):
            generation = (await rig.discover(client))['runtime_generation']
            response = await rig.submit(client, 'preview', generation=generation)
            pid = rig.number('server.pid')
            (rig.peers['fake'] / 'exit.signal').write_text('1')
            profile = rig.registry.resolve('fake')['profile']
            await anyio.to_thread.run_sync(lambda: eventually(lambda: rig.tools.persistent_supervisor.snapshot(
                owner=rig.session.id, profile_id=profile['id'])['runtime_generation'] is None))
            await rig.decision(panel, response)
            done = await rig.result(client, response)
            assert done['data']['status'] == 'failed'
            assert 'uncertain' not in done['data']['error']
            assert rig.number('server.pid') == pid and rig.number('call.count') == 0
            assert not alive(pid)
    anyio.run(scenario)


def test_model_cancel_after_dispatch_preserves_explicit_uncertainty(rig):
    async def scenario():
        async with rig.clients() as (client, panel):
            generation = (await rig.discover(client))['runtime_generation']
            response = await rig.submit(client, 'slow', generation=generation)
            tid = await rig.decision(panel, response)
            await anyio.to_thread.run_sync(lambda: eventually(
                lambda: (rig.peers['fake'] / 'dispatch.signal').exists(), seconds=10))
            canceled = await panel.post(f'/api/tasks/{tid}/cancel')
            assert canceled.status_code == 200
            done = await rig.result(client, response)
            assert done['data']['status'] == 'cancelled'
            assert 'call_cancelled_uncertain' in done['data']['error']
            assert rig.number('call.count') == rig.number('initialize.count') == 1
    anyio.run(scenario)


def test_model_one_shot_cancel_keeps_legacy_finalization_policy(rig):
    async def scenario():
        async with rig.clients() as (client, panel):
            await rig.discover(client, 'one-shot')
            response = await rig.submit(client, 'slow', alias='one-shot')
            tid = await rig.decision(panel, response)
            await anyio.to_thread.run_sync(lambda: eventually(
                lambda: (rig.peers['one-shot'] / 'dispatch.signal').exists(), seconds=10))
            assert (await panel.post(f'/api/tasks/{tid}/cancel')).status_code == 200
            done = await rig.result(client, response)
            assert done['data']['status'] == 'cancelled'
            assert done['data']['error'] == 'Anulowano.'
            assert rig.number('call.count', 'one-shot') == 1
            assert rig.tools.persistent_supervisor.thread is None
    anyio.run(scenario)


@pytest.mark.parametrize('state', ['pause', 'end'])
def test_model_blocked_or_missing_session_never_spawns(rig, state):
    current = rig.controller.snapshot()
    if state == 'pause':
        rig.controller.request_pause(current['revision'])
    else:
        rig.manager.end_session(current['session_id'], current['revision'])
    async def scenario():
        async with rig.clients() as (client, panel):
            response = await rig.api(client, 'list_external_mcp_tools', {'alias': 'fake'})
            assert response['success'] is False
            assert response['session_control']['execution_blocked']
            assert rig.tools.persistent_supervisor.thread is None
            assert rig.number('server.pid') == 0
    anyio.run(scenario)


def test_model_engaged_delegation_refuses_without_spawn(rig):
    # Real session wrapper + admission, control-state double only. No C2 claim.
    rig.controller._autopilot = SimpleNamespace(runtime=None, engaged=True,
        snapshot=lambda: {'engaged': True})
    async def scenario():
        async with rig.clients() as (client, panel):
            response = await rig.api(client, 'list_external_mcp_tools', {'alias': 'fake'})
            assert response['success'] is False
            assert rig.tools.persistent_supervisor.thread is None
            assert rig.number('server.pid') == 0
    try:
        anyio.run(scenario)
    finally:
        rig.controller._autopilot = None


def test_model_disable_during_approval_refuses_without_reconnect(rig):
    async def scenario():
        async with rig.clients() as (client, panel):
            generation = (await rig.discover(client))['runtime_generation']
            response = await rig.submit(client, 'preview', generation=generation)
            pid = rig.number('server.pid')
            profile = rig.registry.resolve('fake')['profile']
            rig.registry.set_enabled(profile['id'], False, rig.registry.list()['revision'])
            await rig.decision(panel, response)
            done = await rig.result(client, response)
            assert done['data']['status'] == 'failed'
            assert 'uncertain' not in done['data']['error']
            await anyio.to_thread.run_sync(lambda: eventually(lambda: not alive(pid)))
            assert rig.number('server.pid') == pid and rig.number('call.count') == 0
    anyio.run(scenario)


def test_model_timeout_after_dispatch_remains_uncertain(rig):
    async def scenario():
        async with rig.clients() as (client, panel):
            generation = (await rig.discover(client))['runtime_generation']
            response = await rig.submit(client, 'slow', generation=generation, timeout=3)
            await rig.decision(panel, response)
            done = await rig.result(client, response)
            assert done['data']['status'] == 'failed'
            assert 'call_timeout_uncertain' in done['data']['error']
            assert rig.number('call.count') == rig.number('initialize.count') == 1
    anyio.run(scenario)
