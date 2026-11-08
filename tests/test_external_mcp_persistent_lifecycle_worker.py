"""L1 worker failure/retention tests; no transport or external resources."""
import threading
import queue

import pytest

from external_mcp.persistent_lifecycle import PersistentLifecycle
from external_mcp_persistent_admission_helpers import until, wait


class Supervisor:
    def __init__(self):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.fail = False

    def invalidate_generation(self, **fields):
        self.calls.append(fields)
        self.entered.set()
        wait(self.release, 'worker invalidation release')
        if self.fail:
            raise RuntimeError('private failure')


def profile(pid='fake'):
    return {'id': pid, 'transport': 'stdio', 'stdio_mode': 'persistent',
            'command': 'never-executed', 'enabled': True}


def test_close_retires_tracked_runtime_and_blocks_new_tracks():
    supervisor = Supervisor()
    worker = PersistentLifecycle(supervisor, name='test-lifecycle')
    try:
        worker.track('owner', profile(), 'generation')
        assert worker.close()
        assert worker.close()
        assert worker.blocked
        assert [c['expected_runtime_generation'] for c in supervisor.calls] == ['generation']
        with pytest.raises(RuntimeError):
            worker.track('owner', profile(), 'next')
    finally:
        worker.close()


def test_overflow_latches_and_drains_every_pin_including_unqueued():
    supervisor = Supervisor()
    supervisor.release.clear()
    worker = PersistentLifecycle(supervisor, name='test-lifecycle', limit=1)
    try:
        worker.track('owner', profile('first'), 'first')
        worker.session_changed('owner', 'pause')
        wait(supervisor.entered, 'first invalidation')
        worker.track('owner', profile('second'), 'second')
        worker.track('owner', profile('third'), 'third')
        with pytest.raises(RuntimeError):
            worker.session_changed('owner', 'pause')
        assert worker.blocked
        supervisor.release.set()
        until(lambda: len(supervisor.calls) == 3, 'overflow drain')
        assert {c['expected_runtime_generation'] for c in supervisor.calls} == {
            'first', 'second', 'third'}
    finally:
        supervisor.release.set()
        assert worker.close()


def test_failed_invalidation_latches_and_retires_other_idle_pins():
    supervisor = Supervisor()
    supervisor.fail = True
    worker = PersistentLifecycle(supervisor, name='test-lifecycle')
    try:
        worker.track('first-owner', profile(), 'first')
        worker.track('second-owner', profile(), 'second')
        worker.session_changed('first-owner', 'pause')
        until(lambda: worker.blocked, 'failure latch')
        until(lambda: len(supervisor.calls) == 2, 'failure drain')
    finally:
        assert worker.close()


def test_repeated_discovery_prunes_obsolete_unqueued_pins():
    supervisor = Supervisor()
    worker = PersistentLifecycle(supervisor, name='test-lifecycle')
    try:
        for count in range(100):
            worker.track('owner', profile(), str(count))
        worker.session_changed('owner', 'pause')
        until(lambda: len(supervisor.calls) >= 1, 'latest pin invalidation')
        assert worker.close()
        assert [c['expected_runtime_generation'] for c in supervisor.calls] == ['99']
    finally:
        worker.close()


def test_notification_at_empty_queue_boundary_is_not_invalidated_twice():
    supervisor = Supervisor()
    supervisor.fail = True
    worker = PersistentLifecycle(supervisor, name='test-lifecycle')
    original = worker._queue.get
    injected = threading.Event()
    def get(**kwargs):
        try:
            return original(**kwargs)
        except queue.Empty:
            if worker.blocked and not injected.is_set():
                # Force the producer between get(Empty) and the fallback scan.
                worker.session_changed('second-owner', 'pause')
                injected.set()
            raise
    worker._queue.get = get
    try:
        worker.track('first-owner', profile(), 'first')
        worker.track('second-owner', profile(), 'second')
        worker.session_changed('first-owner', 'pause')
        wait(injected, 'notification after empty queue read')
        assert worker.close()
        assert [c['expected_runtime_generation'] for c in supervisor.calls] == ['first', 'second']
    finally:
        worker.close()
