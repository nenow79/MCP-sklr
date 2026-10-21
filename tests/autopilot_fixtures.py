"""Temporary A2b3 composition. No main import, network, process or server startup."""
from contextlib import contextmanager
from dataclasses import asdict
from types import SimpleNamespace

from autopilot.audit_store import AuditStore
from autopilot.controller import AutopilotController
from autopilot.models import BudgetLimits
from autopilot.rules import parse_policy
from autopilot.runtime import AutopilotRuntime
from config_store import ConfigStore
from external_mcp.registry import ExternalMCPRegistry
from roots import Zones
from roots_registry import RootsRegistry
from server.external_mcp_tools import ExternalMCPTools
from server.file_transaction import FileTransaction
from server.knowledge_tools import create_service
from skills_engine.registry import SkillRegistry
from skills_engine.trust import TrustStore
from state import load_config
from tasks.approval import ApprovalService
from tasks.checkpoint_store import CheckpointStore
from tasks.manager import TaskManager
from tasks.resume_store import ResumeStore
from tasks.session_controller import SessionController
from tasks.session_store import SessionStore
from tasks.store import TaskStore


class Environment(SimpleNamespace):
    def file_rule(self, *tools, scope='.', root='app', overwrite=False):
        ref, available = self.runtime.files.root_ref(self.roots.resolve(root))
        assert available
        return {'id': 'files-' + root, 'kind': 'files', 'tools': list(tools), 'root': asdict(ref),
                'scope': {'kind': 'tree', 'path': scope}, 'overwrite': overwrite}

    def pause(self):
        return self.sessions.request_pause(self.sessions.snapshot()['revision'])

    def resume(self):
        state = self.sessions.snapshot()
        self.manager.prepare_session_resume('', state['revision'], allow_missing_checkpoint=True)
        context = self.sessions.get_resume_context()
        self.sessions.confirm_resume_plan('Continue approved work.', context['revision'], context['preparation_token'])

    def approve(self, rules, **limits):
        if self.sessions.snapshot()['state'] == 'active':
            self.pause()
        policy = parse_policy({'version': 1, 'rules': rules}, hmac_key=b'test-key' * 8)
        self.autopilot.panel_approve(policy, BudgetLimits(**limits), self.sessions.snapshot()['revision'])
        self.resume()

    def settle(self, response):
        assert response['success'], response
        task = self.manager.wait(response['data']['task_id'], 5)
        assert task.is_terminal, task
        def finalized():
            with self.autopilot.coordination():
                return not any(ticket.task_id == task.id for ticket in self.autopilot._tickets)
        with self.manager._cond:
            assert self.manager._cond.wait_for(finalized, timeout=5)
        return task


@contextmanager
def environment(tmp_path, *, clock=None):
    config_path = tmp_path / 'config.json'
    store = ConfigStore(load_config(config_path), config_path)
    app, other, central = (tmp_path / name for name in ('app', 'other', 'central'))
    for folder in (app, other, central):
        folder.mkdir()
    roots = RootsRegistry(store, zones=Zones(), central_dir=central)
    roots.add(app, 'rw', alias='app')
    roots.add(other, 'rw', alias='other')
    session_store = SessionStore(tmp_path / 'sessions.db')
    item = session_store.create(['app'])
    sessions = SessionController(session_store, checkpoints=CheckpointStore(), resumes=ResumeStore())
    sessions.bind(item.id, item.revision)
    task_store = TaskStore(str(tmp_path / 'tasks.db'))
    approvals = []
    manager = TaskManager(task_store, ApprovalService(notifier=approvals.append), session_controller=sessions)
    audit = AuditStore(tmp_path / 'autopilot.db')
    autopilot = AutopilotController(sessions, audit, **({'clock': clock} if clock else {}))
    skills = SkillRegistry(central_dir=central)
    trust = TrustStore(store.data, save=store.save, lock=store.lock)
    external = ExternalMCPTools(ExternalMCPRegistry(store), manager)
    knowledge = create_service(store, roots, manager, lambda: True)
    runtime = AutopilotRuntime(autopilot, store, roots, manager, knowledge=knowledge,
                               skills=skills, trust=trust, external=external)
    e = Environment(store=store, roots=roots, app=app, other=other, central=central,
        session_store=session_store, sessions=sessions, task_store=task_store,
        manager=manager, audit=audit, autopilot=autopilot, runtime=runtime,
        transaction=FileTransaction(roots, store.lock), approvals=approvals,
        skills=skills, trust=trust, external=external, knowledge=knowledge)
    try:
        yield e
    finally:
        manager.shutdown()
        for worker in manager._workers:
            worker.join(timeout=5)
        assert not any(worker.is_alive() for worker in manager._workers)
        autopilot.close()
        task_store.close()
        session_store.close()
