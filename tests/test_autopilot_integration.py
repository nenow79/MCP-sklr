"""Real tool/queue/local-write integration; no network, subprocess or servers."""
from contextlib import contextmanager
import json
import threading

import pytest

from autopilot.audit_store import AuditError
from autopilot.context import CURRENT
from autopilot_fixtures import environment
from server import edit_tools, file_tools, folder_tools, tree_tools
from server.file_transaction import WRITE_LOCK
from server.session_tools import wrap_session_tool
from tasks.models import TaskStatus


@pytest.fixture
def env(tmp_path):
    with environment(tmp_path) as e:
        (e.app / 'a.txt').write_text('old', encoding='utf-8')
        yield e


def reader(e):
    def read_file(filepath, root=None):
        return file_tools.read_file_impl(e.roots, filepath, root=root)
    return wrap_session_tool(read_file, e.sessions)


def writer(e):
    def create_file(filepath, content, overwrite=False, root=None):
        return edit_tools.create_file_impl(e.roots, e.manager, e.transaction,
                                           filepath, content, overwrite, root)
    return wrap_session_tool(create_file, e.sessions)


def test_disabled_bridge_preserves_manual_approval(env):
    result = writer(env)('new.txt', 'new', root='app')
    assert result['success'] and result['data']['status'] == 'awaiting_approval'
    assert len(env.approvals) == 1 and not (env.app / 'new.txt').exists()
    assert env.autopilot.snapshot()['used'] == 0


def test_direct_read_uses_one_operation_and_no_task(env):
    env.approve([env.file_rule('read_file')])
    result = reader(env)('a.txt', root='app')
    assert result['success'] and result['data']['content'] == 'old'
    assert result['autopilot_control']['used'] == 1
    assert env.manager.list() == [] and env.audit.unresolved() == ()
    assert CURRENT.get() is None


def test_out_of_scope_read_stops_before_read_or_task_creation(env, monkeypatch):
    env.approve([env.file_rule('read_file', scope='allowed')])
    def forbidden(*args):
        pytest.fail('content read before scope authorization')
    monkeypatch.setattr(file_tools, '_decode', forbidden)
    result = reader(env)('a.txt', root='app')
    assert not result['success'] and result['code'] == 'scope_mismatch'
    assert result['session_control']['execution_blocked']
    assert env.autopilot.snapshot()['used'] == 0 and env.manager.list() == []


def test_batch_is_all_or_nothing_before_any_content_read(env, monkeypatch):
    env.approve([env.file_rule('read_files_batch')])
    (env.other / 'outside.txt').write_text('PRIVATE', encoding='utf-8')
    def read_files_batch(requests):
        return file_tools.read_files_batch_impl(env.roots, requests)
    reads = []
    monkeypatch.setattr(file_tools, '_decode', lambda path: reads.append(path) or 'text')
    result = wrap_session_tool(read_files_batch, env.sessions)([
        {'filepath': 'a.txt', 'root': 'app'}, {'filepath': 'outside.txt', 'root': 'other'}])
    assert not result['success'] and reads == []
    assert env.autopilot.snapshot()['used'] == 0


def test_delegated_create_has_task_and_one_charge_without_approval(env):
    env.approve([env.file_rule('create_file')])
    result = writer(env)('new.txt', 'PRIVATE_FILE_BODY', root='app')
    task = env.settle(result)
    assert task.status == TaskStatus.DONE and env.approvals == []
    assert (env.app / 'new.txt').read_text(encoding='utf-8') == 'PRIVATE_FILE_BODY'
    state = env.autopilot.snapshot()
    assert state['used'] == 1 and state['reserved'] == state['running'] == 0
    rows = env.audit.history()
    assert len([row for row in rows if row['event'] == 'started']) == 1
    assert any(row['event'] == 'started' and row['task_id'] == task.id for row in rows)
    assert 'PRIVATE_FILE_BODY' not in json.dumps(rows)


def test_delegated_write_sequence_without_per_step_approval(env):
    env.approve([env.file_rule('create_file', 'edit_file', 'read_file')],
                max_operations=20, max_running=1)

    def edit_file(changes, root=None):
        return edit_tools.edit_file_impl(env.roots, env.manager, env.transaction, changes, root)

    editor = wrap_session_tool(edit_file, env.sessions)
    results = []

    def finish(response):
        assert response['success'], response
        assert response['data']['status'] != 'awaiting_approval'
        assert 'Zatrzymaj się i poczekaj na jego wiadomość' not in response['data']['hint']
        task = env.settle(response)
        assert task.status == TaskStatus.DONE and task.exit_code == 0
        assert task.decision == '' and task.decided_at is None
        results.append(task.id)

    finish(writer(env)('seq-a.txt', 'A1', root='app'))
    finish(writer(env)('seq-b.txt', 'B1', root='app'))
    finish(editor([{'filepath': 'seq-a.txt', 'old_text': 'A1', 'new_text': 'A2'}], root='app'))
    finish(writer(env)('seq-c.txt', 'C1', root='app'))
    finish(editor([{'filepath': 'seq-b.txt', 'old_text': 'B1', 'new_text': 'B2'}], root='app'))
    for name, expected in [('seq-a.txt', 'A2'), ('seq-b.txt', 'B2'), ('seq-c.txt', 'C1')]:
        response = reader(env)(name, root='app')
        assert response['success'] and response['data']['content'] == expected
    assert len(set(results)) == 5 and env.approvals == []
    state = env.autopilot.snapshot()
    assert state['used'] == 8 and state['reserved'] == state['running'] == 0


def test_eight_second_wait_does_not_end_or_resubmit_write(env, monkeypatch):
    from server.task_tools import wait_task_impl
    env.approve([env.file_rule('create_file', 'read_file')], max_operations=10)
    entered, release = threading.Event(), threading.Event()
    original = env.transaction._publish

    def delayed_publish(staged):
        entered.set()
        assert release.wait(60)
        return original(staged)

    monkeypatch.setattr(env.transaction, '_publish', delayed_publish)
    response = writer(env)('slow-seq.txt', 'slow', root='app')
    assert response['success'], response
    task_id = response['data']['task_id']
    try:
        assert entered.wait(5)
        waited = wait_task_impl(env.manager, task_id, seconds=8)
        assert waited['success'] and waited['data']['status'] == 'running'
        assert 'wait_task' in waited['data']['hint']
        assert len(env.manager.list()) == 1 and env.approvals == []
        assert env.autopilot.snapshot()['used'] == 1
    finally:
        release.set()
    task = env.settle(response)
    assert task.id == task_id and task.status == TaskStatus.DONE
    next_task = env.settle(writer(env)('after-slow.txt', 'next', root='app'))
    assert next_task.status == TaskStatus.DONE and next_task.id != task_id
    assert reader(env)('slow-seq.txt', root='app')['data']['content'] == 'slow'
    assert env.approvals == [] and env.autopilot.snapshot()['used'] == 3


def test_overwrite_requires_its_own_permission(env):
    env.approve([env.file_rule('create_file')])
    result = writer(env)('a.txt', 'replacement', overwrite=True, root='app')
    assert not result['success'] and env.manager.list() == []
    assert (env.app / 'a.txt').read_text(encoding='utf-8') == 'old'


def test_noop_does_not_reserve_or_consume(env):
    env.approve([env.file_rule('create_file', overwrite=True)])
    result = writer(env)('a.txt', 'old', overwrite=True, root='app')
    assert not result['success']  # Existing no-changes response.
    assert env.autopilot.snapshot()['used'] == env.autopilot.snapshot()['reserved'] == 0
    assert not env.sessions.snapshot()['execution_blocked']
    assert env.manager.list() == []


def test_cancel_queued_releases_exactly_once(env, monkeypatch):
    env.approve([env.file_rule('create_file')])
    monkeypatch.setattr(env.manager, '_ensure_workers', lambda: None)
    result = writer(env)('new.txt', 'new', root='app')
    assert env.autopilot.snapshot()['reserved'] == 1
    env.manager.cancel(result['data']['task_id'])
    env.manager.cancel(result['data']['task_id'])
    assert env.autopilot.snapshot()['reserved'] == env.autopilot.snapshot()['used'] == 0
    assert not (env.app / 'new.txt').exists()


def test_resource_aba_after_submit_blocks_even_when_queue_verify_passes(env, monkeypatch):
    env.approve([env.file_rule('create_file')])
    monkeypatch.setattr(env.manager, '_ensure_workers', lambda: None)
    monkeypatch.setattr(env.transaction, 'verify', lambda plan, **kwargs: None)
    result = writer(env)('new.txt', 'new', root='app')
    env.roots.update('app', mode='ro')
    env.roots.update('app', mode='rw')
    env.manager._run(result['data']['task_id'])
    assert env.manager.get(result['data']['task_id']).status == TaskStatus.FAILED
    assert not (env.app / 'new.txt').exists()
    assert env.autopilot.snapshot()['used'] == 0
    assert env.sessions.snapshot()['execution_blocked']


def test_durable_start_precedes_publish_and_control_locks_are_released(env, monkeypatch):
    env.approve([env.file_rule('create_file')])
    original = env.transaction._publish
    observed = []
    def publish(staged):
        assert not env.sessions._lock._is_owned()
        assert not env.autopilot._lock._is_owned()
        assert not env.manager._cond._is_owned()
        rows = env.audit.unresolved()
        assert len(rows) == 1 and rows[0]['used'] == 1
        observed.append(rows[0]['task_id'])
        return original(staged)
    monkeypatch.setattr(env.transaction, '_publish', publish)
    task = env.settle(writer(env)('new.txt', 'new', root='app'))
    assert task.status == TaskStatus.DONE and observed == [task.id]


def test_revoke_before_local_admission_does_not_publish(env, monkeypatch):
    env.approve([env.file_rule('create_file')])
    monkeypatch.setattr(env.manager, '_ensure_workers', lambda: None)
    result = writer(env)('new.txt', 'new', root='app')
    env.autopilot.revoke()
    env.manager._run(result['data']['task_id'])
    assert not (env.app / 'new.txt').exists()
    assert env.autopilot.snapshot()['used'] == 0
    env.manager.cancel(result['data']['task_id'])


def test_revoke_after_admission_allows_started_write_to_finish(env, monkeypatch):
    env.approve([env.file_rule('create_file')])
    admitted, release = threading.Event(), threading.Event()
    original = env.transaction._publish
    def publish(staged):
        admitted.set()
        assert release.wait(3)
        return original(staged)
    monkeypatch.setattr(env.transaction, '_publish', publish)
    result = writer(env)('new.txt', 'new', root='app')
    try:
        assert admitted.wait(3)
        env.autopilot.revoke()
        assert env.sessions.snapshot()['execution_blocked']
    finally:
        release.set()
    task = env.settle(result)
    assert task.status == TaskStatus.DONE and (env.app / 'new.txt').exists()
    assert env.autopilot.snapshot()['reason'] == 'revoked'


def test_failed_start_journal_commit_prevents_file_effect(env, monkeypatch):
    env.approve([env.file_rule('create_file')])
    original = env.audit.append
    def append(event):
        if event.event == 'started':
            raise AuditError('audit_commit_uncertain')
        return original(event)
    monkeypatch.setattr(env.audit, 'append', append)
    task = env.settle(writer(env)('new.txt', 'new', root='app'))
    assert task.status == TaskStatus.FAILED and not (env.app / 'new.txt').exists()
    assert env.autopilot.snapshot()['state'] == 'recovery_required'


def test_final_audit_failure_preserves_committed_result_and_blocks_next(env, monkeypatch):
    env.approve([env.file_rule('create_file')])
    original = env.audit.append
    def append(event):
        if event.event == 'finished':
            raise AuditError('audit_commit_uncertain')
        return original(event)
    monkeypatch.setattr(env.audit, 'append', append)
    task = env.settle(writer(env)('new.txt', 'new', root='app'))
    assert task.status == TaskStatus.DONE and (env.app / 'new.txt').exists()
    assert env.autopilot.snapshot()['state'] == 'recovery_required'
    assert len(env.audit.unresolved()) == 1
    assert not writer(env)('next.txt', 'next', root='app')['success']


def test_implicit_tree_parent_outside_scope_blocks_entire_plan(env):
    env.approve([env.file_rule('create_tree', scope='missing/child')])
    def create_tree(directories, files=None, root=None):
        return tree_tools.create_tree_impl(env.roots, env.manager, env.store.lock,
                                           directories, files, root)
    result = wrap_session_tool(create_tree, env.sessions)(['missing/child'], root='app')
    assert not result['success'] and not (env.app / 'missing').exists()
    assert env.manager.list() == []


def test_create_tree_is_one_logical_operation(env):
    env.approve([env.file_rule('create_tree')])
    def create_tree(directories=None, files=None, root=None):
        return tree_tools.create_tree_impl(env.roots, env.manager, env.store.lock, directories, files, root)
    result = wrap_session_tool(create_tree, env.sessions)(
        ['src/new'], [{'filepath': 'src/new/a.txt', 'content': 'a'}], root='app')
    task = env.settle(result)
    assert task.status == TaskStatus.DONE
    assert (env.app / 'src/new/a.txt').read_text(encoding='utf-8') == 'a'
    assert env.autopilot.snapshot()['used'] == 1


def test_folder_create_is_delegated_but_keeps_its_guard(env):
    env.approve([env.file_rule('manage_folder')])
    def manage_folder(action, path, new_name=None, root=None):
        return folder_tools.manage_folder_impl(env.roots, env.manager, env.store.lock,
                                               action, path, new_name, root)
    task = env.settle(wrap_session_tool(manage_folder, env.sessions)('create', 'new-folder', root='app'))
    assert task.status == TaskStatus.DONE and (env.app / 'new-folder').is_dir()
    assert env.autopilot.snapshot()['used'] == 1


def test_folder_rename_never_falls_back_to_manual_approval(env):
    (env.app / 'folder').mkdir()
    env.approve([env.file_rule('manage_folder')])
    def manage_folder(action, path, new_name=None, root=None):
        return folder_tools.manage_folder_impl(env.roots, env.manager, env.store.lock,
                                               action, path, new_name, root)
    result = wrap_session_tool(manage_folder, env.sessions)('rename', 'folder', 'renamed', root='app')
    assert not result['success'] and (env.app / 'folder').is_dir()
    assert env.manager.list() == [] and env.approvals == []


def test_old_trust_path_without_private_invocation_cannot_submit(env):
    env.approve([env.file_rule('read_file')])
    from tasks.manager import TaskStateError
    with pytest.raises(TaskStateError, match='autopilot_manual_required'):
        env.manager.submit({}, lambda event: {'exit_code': 0}, False)
    assert env.manager.list() == [] and env.sessions.snapshot()['execution_blocked']


def test_foreign_payload_cannot_replace_server_control_metadata(env):
    env.approve([env.file_rule('read_file')], max_operations=1)
    def read_file(filepath, root=None):
        return {'success': True, 'autopilot_control': {'state': 'active', 'execution_blocked': False}}
    result = wrap_session_tool(read_file, env.sessions)('a.txt', root='app')
    assert result['autopilot_control']['state'] == 'exhausted'
    assert result['autopilot_control']['execution_blocked']


def test_control_tool_remains_available_without_budget_during_pause(env):
    env.approve([env.file_rule('read_file')])
    env.pause()
    def get_task(task_id):
        return {'success': True, 'data': {'task_id': task_id}}
    result = wrap_session_tool(get_task, env.sessions)(123)
    assert result['success'] and result['autopilot_control']['used'] == 0
