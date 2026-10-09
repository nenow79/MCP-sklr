"""Wspólne fixtures: środowisko kolejki zadań z automatycznie odpowiadającym notifierem."""
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pytest

from server.task_tools import run_skill_script_impl
from skills_engine.trust import TrustStore
from tasks.approval import ApprovalService
from tasks.manager import TaskManager
from tasks.store import TaskStore


@dataclass
class TaskEnv:
    registry: Any
    config: Dict[str, Any]
    trust_store: TrustStore
    manager: TaskManager
    approval: ApprovalService
    requests: List[Any] = field(default_factory=list)  # zadania zgłoszone do zatwierdzenia

    def call(self, **kwargs) -> Dict[str, Any]:
        params = dict(name="demo", script="scripts/run.py", args=["a", "b"],
                      timeout=30, default_python=sys.executable)
        params.update(kwargs)
        return run_skill_script_impl(
            self.registry, self.config, self.trust_store, self.manager, **params)


@pytest.fixture
def make_env(tmp_path):
    created = []

    def factory(registry, answer: Optional[str] = "allow", config: Optional[Dict[str, Any]] = None,
                max_parallel: int = 2, max_queued: int = 50,
                before_decision: Optional[Callable[[Any], None]] = None) -> TaskEnv:
        """answer: decyzja wysyłana automatycznie przez notifier (None = czeka na decyzję)."""
        config = config if config is not None else {"skills": {"trust": {}}}
        store = TaskStore(str(tmp_path / f"tasks{len(created)}.db"))
        requests: List[Any] = []

        def notifier(task):
            requests.append(task)
            if before_decision is not None:
                before_decision(task)
            if answer is not None:
                approval.resolve(task.id, answer)

        approval = ApprovalService(notifier=notifier)
        manager = TaskManager(store, approval, max_parallel=max_parallel,
                              max_queued=max_queued, max_wait_s=8)
        env = TaskEnv(registry, config, TrustStore(config), manager, approval, requests)
        created.append((manager, store))
        return env

    yield factory
    for manager, store in created:
        manager.shutdown()
        store.close()
