"""Detached P3a RAM store tests. No disk, no controller."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from tasks.checkpoint_store import MAX_ENTRIES, CheckpointStore

SID = 'a' * 32


def sid(n):
    return format(n, '032x')


def test_put_get_replace_discard():
    store = CheckpointStore()
    assert store.get(SID) is None and len(store) == 0
    first = store.put(SID, 2, 'tresc-1')
    assert first.session_id == SID and first.revision == 2 and first.saved_at
    assert store.get(SID).checkpoint == 'tresc-1'
    store.put(SID, 3, 'tresc-2')                    # last write wins, still one entry
    assert len(store) == 1 and store.get(SID).revision == 3
    assert store.discard(SID) is True and store.discard(SID) is False
    assert store.get(SID) is None


def test_clear_and_len():
    store = CheckpointStore()
    store.put(sid(1), 1, 'a')
    store.put(sid(2), 1, 'b')
    assert len(store) == 2
    store.clear()
    assert len(store) == 0


@pytest.mark.parametrize('bad', ['', 'x', 'A' * 32, 'a' * 31, None, 5])
def test_invalid_session_id_rejected(bad):
    store = CheckpointStore()
    with pytest.raises(ValueError):
        store.put(bad, 1, 'x')
    with pytest.raises(ValueError):
        store.get(bad)
    with pytest.raises(ValueError):
        store.discard(bad)


@pytest.mark.parametrize('bad', [0, -1, True, 1.0, '1', None])
def test_invalid_revision_rejected(bad):
    with pytest.raises(ValueError):
        CheckpointStore().put(SID, bad, 'x')


def test_checkpoint_is_required_and_count_is_capped():
    store = CheckpointStore()
    with pytest.raises(ValueError):
        store.put(SID, 1, None)
    for n in range(MAX_ENTRIES):
        store.put(sid(n), 1, 'x')
    with pytest.raises(ValueError):
        store.put(sid(MAX_ENTRIES), 1, 'x')
    store.put(sid(0), 2, 'y')                       # replacing an existing entry is allowed
    assert len(store) == MAX_ENTRIES


def test_repr_hides_content():
    store = CheckpointStore()
    item = store.put(SID, 1, 'tajna-tresc')
    assert 'tajna-tresc' not in repr(item)


def test_concurrent_puts_keep_a_single_consistent_entry():
    store = CheckpointStore()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: store.put(SID, n + 1, 'v%d' % n), range(100)))
    item = store.get(SID)
    assert len(store) == 1 and item.checkpoint == 'v%d' % (item.revision - 1)
