"""Advisory catalogue scanning cannot hold session/config/dispatch authority."""
from concurrent.futures import ThreadPoolExecutor
import math
import threading

import pytest

from autopilot_fixtures import environment
from server.autopilot_admin import AutopilotAdmin
from server.autopilot_resources import resource_choices
from server.panel_timing import PHASES, timing_header
from server.session_admin import SessionAdmin
from skills_engine.registry import SkillRegistry
from tasks.session_models import SessionConflict


@pytest.fixture
def env(tmp_path):
    with environment(tmp_path) as e:
        e.admin = AutopilotAdmin(e.runtime)
        e.session_admin = SessionAdmin(e.session_store, e.sessions, e.roots)
        e.session_admin.attach_manager(e.manager)
        yield e


def hold_scan(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = SkillRegistry.rescan
    def scan(registry):
        entered.set()
        assert release.wait(5), 'Test did not release catalogue scan'
        return original(registry)
    monkeypatch.setattr(SkillRegistry, 'rescan', scan)
    return entered, release


def test_slow_catalogue_allows_roots_status_and_pause(env, monkeypatch):
    entered, release = hold_scan(monkeypatch)
    active = env.session_admin.status()
    with ThreadPoolExecutor(max_workers=4) as pool:
        scan = pool.submit(env.admin.resources)
        try:
            assert entered.wait(2)
            assert pool.submit(env.roots.list_status).result(timeout=2)
            assert pool.submit(env.admin.status).result(timeout=2)['session_id'] == active['session_id']
            paused = pool.submit(env.session_admin.pause, active['session_id'], active['revision']).result(timeout=2)
            assert paused['state'] == 'paused'
            assert not scan.done()
            # A duplicate catalogue request must not consume a waiting worker.
            with pytest.raises(SessionConflict):
                env.admin.resources()
        finally:
            release.set()
        with pytest.raises(SessionConflict):
            scan.result(timeout=2)  # Result was prepared for the old active revision.


def test_old_scan_cannot_publish_after_end_and_fresh_start(env, monkeypatch):
    entered, release = hold_scan(monkeypatch)
    old = env.session_admin.status()
    with ThreadPoolExecutor(max_workers=2) as pool:
        scan = pool.submit(env.admin.resources)
        try:
            assert entered.wait(2)
            ended = pool.submit(env.session_admin.end, old['session_id'], old['revision']).result(timeout=2)
            assert ended['session_id'] is None
            fresh = pool.submit(env.session_admin.start, ['other']).result(timeout=2)
            assert fresh['session_id'] != old['session_id']
        finally:
            release.set()
        with pytest.raises(SessionConflict):
            scan.result(timeout=2)
        assert env.session_admin.status() == fresh


@pytest.mark.parametrize('source', ['config', 'skills'])
def test_change_and_restore_during_scan_is_rejected(env, monkeypatch, source):
    entered, release = hold_scan(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scan = pool.submit(env.admin.resources)
        try:
            assert entered.wait(2)
            if source == 'config':
                env.store.update(lambda data: data.update(catalog_probe=True))
                env.store.update(lambda data: data.pop('catalog_probe'))
            else:
                env.skills.set_disabled(['temporary'])
                env.skills.set_disabled([])
        finally:
            release.set()
        with pytest.raises(SessionConflict):
            scan.result(timeout=2)


def test_catalogue_preserves_authority_generations_live_registry_and_journal(env):
    skill = env.central / 'demo'
    skill.mkdir()
    (skill / 'SKILL.md').write_text('---\nname: demo\ndescription: Demo skill.\n---\nDemo\n')
    before_config = env.store.snapshot()
    before_registry = env.skills.snapshot()
    before_rows = dict(env.runtime.generations._rows)
    before_revision = env.runtime.generations.revision
    timings = {}
    choices = env.admin.resources(timings)
    assert any(row['key'] == 'skill:demo' for row in choices)
    assert env.store.snapshot() == before_config
    assert env.skills.snapshot() == before_registry
    assert env.runtime.generations._rows == before_rows
    assert env.runtime.generations.revision == before_revision
    assert env.runtime.skills._known == set()
    assert env.audit.history() == () and not env.autopilot.engaged
    assert set(timings) <= PHASES
    assert all(type(value) is float and math.isfinite(value) and value >= 0 for value in timings.values())
    with env.store.lock:
        _, authoritative = resource_choices(env.runtime)
    assert choices == authoritative  # Same editor keys; no private references are returned.


def test_failed_scan_releases_only_its_own_lock(env, monkeypatch):
    def fail(*args):
        raise OSError('PRIVATE-PATH')
    with monkeypatch.context() as patch:
        patch.setattr(SkillRegistry, 'rescan', fail)
        with pytest.raises(OSError):
            env.admin.resources()
    assert env.admin.resources()
    assert env.session_admin.status()['state'] == 'active'


def test_timing_header_uses_only_fixed_names_and_finite_numbers():
    assert timing_header({'roots': 2.5, 'private path': 10, 'app': float('nan'),
                          'catalog_scan': 'SECRET', 'catalog_wait': True}) == 'roots;dur=2.50'
