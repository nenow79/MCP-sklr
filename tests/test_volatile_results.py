"""C1: pamięciowe wyniki; sztuczny zegar, bez SQLite i procesów zewnętrznych."""
import threading

import pytest

from tasks import volatile_results as vr
from tasks.volatile_results import VolatileResult, VolatileResultError, VolatileResultStore

SECRET = "SENTINEL-PRIVATE-CONTENT"


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def factory():
    stores = []

    def make(**kwargs):
        clock = kwargs.pop("clock", Clock())
        store = VolatileResultStore(clock=clock, **kwargs)
        stores.append(store)
        return store, clock

    yield make
    for store in stores:
        store.close()


def publish(store, task_id=1, content=SECRET, check=None, **metadata):
    result = VolatileResult({"content": content, **metadata}, check)
    token = store.reserve(task_id, result)
    store.activate(task_id, token)
    return token


def test_reservation_hidden_and_ttl_starts_at_activation(factory):
    store, clock = factory(ttl_seconds=10)
    token = store.reserve(1, VolatileResult({"content": SECRET}))
    assert store.get(1) == {"state": "unavailable"}
    assert store._thread is None
    clock.advance(100)
    store.sweep()
    store.activate(1, token)
    assert store.get(1)["data"]["content"] == SECRET
    clock.advance(9)
    assert store.get(1)["state"] == "available"
    clock.advance(1)
    assert store.get(1) == {"state": "unavailable"}
    assert store._bytes == 0 and not store._entries


def test_repeated_get_does_not_consume_or_extend_ttl(factory):
    store, clock = factory(ttl_seconds=10)
    publish(store)
    for _ in range(3):
        clock.advance(3)
        assert store.get(1)["data"]["content"] == SECRET
    clock.advance(1)
    assert store.get(1)["state"] == "unavailable"


def test_input_and_output_mutations_do_not_change_stored_data(factory):
    store, _ = factory()
    data = {"content": SECRET, "selector": {"lines": [1, 2]}}
    token = store.reserve(1, VolatileResult(data))
    data["content"] = "changed"
    data["selector"]["lines"].append(3)
    store.activate(1, token)
    output = store.get(1)["data"]
    assert output == {"content": SECRET, "selector": {"lines": [1, 2]}}
    output["selector"]["lines"].clear()
    output["content"] = "changed"
    assert store.get(1)["data"] == {"content": SECRET, "selector": {"lines": [1, 2]}}


def test_repr_does_not_contain_content(factory):
    store, _ = factory()
    result = VolatileResult({"content": SECRET})
    store.reserve(1, result)
    assert SECRET not in repr(result)
    assert SECRET not in repr(store)
    assert SECRET not in repr(store._entries[1])


def test_count_limit_includes_reservations_without_eviction(factory):
    store, _ = factory(max_results=1)
    token = store.reserve(1, VolatileResult({"content": SECRET}))
    with pytest.raises(VolatileResultError, match="pełny"):
        store.reserve(2, VolatileResult({"content": "second"}))
    store.activate(1, token)
    assert store.get(1)["data"]["content"] == SECRET
    store.discard(1)
    publish(store, 2)
    assert store.get(2)["state"] == "available"


def test_total_limit_counts_utf8_and_metadata(factory):
    # Puste metadane kosztują 64 bajty; żółć = 8 bajtów UTF-8.
    store, _ = factory(max_total_bytes=72)
    publish(store, content="żółć")
    assert store._bytes == 72
    with pytest.raises(VolatileResultError, match="pełny"):
        store.reserve(2, VolatileResult({"content": ""}))
    assert store.get(1)["data"]["content"] == "żółć"


def test_expired_entries_release_capacity(factory):
    store, clock = factory(max_results=1, ttl_seconds=2)
    publish(store)
    clock.advance(2)
    publish(store, 2)
    assert store.get(1)["state"] == "unavailable"
    assert store.get(2)["state"] == "available"


def test_content_limits_no_truncation_and_unicode(factory, monkeypatch):
    store, _ = factory()
    monkeypatch.setattr(vr, "MAX_CONTENT_CHARS", 2)
    monkeypatch.setattr(vr, "MAX_CONTENT_BYTES", 8)
    publish(store, content="😀😀")
    assert store.get(1)["data"]["content"] == "😀😀"
    with pytest.raises(VolatileResultError, match="niczego nie obcięto"):
        store.reserve(2, VolatileResult({"content": "abc"}))
    monkeypatch.setattr(vr, "MAX_CONTENT_BYTES", 7)
    with pytest.raises(VolatileResultError, match="niczego nie obcięto"):
        store.reserve(2, VolatileResult({"content": "😀😀"}))
    assert store.get(2)["state"] == "unavailable"


@pytest.mark.parametrize("data", [
    {}, {"content": 1}, {"content": chr(0xD800)},
    {"content": "ok", "extra": object()},
    {"content": "ok", "extra": {1: "bad"}},
    {"content": "ok", "extra": 1 << 65},
    {"content": "ok", "extra": [None] * 257},
    {"content": "ok", "extra": [[[[["deep"]]]]]},
    {"content": "ok", "extra": "x" * vr.MAX_METADATA_BYTES},
])
def test_invalid_data_rejected_without_entry(factory, data):
    store, _ = factory()
    with pytest.raises(VolatileResultError):
        store.reserve(1, VolatileResult(data))
    assert not store._entries and store._bytes == 0


def test_invalid_callback_and_cyclic_metadata_rejected(factory):
    store, _ = factory()
    cyclic = []
    cyclic.append(cyclic)
    for result in (VolatileResult({"content": "ok"}, 5),
                   VolatileResult({"content": "ok", "extra": cyclic})):
        with pytest.raises(VolatileResultError):
            store.reserve(1, result)
    assert not store._entries


@pytest.mark.parametrize("task_id", [True, 0, -1, "1", None])
def test_invalid_reservation_id(factory, task_id):
    store, _ = factory()
    with pytest.raises(VolatileResultError):
        store.reserve(task_id, VolatileResult({"content": SECRET}))


def test_tokens_duplicate_and_discard(factory):
    store, _ = factory()
    token = store.reserve(1, VolatileResult({"content": SECRET}))
    with pytest.raises(VolatileResultError, match="już istnieje"):
        store.reserve(1, VolatileResult({"content": "other"}))
    with pytest.raises(VolatileResultError):
        store.activate(1, object())
    store.discard(1, object())
    store.activate(1, token)
    with pytest.raises(VolatileResultError, match="już opublikowany"):
        store.activate(1, token)
    store.discard(1, token)
    assert store.get(1)["state"] == "unavailable"


@pytest.mark.parametrize("answer", ["denied", False, ""])
def test_any_non_none_access_answer_denies_and_removes(factory, answer):
    store, _ = factory()
    publish(store, check=lambda: answer)
    assert store.get(1) == {"state": "denied"}
    assert store.get(1) == {"state": "unavailable"}
    assert store._bytes == 0


def test_callback_exception_does_not_leak_or_log(factory, caplog):
    store, _ = factory()

    def fail():
        raise RuntimeError(SECRET)

    publish(store, check=fail)
    caplog.clear()
    response = store.get(1)
    assert response == {"state": "denied"}
    assert SECRET not in repr(response) + caplog.text


def test_callback_runs_outside_store_lock(factory):
    store, _ = factory()
    acquired = []

    def check():
        def contender():
            got = store._lock.acquire(blocking=False)
            acquired.append(got)
            if got:
                store._lock.release()

        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
        return None

    publish(store, check=check)
    assert store.get(1)["state"] == "available"
    assert acquired == [True]


def test_expiry_during_callback_returns_no_content(factory):
    store, clock = factory(ttl_seconds=1)
    publish(store, check=lambda: clock.advance(1))
    assert store.get(1) == {"state": "unavailable"}


def test_replaced_entry_during_callback_not_returned(factory):
    store, _ = factory()

    def check():
        store.discard(1)
        publish(store, content="replacement")
        return None

    publish(store, check=check)
    assert store.get(1) == {"state": "unavailable"}
    assert store.get(1)["data"]["content"] == "replacement"


def test_close_clears_results_and_blocks_late_publication(factory):
    store, _ = factory()
    publish(store)
    token = store.reserve(2, VolatileResult({"content": SECRET}))
    thread = store._thread
    store.close()
    store.close()
    assert store.get(1) == {"state": "unavailable"}
    assert not store._entries and store._bytes == 0
    assert not thread.is_alive()
    with pytest.raises(VolatileResultError):
        store.activate(2, token)
    with pytest.raises(VolatileResultError, match="zamknięty"):
        store.reserve(3, VolatileResult({"content": SECRET}))


def test_close_during_callback_returns_no_content(factory):
    store, _ = factory()
    publish(store, check=store.close)
    assert store.get(1) == {"state": "unavailable"}


def test_thread_start_failure_keeps_reservation_hidden(factory, monkeypatch):
    store, _ = factory()
    token = store.reserve(1, VolatileResult({"content": SECRET}))

    def fail(_thread):
        raise RuntimeError("thread-start-failure")

    monkeypatch.setattr(vr.threading.Thread, "start", fail)
    with pytest.raises(RuntimeError, match="thread-start-failure"):
        store.activate(1, token)
    assert store.get(1) == {"state": "unavailable"}
    assert store._thread is None
    store.discard(1, token)
    assert store._bytes == 0


@pytest.mark.parametrize("kwargs", [
    {"ttl_seconds": 0}, {"ttl_seconds": True}, {"ttl_seconds": 301},
    {"ttl_seconds": float("nan")}, {"max_results": 0}, {"max_results": 51},
    {"max_results": True}, {"max_total_bytes": 0}, {"max_total_bytes": True},
    {"max_total_bytes": vr.MAX_TOTAL_BYTES + 1}, {"sweep_seconds": 0},
    {"sweep_seconds": 16}, {"sweep_seconds": True}, {"clock": None},
])
def test_invalid_store_limits(kwargs):
    with pytest.raises(VolatileResultError):
        VolatileResultStore(**kwargs)
