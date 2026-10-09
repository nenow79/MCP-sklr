#!/usr/bin/env python3
"""Modele kolejki zadań (bez zależności od FastMCP i tkinter)."""
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    AWAITING_APPROVAL = "awaiting_approval"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


ACTIVE_STATUSES = frozenset({
    TaskStatus.AWAITING_APPROVAL,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
})
TERMINAL_STATUSES = frozenset(set(TaskStatus) - set(ACTIVE_STATUSES))

_SUMMARY_EXCLUDED = ("stdout", "stderr", "command")


@dataclass
class Task:
    id: int
    created_at: str
    status: TaskStatus = TaskStatus.QUEUED
    skill: str = ""
    source: str = ""
    script: str = ""
    args: List[str] = field(default_factory=list)
    timeout: int = 60
    command: List[str] = field(default_factory=list)
    skill_hash: str = ""
    approval_reason: str = ""
    offer_trust: bool = False
    decision: str = ""
    decided_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_s: Optional[float] = None
    error: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    def to_summary(self) -> Dict[str, Any]:
        """Zwięzły widok (bez stdout/stderr/command) dla list_tasks."""
        d = self.to_dict()
        for key in _SUMMARY_EXCLUDED:
            d.pop(key, None)
        return d
