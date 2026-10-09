import json
import threading
import time

from skills_engine.trust import TrustStore, compute_skill_hash


def _skill(tmp_path, body="body\n", script="print(1)\n"):
    base = tmp_path / "demo"
    (base / "scripts").mkdir(parents=True, exist_ok=True)
    (base / "SKILL.md").write_text(f"---\nname: demo\ndescription: d\n---\n{body}", encoding="utf-8")
    (base / "scripts" / "run.py").write_text(script, encoding="utf-8")
    return base


def test_hash_is_stable(tmp_path):
    base = _skill(tmp_path)
    assert compute_skill_hash(base) == compute_skill_hash(base)
    assert len(compute_skill_hash(base)) == 64


def test_hash_changes_when_script_changes(tmp_path):
    base = _skill(tmp_path)
    before = compute_skill_hash(base)
    (base / "scripts" / "run.py").write_text("print(2)\n", encoding="utf-8")
    assert compute_skill_hash(base) != before


def test_hash_changes_when_skill_md_changes(tmp_path):
    base = _skill(tmp_path)
    before = compute_skill_hash(base)
    (base / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nchanged\n", encoding="utf-8")
    assert compute_skill_hash(base) != before


def test_hash_changes_when_script_added(tmp_path):
    base = _skill(tmp_path)
    before = compute_skill_hash(base)
    (base / "scripts" / "extra.py").write_text("x=1\n", encoding="utf-8")
    assert compute_skill_hash(base) != before


def test_hash_changes_when_script_renamed(tmp_path):
    base = _skill(tmp_path)
    before = compute_skill_hash(base)
    (base / "scripts" / "run.py").rename(base / "scripts" / "run2.py")
    assert compute_skill_hash(base) != before


def test_hash_ignores_references_and_assets(tmp_path):
    base = _skill(tmp_path)
    before = compute_skill_hash(base)
    (base / "references").mkdir()
    (base / "references" / "g.md").write_text("doc", encoding="utf-8")
    (base / "assets").mkdir()
    (base / "assets" / "a.txt").write_text("asset", encoding="utf-8")
    assert compute_skill_hash(base) == before


def test_hash_ignores_pycache(tmp_path):
    base = _skill(tmp_path)
    before = compute_skill_hash(base)
    (base / "scripts" / "__pycache__").mkdir()
    (base / "scripts" / "__pycache__" / "run.cpython-314.pyc").write_bytes(b"\x00bytecode")
    (base / "scripts" / "stray.pyc").write_bytes(b"\x00")
    assert compute_skill_hash(base) == before


def test_hash_ignores_line_endings(tmp_path):
    base = _skill(tmp_path)
    (base / "scripts" / "run.py").write_bytes(b"a = 1\nb = 2\n")
    lf = compute_skill_hash(base)
    (base / "scripts" / "run.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    assert compute_skill_hash(base) == lf


def test_hash_without_scripts_dir(tmp_path):
    base = tmp_path / "plain"
    base.mkdir()
    (base / "SKILL.md").write_text("---\nname: plain\ndescription: d\n---\n", encoding="utf-8")
    assert len(compute_skill_hash(base)) == 64


def test_trust_store_roundtrip_and_save_callback():
    config = {"skills": {"trust": {}}}
    saved = []
    store = TrustStore(config, save=lambda: saved.append(1))

    assert store.is_trusted("demo", "abc") is False
    store.trust("demo", "abc")
    assert store.is_trusted("demo", "abc") is True
    assert store.is_trusted("demo", "other") is False
    assert config["skills"]["trust"]["demo"]["hash"] == "abc"
    assert "trusted_at" in config["skills"]["trust"]["demo"]
    assert saved == [1]


def test_trust_store_revoke():
    config = {"skills": {"trust": {}}}
    store = TrustStore(config)
    store.trust("demo", "abc")
    assert store.revoke("demo") is True
    assert store.is_trusted("demo", "abc") is False
    assert store.revoke("demo") is False


def test_trust_store_creates_missing_sections():
    config = {}
    store = TrustStore(config)
    assert store.is_trusted("x", "y") is False
    store.trust("x", "y")
    assert config["skills"]["trust"]["x"]["hash"] == "y"


def test_trust_store_tolerates_malformed_entry():
    config = {"skills": {"trust": {"demo": "garbage"}}}
    assert TrustStore(config).is_trusted("demo", "abc") is False


def _run_threads(targets):
    errors = []

    def wrap(fn):
        def run():
            try:
                fn()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
        return run

    threads = [threading.Thread(target=wrap(t)) for t in targets]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return errors


def test_trust_store_concurrent_trust_all_entries_present():
    config = {}
    snapshots = []
    store = TrustStore(config, save=lambda: snapshots.append(json.dumps(config)))

    def worker(w):
        def run():
            for i in range(25):
                store.trust(f"skill-{w}-{i}", f"hash-{w}-{i}")
        return run

    errors = _run_threads([worker(w) for w in range(8)])
    assert not errors
    assert len(config["skills"]["trust"]) == 200
    assert len(snapshots) == 200


def test_trust_store_save_is_never_concurrent():
    config = {}
    state = {"inside": 0, "max": 0}

    def slow_save():
        state["inside"] += 1
        state["max"] = max(state["max"], state["inside"])
        time.sleep(0.002)
        state["inside"] -= 1

    store = TrustStore(config, save=slow_save)

    def worker(w):
        def run():
            for i in range(10):
                store.trust(f"s-{w}-{i}", "h")
        return run

    errors = _run_threads([worker(w) for w in range(6)])
    assert not errors
    assert state["max"] == 1


def test_trust_store_concurrent_read_write_same_name():
    config = {}
    store = TrustStore(config, save=lambda: json.dumps(config))

    def writer():
        for i in range(50):
            store.trust("demo", f"h{i}")
            store.revoke("demo")

    def reader():
        for i in range(200):
            store.is_trusted("demo", f"h{i % 50}")

    errors = _run_threads([writer, writer, reader, reader])
    assert not errors
