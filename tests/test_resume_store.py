"""P3b1 RAM preparation tests; confirmation here never opens a dispatch gate."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from server.session_resume import validate_resume_plan, validate_supplement
from tasks.resume_store import ResumeStore
from tasks.session_models import SessionConflict

SID = 'a' * 32
OTHER = 'b' * 32


def prepare(store):
    return store.prepare(SID, 4, validate_supplement('supplement-marker'))


def test_prepare_context_and_immutable_data():
    store = ResumeStore()
    item = prepare(store)
    assert store.get() is item and len(item.token) == 64
    view = item.to_context()
    assert view['supplement'] == 'supplement-marker'
    assert not {'checkpoint', 'checkpoint_missing'} & view.keys()
    view['supplement'] = 'changed'
    assert item.to_context()['supplement'] == 'supplement-marker'
    assert item.token not in repr(item) and 'supplement-marker' not in repr(item)
    with pytest.raises(SessionConflict):
        prepare(store)


def test_empty_supplement_and_clear():
    store = ResumeStore()
    first = store.prepare(SID, 1, validate_supplement(''))
    assert first.to_context()['supplement'] == ''
    store.clear()
    assert store.get() is None
    second = store.prepare(SID, 2, validate_supplement(''))
    assert second.token != first.token


@pytest.mark.parametrize('sid,revision,token', [(OTHER, 4, None), (SID, 3, None), (SID, 4, 'bad')])
def test_wrong_identity_revision_or_token_keeps_preparation(sid, revision, token):
    store = ResumeStore()
    item = prepare(store)
    with pytest.raises(SessionConflict):
        store.confirm(sid, revision, item.token if token is None else token, validate_resume_plan('Plan'))
    assert store.get() is item and item.plan is None


def test_confirm_once_and_old_token_cannot_confirm_new_preparation():
    store = ResumeStore()
    item = prepare(store)
    confirmed = store.confirm(SID, 4, item.token, validate_resume_plan('plan-marker'))
    assert confirmed.plan.text == 'plan-marker' and 'plan-marker' not in repr(confirmed)
    with pytest.raises(SessionConflict):
        store.confirm(SID, 4, item.token, validate_resume_plan('Replacement'))
    store.clear()
    current = prepare(store)
    with pytest.raises(SessionConflict):
        store.confirm(SID, 4, item.token, validate_resume_plan('Old'))
    assert store.get() is current


def test_requires_validated_types_and_identifiers():
    store = ResumeStore()
    with pytest.raises(ValueError):
        store.prepare('bad', 1, validate_supplement(''))
    with pytest.raises(ValueError):
        store.prepare(SID, True, validate_supplement(''))
    with pytest.raises(ValueError):
        store.prepare(SID, 1, 'raw text')
    with pytest.raises(TypeError):
        store.prepare(SID, 1, validate_supplement(''), {})
    item = prepare(store)
    with pytest.raises(ValueError):
        store.confirm(SID, 4, item.token, 'raw plan')
    assert store.get() is item


def test_concurrent_confirmation_has_one_winner():
    store = ResumeStore()
    item = prepare(store)
    def confirm(n):
        try:
            store.confirm(SID, 4, item.token, validate_resume_plan('Plan %d' % n))
            return True
        except SessionConflict:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(confirm, range(8)))
    assert sum(results) == 1 and store.get().plan is not None
