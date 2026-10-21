"""Optional execution bridge. A2b4 must explicitly construct/authorize this runtime.

No imports of main, no databases/threads at import time, no implicit activation.
Local writes keep only write/config locks. Local knowledge scans that can submit
maintenance tasks use manager -> session -> config, with no contract lock.
No session/contract lock spans network, processes or waiting for the user.
"""
import copy
import inspect
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

from autopilot.adapters.files import FileAdapter, WRITES
from autopilot.adapters.knowledge import KnowledgeAdapter
from autopilot.adapters.mcp import MCPAdapter
from autopilot.adapters.skills import SkillAdapter
from autopilot.context import CURRENT
from autopilot.controller import AutopilotBlocked, AutopilotBusy
from autopilot.matcher import evaluate
from autopilot.operations import OperationDescriptor
from server.file_transaction import WRITE_LOCK

QUEUED = WRITES | {'run_skill_script', 'list_external_mcp_tools', 'call_external_mcp_tool'}


@dataclass(repr=False)
class Invocation:
    runtime: object
    refresh: object
    ticket: object = None
    queued: bool = False
    submitted: bool = False
    result: object = field(default=None, repr=False)
    blocked_code: str = ''
    uncertain: bool = False
    dispatched: bool = False
    call_args: tuple = field(default=(), repr=False)
    call_kwargs: dict = field(default_factory=dict, repr=False)

    def verify_only(self):
        # Used with resource locks, with no callback into controller/manager.
        if self.ticket is not None:
            try:
                self.refresh(self.ticket.binding)
            except Exception as exc:
                self.blocked_code = exc.code if type(exc) is AutopilotBlocked else 'resource_changed'
                raise

    def recheck(self):
        if self.ticket is not None and self.ticket.phase == 'started':
            self.runtime.controller.recheck_started(self.ticket)


class AutopilotRuntime:
    def __init__(self, controller, config_store, roots, manager, *, knowledge=None, skills=None,
                 trust=None, external=None):
        if controller.runtime is not None or manager._sessions is not controller.sessions:
            raise ValueError('autopilot_runtime_binding')
        self.controller, self.store, self.manager = controller, config_store, manager
        from autopilot.resources import ResourceGenerations
        self.generations = ResourceGenerations(config_store)
        if skills is not None:
            skills.add_change_listener(self.generations.invalidate)
        self.files = FileAdapter(roots, self.generations)
        self.knowledge = KnowledgeAdapter(knowledge, self.files, self.generations)
        self.skills = SkillAdapter(skills, trust, config_store, self.generations)
        self.mcp = MCPAdapter(external, self.generations)
        if knowledge is not None:
            knowledge._dispatch_context = self.knowledge_coordination
        controller.runtime = self

    @contextmanager
    def knowledge_coordination(self):
        with self.manager._cond:
            with self.controller.sessions._lock:
                yield

    def build(self, binding, tool, args):
        with self.store.lock:
            if tool == 'list_roots':
                from autopilot.operations import AccessSnapshot, ResourceAccess, ResourceTarget
                references = [self.files.root_ref(root) for root in self.files.roots.list()]
                return OperationDescriptor(binding, tool, 'list', targets=tuple(
                    ResourceTarget(ref) for ref, available in references)), AccessSnapshot(binding, tuple(
                        ResourceAccess(ref, checked=True, available=available) for ref, available in references)), not references
            if tool in WRITES | {'list_directory', 'read_file', 'read_files_batch', 'search_code'}:
                return self.files.build(binding, tool, args)
            if tool in {'knowledge_bundles', 'knowledge_search', 'knowledge_get'}:
                return self.knowledge.build(binding, tool, args)
            if tool in {'list_skills', 'activate_skill', 'read_skill_resource', 'run_skill_script'}:
                return self.skills.build(binding, tool, args)
            if tool in {'list_external_mcp', 'list_external_mcp_tools', 'call_external_mcp_tool'}:
                return self.mcp.build(binding, tool, args)
            from autopilot.operations import AccessSnapshot
            return OperationDescriptor(binding, tool, args.get('action', 'unknown')), AccessSnapshot(binding), False

    @contextmanager
    def invocation(self, function, args, kwargs):
        controller = self.controller
        if not controller.engaged:
            yield None
            return
        try:
            with controller.coordination():
                controller._active()
            bound = inspect.signature(function).bind(*args, **kwargs)
            bound.apply_defaults()
            values = copy.deepcopy(dict(bound.arguments))
            binding = controller.binding
            operation, access, noop = self.build(binding, function.__name__, values)
            # A no-op must still have a valid scope/policy before skipping charge.
            decision = evaluate(controller._policy, operation, access, binding)
            if not decision.allowed:
                raise AutopilotBlocked(decision.code)

            def refresh(current_binding):
                fresh, current_access, current_noop = self.build(current_binding, function.__name__, values)
                if fresh != operation:
                    raise AutopilotBlocked('resource_changed')
                decision = evaluate(controller._policy, fresh, current_access, current_binding)
                if not decision.allowed:
                    raise AutopilotBlocked(decision.code)
                if function.__name__ == 'call_external_mcp_tool':
                    discovery = OperationDescriptor(current_binding, 'list_external_mcp_tools', 'discover',
                        resource=fresh.resource, timeout_s=fresh.timeout_s)
                    if not evaluate(controller._policy, discovery, current_access, current_binding).allowed:
                        raise AutopilotBlocked('tool_not_allowed')
                return fresh, current_access

            # Execute a detached copy of the same request that was authorized.
            # Concurrent changes to caller-owned JSON cannot change dispatch.
            bound.arguments = copy.deepcopy(values)
            scope = Invocation(self, refresh, queued=function.__name__ in QUEUED,
                               call_args=bound.args, call_kwargs=bound.kwargs)
            if not noop:
                scope.ticket = controller.reserve(refresh)
        except Exception as exc:
            code = exc.code if type(exc) is AutopilotBlocked else 'invalid_operation'
            controller.stop(code)
            raise AutopilotBlocked(code) from None
        marker = CURRENT.set(scope)
        failed = True
        try:
            if scope.queued or scope.ticket is None:
                yield scope
            else:
                # File/skill catalog reads retain their resource lock while reading.
                retain_config = operation.tool not in {'knowledge_search', 'knowledge_get', 'knowledge_bundles'}
                with self.effect(scope.ticket, retain_config=retain_config):
                    if retain_config:
                        yield scope
                    else:
                        # Knowledge may submit/reap refresh tasks under config lock.
                        # Enter manager first; no session/contract lock spans the read.
                        with self.knowledge_coordination():
                            yield scope
            failed = False
            if scope.blocked_code:
                controller.stop(scope.blocked_code)
            elif scope.queued and scope.ticket is not None and not scope.submitted:
                # The underlying adapter rejected before submit. No automatic
                # approval fallback or later operation under this delegation.
                controller.stop('invalid_operation')
        except AutopilotBlocked as exc:
            controller.stop(exc.code)
            raise
        finally:
            CURRENT.reset(marker)
            if scope.ticket is not None and not scope.submitted:
                try:
                    self.complete(scope, uncertain=scope.uncertain or failed and scope.ticket.phase == 'started',
                                  failed=failed or type(scope.result) is dict and scope.result.get('success') is False)
                except Exception:
                    controller.stop('storage_error', uncertain=True)

    @contextmanager
    def effect(self, ticket, *, retain_config=False, cancel_event=None, verify=None):
        """One admission point. Never wait for WRITE_LOCK with control locks held."""
        config_locked = write_locked = False
        try:
            while True:
                retry = False
                with self.manager._cond:
                    with self.controller.coordination():
                        if self.manager._stopped or cancel_event is not None and cancel_event.is_set():
                            raise AutopilotBlocked('cancelled')
                        self.controller._require(ticket, 'reserved')
                        self.controller._active()
                        write = ticket.tool in WRITES
                        if write and not WRITE_LOCK.acquire(blocking=False):
                            retry = True
                        else:
                            write_locked = write
                            self.store.lock.acquire()
                            config_locked = True
                            try:
                                if verify is not None and verify():
                                    raise AutopilotBlocked('resource_changed')
                                self.controller.start_internal(ticket)
                            except AutopilotBusy:
                                retry = True
                            except Exception:
                                raise
                            if not retry:
                                break
                if config_locked:
                    self.store.lock.release()
                    config_locked = False
                if write_locked:
                    WRITE_LOCK.release()
                    write_locked = False
                # Internal contention wait, never model polling or a parked paused worker.
                if cancel_event is not None:
                    cancel_event.wait(0.025)
                else:
                    threading.Event().wait(0.025)
                self.controller.tick()
            if not retain_config and ticket.tool not in WRITES:
                self.store.lock.release()
                config_locked = False
            yield
        finally:
            if config_locked:
                self.store.lock.release()
            if write_locked:
                WRITE_LOCK.release()

    def complete(self, scope, *, uncertain=False, failed=False):
        ticket = scope.ticket
        if ticket is None:
            return
        if ticket.phase == 'started':
            self.controller.finish(ticket, 'uncertain' if uncertain else 'failed' if failed else 'ok')
        elif ticket.phase == 'reserved':
            self.controller.release(ticket)

    def task_invocation(self, ticket):
        return Invocation(self, ticket.refresh, ticket=ticket, queued=True, submitted=True)
