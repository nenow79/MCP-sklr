"""Work-session metadata only; no results, checkpoint text or authorization."""
from dataclasses import dataclass
from enum import Enum
import re
from typing import Tuple


class SessionState(str, Enum):
    ACTIVE = "active"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"


class CheckpointState(str, Enum):
    PENDING = "pending"
    SAVED = "saved"
    UNAVAILABLE = "unavailable"


class SessionConflict(ValueError):
    """Stale revision or invalid transition; contains no foreign content."""


class SessionNotFound(KeyError):
    pass


def positive_int(value):
    if type(value) is not int or value < 1:
        raise ValueError("Expected a positive integer.")
    return value


def session_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Invalid session identifier.")
    return value


def root_aliases(values):
    if not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 32:
        raise ValueError("Expected 1 to 32 root aliases.")
    if any(not isinstance(v, str) or not re.fullmatch(r"[a-z0-9_-]{1,64}", v)
           for v in values):
        raise ValueError("Invalid root alias.")
    if len(set(values)) != len(values):
        raise ValueError("Duplicate root alias.")
    return tuple(values)


@dataclass(frozen=True)
class WorkSession:
    id: str
    roots: Tuple[str, ...]
    state: SessionState
    checkpoint_state: CheckpointState
    revision: int
    reason: str
    created_at: str
    updated_at: str

    def __post_init__(self):
        session_id(self.id)
        object.__setattr__(self, "roots", root_aliases(self.roots))
        positive_int(self.revision)
        if not isinstance(self.state, SessionState):
            raise ValueError("Invalid session state.")
        if not isinstance(self.checkpoint_state, CheckpointState):
            raise ValueError("Invalid checkpoint state.")
        if self.reason not in ("", "user_requested_pause", "server_restarted"):
            raise ValueError("Invalid reason.")


@dataclass(frozen=True)
class SessionTaskLink:
    session_id: str
    task_id: int
    session_revision: int

    def __post_init__(self):
        session_id(self.session_id)
        positive_int(self.task_id)
        positive_int(self.session_revision)
