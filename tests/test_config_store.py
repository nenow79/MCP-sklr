#!/usr/bin/env python3
"""Testy ConfigStore: wspólny lock z TrustStore, atomowy zapis, wycofanie przy błędzie."""
import json
import threading

import pytest

from config_store import ConfigStore
from skills_engine.trust import TrustStore
from state import DEFAULT_CONFIG, load_config


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "config.json"
    return ConfigStore(load_config(path), path), path


def test_update_saves_file(store):
    cs, path = store
    cs.update(lambda c: c["skills"]["disabled"].append("x"))
    assert json.loads(path.read_text(encoding="utf-8"))["skills"]["disabled"] == ["x"]
    assert cs.data["skills"]["disabled"] == ["x"]


def test_update_returns_mutator_result(store):
    cs, _ = store
    assert cs.update(lambda c: 42) == 42


def test_mutator_error_rolls_back_in_place_and_does_not_save(store):
    cs, path = store
    reference = cs.data  # inni użytkownicy trzymają referencję do tego samego słownika

    def bad(config):
        config["skills"]["disabled"].append("y")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        cs.update(bad)
    assert reference is cs.data
    assert cs.data["skills"]["disabled"] == []
    assert not path.exists()


def test_save_error_rolls_back(store, monkeypatch):
    cs, _ = store

    def failing_save(config, path=None):
        raise OSError("disk full")

    monkeypatch.setattr("config_store.save_config", failing_save)
    with pytest.raises(OSError):
        cs.update(lambda c: c["skills"]["disabled"].append("z"))
    assert cs.data["skills"]["disabled"] == []


def test_snapshot_is_deep_copy(store):
    cs, _ = store
    snap = cs.snapshot()
    snap["skills"]["disabled"].append("q")
    assert cs.data["skills"]["disabled"] == []


def test_defaults_untouched(store):
    cs, _ = store
    cs.update(lambda c: c["skills"]["disabled"].append("w"))
    assert DEFAULT_CONFIG["skills"]["disabled"] == []


def test_trust_store_uses_shared_lock(store):
    cs, _ = store
    trust = TrustStore(cs.data, save=cs.save, lock=cs.lock)
    assert trust._lock is cs.lock
    trust.trust("a", "h1")
    assert cs.data["skills"]["trust"]["a"]["hash"] == "h1"


def test_trust_store_default_lock_is_backward_compatible():
    trust = TrustStore({}, save=None)
    trust.trust("a", "h")
    assert trust.is_trusted("a", "h")


def test_concurrent_trust_and_update_keep_valid_json(store):
    cs, path = store
    trust = TrustStore(cs.data, save=cs.save, lock=cs.lock)
    errors = []

    def trust_worker():
        try:
            for i in range(60):
                trust.trust(f"skill{i}", f"h{i}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def update_worker():
        try:
            for i in range(60):
                cs.update(lambda c, i=i: c["skills"]["disabled"].append(f"d{i}"))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=trust_worker), threading.Thread(target=update_worker),
               threading.Thread(target=trust_worker), threading.Thread(target=update_worker)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert len(loaded["skills"]["trust"]) == 60
    assert len(loaded["skills"]["disabled"]) == 120
    assert not (path.parent / "config.json.tmp").exists()
