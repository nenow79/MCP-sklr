"""Worker na sztucznych callbackach; bez produkcyjnej bazy ani serwera."""
import threading

import pytest

from okf_engine.cache_worker import CacheWorker, WorkerError


def test_batches_and_resource_share_owner_thread():
    finished = threading.Event()
    owners, cursors = [], []

    def factory():
        owners.append(threading.get_ident())
        return object()

    def step(resource, item, valid):
        owners.append(threading.get_ident())
        assert valid()
        cursors.append(item.cursor)
        return len(cursors) == 3, len(cursors)

    def dispose(resource):
        owners.append(threading.get_ident())
        finished.set()

    worker = CacheWorker(factory, step, dispose)
    worker.start()
    worker.submit('bundle')
    # Event ustawiamy po trzeciej partii, nie czekamy na czas wall-clock.
    with worker._condition:
        assert worker._condition.wait_for(lambda: worker.status()['completed'] == 1, timeout=5)
    assert worker.close()
    assert finished.is_set() and cursors == [None, 1, 2]
    assert len(set(owners)) == 1 and owners[0] != threading.get_ident()


def test_new_submission_invalidates_active_ticket():
    entered, release, complete = threading.Event(), threading.Event(), threading.Event()
    tickets = []

    def step(resource, item, valid):
        tickets.append(item.ticket)
        if len(tickets) == 1:
            entered.set()
            assert release.wait(5)
            assert not valid()
            return False, 'obsolete cursor'
        assert item.cursor is None and valid()
        complete.set()
        return True, None

    worker = CacheWorker(lambda: None, step, lambda resource: None)
    worker.start()
    try:
        first = worker.submit('bundle')
        assert entered.wait(5)
        second = worker.submit('bundle')
        release.set()
        assert complete.wait(5)
        assert tickets == [first, second]
    finally:
        release.set()
        assert worker.close()


def test_overflow_and_cancel_mark_state():
    entered, release = threading.Event(), threading.Event()
    validity = []

    def step(resource, item, valid):
        entered.set()
        assert release.wait(5)
        validity.append(valid())
        return True, None

    worker = CacheWorker(lambda: None, step, lambda resource: None, capacity=1)
    worker.start()
    try:
        worker.submit('first')
        assert entered.wait(5)
        with pytest.raises(WorkerError, match='WORKER_QUEUE_FULL'):
            worker.submit('other')
        assert worker.status()['reconcile_required']
        assert worker.cancel('first')
        release.set()
    finally:
        release.set()
        assert worker.close()
    assert validity == [False]


def test_shutdown_does_not_claim_to_kill_blocked_io():
    entered, release = threading.Event(), threading.Event()

    def step(resource, item, valid):
        entered.set()
        assert release.wait(5)
        assert not valid()
        return True, None

    worker = CacheWorker(lambda: None, step, lambda resource: None)
    worker.start()
    try:
        worker.submit('bundle')
        assert entered.wait(5)
        assert worker.close(timeout=0) is False
        with pytest.raises(WorkerError, match='WORKER_UNAVAILABLE'):
            worker.submit('later')
    finally:
        release.set()
        assert worker.close()


def test_callback_error_is_generic_and_other_work_can_continue():
    entered = threading.Event()

    def step(resource, item, valid):
        entered.set()
        raise RuntimeError('SECRET_SENTINEL')

    worker = CacheWorker(lambda: None, step, lambda resource: None)
    worker.start()
    try:
        worker.submit('bundle')
        assert entered.wait(5)
        with worker._condition:
            assert worker._condition.wait_for(lambda: worker.status()['error'] is not None, timeout=5)
        assert worker.status()['error'] == 'WORKER_STEP_FAILED'
        assert 'SENTINEL' not in str(worker.status())
    finally:
        assert worker.close()


@pytest.mark.parametrize('capacity', [True, 0, 5001])
def test_invalid_capacity(capacity):
    with pytest.raises(WorkerError, match='WORKER_CAPACITY_INVALID'):
        CacheWorker(lambda: None, lambda *args: (True, None), lambda resource: None, capacity=capacity)
