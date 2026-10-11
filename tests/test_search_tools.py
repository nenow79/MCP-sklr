"""Search: dosłowne dopasowania, scalanie kontekstu, maskowanie i budżety."""
import os
import subprocess

import pytest

from config_store import ConfigStore
from roots import MODE_RO, MODE_RW, Zones
from roots_registry import RootsRegistry
from server import search_tools
from server.file_tools import SKIP_DIRS
from server.search_tools import SEARCH_LIMIT_BYTES, search_code_impl
from state import load_config


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.json"
    central = tmp_path / "central"
    central.mkdir()
    store = ConfigStore(load_config(config_path), config_path)
    registry = RootsRegistry(store, zones=Zones(), central_dir=central)
    app, docs = tmp_path / "app", tmp_path / "docs"
    app.mkdir()
    docs.mkdir()
    registry.add(app, MODE_RW, alias="app")
    registry.add(docs, MODE_RO, alias="docs")

    class Env:
        pass

    result = Env()
    result.registry, result.app, result.docs, result.tmp = registry, app, docs, tmp_path
    return result


def put(base, filename, text):
    path = base / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


def data(response):
    assert response["success"], response
    return response["data"]


def test_literal_case_and_no_results(env):
    put(env.app, "a.py", "prefix A.*B suffix\naXXb\na.*b\n")
    result = data(search_code_impl(env.registry, "a.*b"))
    assert result["count"] == 2
    assert [item["match_lines"] for item in result["results"]] == [[1], [3]]
    assert result["results"][0]["content"] == "1: prefix A.*B suffix"
    sensitive = data(search_code_impl(env.registry, "a.*b", case_sensitive=True))
    assert sensitive["count"] == 1 and sensitive["results"][0]["match_lines"] == [3]
    empty = data(search_code_impl(env.registry, "not-found"))
    assert empty["count"] == 0 and empty["results"] == [] and not empty["truncated"]


def test_context_merges_overlapping_windows(env):
    put(env.app, "a.txt", "before\nhit\nmiddle\nhit\nafter\nfar\nfar\nfar\nhit\nend\n")
    result = data(search_code_impl(env.registry, "hit", context_lines=1))
    assert result["count"] == 3 and len(result["results"]) == 2
    first, second = result["results"]
    assert (first["start_line"], first["end_line"], first["total_lines"]) == (1, 5, 10)
    assert first["match_lines"] == [2, 4] and second["match_lines"] == [9]
    assert first["content"] == "1: before\n2: hit\n3: middle\n4: hit\n5: after"
    assert result["total_chars_returned"] == sum(len(item["content"]) for item in result["results"])


def test_context_clamping(env):
    put(env.app, "a.txt", "before\nhit\nafter\n")
    zero = data(search_code_impl(env.registry, "hit", context_lines=-1))
    assert zero["results"][0]["content"] == "2: hit"
    wide = data(search_code_impl(env.registry, "hit", context_lines=999))
    assert wide["results"][0]["start_line"] == 1 and wide["results"][0]["end_line"] == 3


def test_file_subpath_and_default_root_tolerance(env):
    path = put(env.app, "src/a.py", "hit")
    put(env.app, "b.py", "hit")
    for root in (None, "", False, "false"):
        result = data(search_code_impl(env.registry, "hit", subpath="src/a.py", root=root))
        assert result["count"] == 1 and result["results"][0]["file"] == "src/a.py"
    assert data(search_code_impl(env.registry, "hit", subpath=str(path)))["count"] == 1


def test_all_roots_and_no_default(env):
    put(env.app, "a.txt", "app hit")
    path = put(env.docs, "a.txt", "docs hit")
    result = data(search_code_impl(env.registry, "hit", root="*"))
    assert result["count"] == 2
    assert {item["root"] for item in result["results"]} == {"app", "docs"}
    assert data(search_code_impl(env.registry, "hit", root="docs"))["count"] == 1
    env.registry.remove("app")
    assert data(search_code_impl(env.registry, "hit", subpath=str(path)))["results"][0]["root"] == "docs"
    assert not search_code_impl(env.registry, "hit")["success"]
    assert data(search_code_impl(env.registry, "hit", root="*"))["count"] == 1


def test_missing_subpath_hints_and_multi_root_isolation(env):
    put(env.app, "src/a.py", "hit")
    response = search_code_impl(env.registry, "hit", subpath="missing")
    assert not response["success"] and "src/" in response["error"]
    result = data(search_code_impl(env.registry, "hit", subpath="src", root="*"))
    assert result["count"] == 1 and result["warnings"][0]["root"] == "docs"
    bad = search_code_impl(env.registry, "hit", subpath="missing", root="*")
    assert not bad["success"] and len(bad["data"]["warnings"]) == 2
    unknown = search_code_impl(env.registry, "hit", root="missing")
    assert not unknown["success"] and "app" in unknown["error"] and "docs" in unknown["error"]
    assert not search_code_impl(env.registry, "hit", subpath="../outside")["success"]


@pytest.mark.parametrize("kwargs", [
    {"query": ""}, {"query": None}, {"subpath": 1}, {"case_sensitive": "false"},
    {"context_lines": True}, {"max_matches": False}, {"max_total_chars": "24000"}, {"root": 5},
])
def test_invalid_types(env, kwargs):
    parameters = {"query": "hit", **kwargs}
    assert not search_code_impl(env.registry, **parameters)["success"]


def test_masking_before_matching_and_context(env):
    put(env.app, ".env", "TOKEN=UNIQUE_VALUE_987\nPUBLIC=hello\nAUTH=SECOND_VALUE_987\n")
    assert data(search_code_impl(env.registry, "UNIQUE_VALUE_987"))["count"] == 0
    assert data(search_code_impl(env.registry, "SECOND_VALUE_987"))["count"] == 0
    result = data(search_code_impl(env.registry, "PUBLIC", context_lines=1))
    item = result["results"][0]
    assert "TOKEN=***" in item["content"] and "AUTH=***" in item["content"]
    assert "VALUE_987" not in item["content"] and item["masked"] and item["masked_count"] == 2


def test_pem_masking_preserves_line_numbers(env):
    put(env.app, "a.pem", "-----BEGIN PRIVATE KEY-----\nSECRET_A\nSECRET_B\nSECRET_C\n"
        "-----END PRIVATE KEY-----\nafter\n")
    assert data(search_code_impl(env.registry, "SECRET_A"))["count"] == 0
    result = data(search_code_impl(env.registry, "after", context_lines=2))
    item = result["results"][0]
    assert item["match_lines"] == [6] and item["total_lines"] == 6
    assert "SECRET" not in item["content"] and item["masked"]


@pytest.mark.parametrize("filename", [".env.example", ".env.sample", ".env.template"])
def test_example_env_not_masked(env, filename):
    put(env.app, filename, "TOKEN=example_value")
    result = data(search_code_impl(env.registry, "example_value"))
    assert result["count"] == 1 and not result["results"][0]["masked"]


def test_skip_garbage_binary_large_and_blocked(env):
    for directory in SKIP_DIRS:
        put(env.app, directory + "/a.txt", "hit")
    put(env.app, "binary.bin", b"hit\x00")
    put(env.app, "large.txt", b"hit" + b"x" * SEARCH_LIMIT_BYTES)
    put(env.app, "cert.pfx", "hit")
    put(env.app, "cert.p12", "hit")
    for name in ("a.lock", "b.svg", "c.map"):
        put(env.app, name, "hit")
    result = data(search_code_impl(env.registry, "hit"))
    assert result["count"] == 3 and result["skipped_files"] == 4
    assert {item["file"] for item in result["results"]} == {"a.lock", "b.svg", "c.map"}
    assert data(search_code_impl(env.registry, "hit", subpath=".git"))["count"] == 0


def test_utf8_bom_and_cp1250(env):
    put(env.app, "a.txt", b"\xef\xbb\xbf" + "zażółć".encode("utf-8"))
    put(env.app, "b.txt", "zażółć".encode("cp1250"))
    assert data(search_code_impl(env.registry, "zażółć"))["count"] == 2


def test_match_limit_shared_between_roots(env):
    put(env.app, "a.txt", "hit\nhit\nhit\n")
    put(env.docs, "b.txt", "hit")
    result = data(search_code_impl(env.registry, "hit", max_matches=2, root="*"))
    assert result["count"] == 2 and result["truncated_reason"] == "max_matches"
    assert result["results"][0]["match_lines"] == [1, 2]
    assert data(search_code_impl(env.registry, "hit", max_matches=-1))["count"] == 1


def test_character_budget_and_counters(env):
    put(env.app, "a.txt", "hit" + "x" * 2000)
    result = data(search_code_impl(env.registry, "hit", max_total_chars=1000))
    assert result["count"] == 1 and result["total_chars_returned"] == 1000
    assert result["truncated_reason"] == "max_total_chars" and result["results"][0]["truncated"]
    assert len(result["results"][0]["content"]) == 1000


def test_time_budget_returns_previous_results_without_sleep(env, monkeypatch):
    put(env.app, "a.txt", "hit")
    put(env.app, "b.txt", "hit")
    clock = [0.0]
    original = search_tools._read_text

    def read(path):
        if path.name == "b.txt":
            clock[0] = 7.0
        return original(path)

    monkeypatch.setattr(search_tools.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(search_tools, "_read_text", read)
    result = data(search_code_impl(env.registry, "hit"))
    assert result["count"] == 1 and result["results"][0]["file"] == "a.txt"
    assert result["truncated"] and result["truncated_reason"] == "time_budget"


def test_symlinks_skipped_including_explicit_subpath(env):
    target = put(env.app, "real/a.txt", "hit")
    outside = put(env.tmp, "outside.txt", "hit")
    try:
        os.symlink(target, env.app / "link.txt")
        os.symlink(target.parent, env.app / "linked", target_is_directory=True)
        os.symlink(outside, env.app / "outside-link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("brak uprawnień do symlinków")
    result = data(search_code_impl(env.registry, "hit"))
    assert result["count"] == 1 and result["results"][0]["file"] == "real/a.txt"
    assert not search_code_impl(env.registry, "hit", subpath="link.txt")["success"]
    assert not search_code_impl(env.registry, "hit", subpath="linked/a.txt")["success"]


@pytest.mark.skipif(os.name != "nt", reason="junction specyficzny dla Windows")
def test_junction_skipped_including_explicit_subpath(env):
    target = put(env.app, "real/a.txt", "hit")
    link = env.app / "junction"
    command = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target.parent)],
                             capture_output=True, text=True)
    if command.returncode:
        pytest.skip("nie można utworzyć junctiona")
    assert data(search_code_impl(env.registry, "hit"))["count"] == 1
    assert not search_code_impl(env.registry, "hit", subpath="junction/a.txt")["success"]
