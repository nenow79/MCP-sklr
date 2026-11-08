"""L4 authenticated panel with isolated local SDK peers; no production bootstrap."""
import copy

import anyio
import pytest

from external_mcp_model_sdk_helpers import ModelSDKRig, business
from external_mcp_persistent_sdk_helpers import alive
from panel.app import create_panel_app
from server.external_mcp_admin import ExternalMCPAdmin


@pytest.fixture
def rig(tmp_path):
    instance = ModelSDKRig(tmp_path)
    instance.admin = ExternalMCPAdmin(instance.registry, instance.manager, runtime=instance.tools)
    instance.panel = create_panel_app(instance.manager, instance.approval, instance.auth,
                                      external_mcp=instance.admin)
    try:
        yield instance
    finally:
        instance.finish()


async def listing(panel):
    result = await panel.get('/api/external-mcp')
    assert result.status_code == 200, result.text
    return result.json()['data']


def test_panel_mode_revision_save_enabled_and_list_never_spawn(rig):
    async def scenario():
        async with rig.clients() as (client, panel):
            before = await listing(panel)
            proposal = {'alias': 'new-fake', 'transport': 'stdio', 'command': 'never-executed'}
            added = await panel.post('/api/external-mcp', json={
                'profile': proposal, 'revision': before['revision']})
            assert added.status_code == 200, added.text
            current = added.json()['data']
            row = next(p for p in current['profiles'] if p['alias'] == 'new-fake')
            assert row['stdio_mode'] == 'one_shot' and row['enabled'] is False
            path = '/api/external-mcp/' + row['id']
            proposal['stdio_mode'] = 'persistent'
            conflict = await panel.post(path + '/update', json={
                'profile': proposal, 'revision': before['revision']})
            assert conflict.status_code == 409, conflict.text
            assert next(p for p in (await listing(panel))['profiles']
                        if p['id'] == row['id'])['stdio_mode'] == 'one_shot'
            saved = await panel.post(path + '/update', json={
                'profile': proposal, 'revision': current['revision']})
            assert saved.status_code == 200, saved.text
            enabled = await panel.post(path + '/enabled', json={
                'enabled': True, 'revision': saved.json()['data']['revision']})
            assert enabled.status_code == 200, enabled.text
            view = next(p for p in (await listing(panel))['profiles'] if p['id'] == row['id'])
            assert view['stdio_mode'] == 'persistent' and view['enabled'] is True
            assert view['runtime']['state'] == 'absent' and view['last_test'] is None
            model = await rig.api(client, 'list_external_mcp', {})
            assert all(p['runtime'] is None or p['runtime']['state'] == 'absent'
                       for p in model['data']['profiles'])
            assert rig.tools.persistent_supervisor.thread is None
            assert not rig.tools.catalogs and not rig.manager.list()
            assert not list(rig.folder.glob('*/server.pid'))
    anyio.run(scenario)


def test_admin_inspect_is_separate_process_preserves_working_preview_cache_and_idle(rig):
    async def scenario():
        async with rig.clients() as (client, panel):
            profile = rig.registry.resolve('fake')['profile']
            path = '/api/external-mcp/' + profile['id'] + '/inspect'
            refused = await panel.post(path, json={})
            assert refused.status_code == 400
            assert rig.tools.persistent_supervisor.thread is None
            generation = (await rig.discover(client))['runtime_generation']
            working_pid = rig.number('server.pid')
            rig.pids.add(working_pid)
            preview = await rig.submit(client, 'preview', generation=generation)
            await rig.decision(panel, preview)
            token = business(await rig.result(client, preview))['content'][0]['text']
            cached = copy.deepcopy(rig.tools.catalogs)
            supervisor = rig.tools.persistent_supervisor
            key = (rig.session.id, profile['id'])
            with supervisor._lock:
                idle = supervisor._runtimes[key].last_activity
            started = await panel.post(path, json={'acknowledge_launch': True})
            assert started.status_code == 202, started.text
            task_id = started.json()['data']['task_id']
            task = await anyio.to_thread.run_sync(rig.manager.wait, task_id, 10)
            assert task.status.value == 'done', task.error
            inspector_pid = rig.number('server.pid')
            rig.pids.add(inspector_pid)
            assert inspector_pid != working_pid and not alive(inspector_pid)
            assert alive(working_pid)
            data = await listing(panel)
            row = next(p for p in data['profiles'] if p['id'] == profile['id'])
            assert row['last_test']['state'] == 'ok'
            assert row['last_test']['execution_mode'] == 'one_shot'
            assert row['runtime'] == {'state': 'ready', 'runtime_generation': generation, 'reason': ''}
            assert rig.tools.catalogs == cached
            with supervisor._lock:
                assert supervisor._runtimes[key].last_activity == idle
            apply = await rig.submit(client, 'apply', {'token': token}, generation)
            await rig.decision(panel, apply)
            assert business(await rig.result(client, apply))['content'][0]['text'] == 'applied'
    anyio.run(scenario)


def test_admin_inspection_before_discovery_does_not_create_model_cache_or_runtime(rig):
    async def scenario():
        profile = rig.registry.resolve('fake')['profile']
        rig.registry.set_enabled(profile['id'], False, rig.registry.list()['revision'])
        async with rig.clients() as (client, panel):
            result = await panel.post('/api/external-mcp/' + profile['id'] + '/inspect',
                                      json={'acknowledge_launch': True})
            assert result.status_code == 202, result.text
            tid = result.json()['data']['task_id']
            done = await anyio.to_thread.run_sync(rig.manager.wait, tid, 10)
            assert done.status.value == 'done' and done.decision == '', done.error
            assert not alive(rig.number('server.pid'))
            assert not rig.tools.catalogs and rig.tools.persistent_supervisor.thread is None
            row = next(p for p in (await listing(panel))['profiles'] if p['id'] == profile['id'])
            assert row['last_test']['state'] == 'ok' and row['runtime']['state'] == 'absent'
            rig.registry.set_enabled(profile['id'], True, rig.registry.list()['revision'])
            refused = await rig.submit(client, 'preview', generation='admin-is-not-a-generation')
            assert refused['success'] is False
            assert rig.tools.persistent_supervisor.thread is None and not rig.tools.catalogs
    anyio.run(scenario)
