"""Paczka B: odczyt, listowanie i batch w roots; bez FastMCP."""
import os
from pathlib import Path

import pytest

from config_store import ConfigStore
from roots import MODE_RO, MODE_RW, Zones
from roots_registry import RootsRegistry
from server.file_tools import (
    DIRECTORY_LIMIT, READ_LIMIT_BYTES, SKIP_DIRS, list_directory_impl,
    read_file_impl, read_files_batch_impl,
)
from state import load_config


@pytest.fixture
def env(tmp_path):
    config_path = tmp_path / "config.json"
    store = ConfigStore(load_config(config_path), config_path)
    central = tmp_path / "central"
    central.mkdir()
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


def put(base, filename, content):
    path = base / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


def data(response):
    assert response["success"], response
    return response["data"]


def test_read_default_and_root_tolerance(env):
    put(env.app, "a.txt", "pierwsza\ndruga\n")
    for root in (None, "", False, "false"):
        result = data(read_file_impl(env.registry, "a.txt", root=root))
        assert result["root"] == "app" and result["filepath"] == "a.txt"
        assert result["content"] == "pierwsza\ndruga\n"
        assert result["total_lines"] == 2 and result["eol"] == "LF"
        assert not result["truncated"] and not result["masked"]
        assert result["masked_count"] == 0


def test_read_ro_and_absolute_without_default(env):
    path = put(env.docs, "a.txt", "docs")
    assert data(read_file_impl(env.registry, "a.txt", root="docs"))["content"] == "docs"
    env.registry.remove("app")
    assert data(read_file_impl(env.registry, str(path)))["root"] == "docs"
    assert not read_file_impl(env.registry, "a.txt")["success"]


@pytest.mark.parametrize("content, expected, eol, lines", [
    (b"", "", "none", 0), (b"x", "x", "none", 1),
    (b"a\r\nb\r\n", "a\r\nb\r\n", "CRLF", 2),
    (b"a\nb\n", "a\nb\n", "LF", 2), (b"a\rb\r", "a\rb\r", "CR", 2),
    (b"a\r\nb\nc\r", "a\r\nb\nc\r", "mixed", 3),
    (b"\xef\xbb\xbfhello\n", "hello\n", "LF", 1),
    ("zażółć\r\n".encode("cp1250"), "zażółć\r\n", "CRLF", 1),
])
def test_encodings_empty_and_eol(env, content, expected, eol, lines):
    put(env.app, "a.txt", content)
    result = data(read_file_impl(env.registry, "a.txt"))
    assert result["content"] == expected and result["eol"] == eol
    assert result["total_lines"] == lines


def test_line_range_and_numbers(env):
    put(env.app, "a.txt", "one\r\ntwo\r\nthree\r\nfour")
    result = data(read_file_impl(env.registry, "a.txt", start_line=2, end_line=3, line_numbers=True))
    assert result["content"] == "2: two\r\n3: three\r\n"
    assert result["total_lines"] == 4 and result["start_line"] == 2 and result["end_line"] == 3
    assert not result["truncated"]
    assert data(read_file_impl(env.registry, "a.txt", start_line=20))["content"] == ""
    assert data(read_file_impl(env.registry, "a.txt", start_line=-1, end_line=1))["content"] == "one\r\n"
    assert not read_file_impl(env.registry, "a.txt", start_line=3, end_line=2)["success"]


def test_character_limits_and_clamping(env):
    put(env.app, "a.txt", "ą" * 81000)
    result = data(read_file_impl(env.registry, "a.txt"))
    assert len(result["content"]) == 12000 and result["truncated"]
    assert len(data(read_file_impl(env.registry, "a.txt", max_chars=1))["content"]) == 1000
    assert len(data(read_file_impl(env.registry, "a.txt", max_chars=100000))["content"]) == 80000
    put(env.app, "exact.txt", "x" * 1000)
    assert not data(read_file_impl(env.registry, "exact.txt", max_chars=1000))["truncated"]


@pytest.mark.parametrize("kwargs", [
    {"max_chars": True}, {"max_chars": "12000"}, {"start_line": False},
    {"end_line": 1.5}, {"line_numbers": "false"}, {"root": 1},
])
def test_read_rejects_invalid_parameter_types(env, kwargs):
    put(env.app, "a.txt", "x")
    assert not read_file_impl(env.registry, "a.txt", **kwargs)["success"]


def test_read_path_errors(env):
    put(env.app, "a.txt", "x")
    for path in ("", "a.txt ", "../outside.txt", "missing.txt", ".", 1):
        assert not read_file_impl(env.registry, path)["success"]
    error = read_file_impl(env.registry, "a.txt", root="missing")
    assert not error["success"] and "app" in error["error"] and "docs" in error["error"]
    outside = put(env.tmp, "outside.txt", "outside")
    assert not read_file_impl(env.registry, str(outside))["success"]


@pytest.mark.parametrize("payload", [b"hello\x00world", b"\x89PNG\r\n\x1a\n", b"x\x01y", b"\xff\xfeh\x00"])
def test_binary_files_rejected(env, payload):
    put(env.app, "a.bin", payload)
    assert not read_file_impl(env.registry, "a.bin")["success"]


def test_read_size_limit(env):
    put(env.app, "limit.txt", b"x" * READ_LIMIT_BYTES)
    assert read_file_impl(env.registry, "limit.txt")["success"]
    put(env.app, "large.txt", b"x" * (READ_LIMIT_BYTES + 1))
    response = read_file_impl(env.registry, "large.txt")
    assert not response["success"] and "5 MB" in response["error"]


@pytest.mark.parametrize("filename", ["cert.pfx", "cert.P12"])
def test_blocked_secret_even_when_text(env, filename):
    put(env.app, filename, "not actually binary")
    assert not read_file_impl(env.registry, filename)["success"]


def test_masking_before_range_and_limit(env):
    put(env.app, ".env", "TOKEN=" + "secret" * 3000 + "\nNORMAL=ok\nPASSWORD=hidden\n")
    result = data(read_file_impl(env.registry, ".env", end_line=1, max_chars=1000))
    assert result["content"] == "TOKEN=***\n" and not result["truncated"]
    assert result["masked"] and result["masked_count"] == 2 and result["total_lines"] == 3
    assert "secret" not in result["content"]
    selected = data(read_file_impl(env.registry, ".env", start_line=3, line_numbers=True))
    assert selected["content"] == "3: PASSWORD=***\n"


@pytest.mark.parametrize("filename", [".env.example", ".env.sample", ".env.template"])
def test_example_exceptions(env, filename):
    put(env.app, filename, "TOKEN=example\n")
    result = data(read_file_impl(env.registry, filename))
    assert result["content"] == "TOKEN=example\n" and not result["masked"]


def test_masked_pem_keeps_physical_line_positions(env):
    original = ("before\n-----BEGIN PRIVATE KEY-----\nSECRET1\nSECRET2\nSECRET3\n"
                "-----END PRIVATE KEY-----\nafter\n")
    put(env.app, "a.pem", original)
    all_lines = data(read_file_impl(env.registry, "a.pem"))
    assert all_lines["total_lines"] == 7 and all_lines["masked_count"] == 1
    assert "SECRET" not in all_lines["content"] and "[ZAMASKOWANO]" in all_lines["content"]
    selected = data(read_file_impl(env.registry, "a.pem", start_line=7, line_numbers=True))
    assert selected["content"] == "7: after\n"
    body = data(read_file_impl(env.registry, "a.pem", start_line=3, end_line=5))
    assert "SECRET" not in body["content"]


def test_raw_key_fully_masked(env):
    put(env.app, "a.key", "SECRET1\nSECRET2\nSECRET3")
    result = data(read_file_impl(env.registry, "a.key"))
    assert result["masked"] and "SECRET" not in result["content"]
    assert result["total_lines"] == 3


def test_directory_one_level_sorted_and_skips_garbage(env):
    put(env.app, "z.txt", "z")
    put(env.app, "a.txt", "a")
    put(env.app, "folder/nested.txt", "n")
    put(env.app, ".env", "TOKEN=secret")  # nazw plików nie ukrywamy
    for folder in SKIP_DIRS:
        (env.app / folder).mkdir()
    result = data(list_directory_impl(env.registry))
    assert result["entries"] == ["folder/", ".env", "a.txt", "z.txt"]
    assert result["count"] == 4 and not result["truncated"]
    assert result["subpath"] == "." and result["root"] == "app"
    assert data(list_directory_impl(env.registry, "folder"))["entries"] == ["nested.txt"]
    assert data(list_directory_impl(env.registry, None))["subpath"] == "."


def test_directory_ro_and_errors(env):
    put(env.docs, "a.txt", "docs")
    assert data(list_directory_impl(env.registry, root="docs"))["entries"] == ["a.txt"]
    for subpath in ("../", "missing", 1):
        assert not list_directory_impl(env.registry, subpath)["success"]
    assert not list_directory_impl(env.registry, "a.txt", root="docs")["success"]
    assert not list_directory_impl(env.registry, root="missing")["success"]


def test_directory_limit(env):
    for number in range(DIRECTORY_LIMIT + 1):
        put(env.app, f"{number:04}.txt", "")
    result = data(list_directory_impl(env.registry))
    assert result["count"] == DIRECTORY_LIMIT and result["truncated"]


def test_symlink_outside_not_listed_or_read(env):
    outside = put(env.tmp, "outside.txt", "secret")
    try:
        os.symlink(outside, env.app / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("brak uprawnień do symlinków")
    assert "link.txt" not in data(list_directory_impl(env.registry))["entries"]
    assert not read_file_impl(env.registry, "link.txt")["success"]


def test_batch_ro_per_entry_and_error_isolation(env):
    put(env.app, "a.txt", "app")
    put(env.docs, "a.txt", "docs")
    result = data(read_files_batch_impl(env.registry, [
        {"filepath": "a.txt"}, {"filepath": "missing"},
        {"filepath": "a.txt", "root": "docs"}, "invalid", {"no_path": "x"},
    ]))
    entries = result["results"]
    assert [entry["success"] for entry in entries] == [True, False, True, False, False]
    assert entries[0]["data"]["content"] == "app" and entries[2]["data"]["content"] == "docs"
    assert [entry["index"] for entry in entries] == list(range(5))
    assert result["total_chars_returned"] == 7 and not result["truncated"]


def test_batch_shared_budget_below_individual_minimum(env):
    put(env.app, "a.txt", "a" * 900)
    put(env.app, "b.txt", "b" * 300)
    put(env.app, "c.txt", "c")
    result = data(read_files_batch_impl(env.registry, [
        {"filepath": "a.txt"}, {"filepath": "b.txt"}, {"filepath": "c.txt"},
    ], max_total_chars=1000))
    entries = result["results"]
    assert [len(entry["data"]["content"]) for entry in entries] == [900, 100, 0]
    assert not entries[0]["data"]["truncated"]
    assert entries[1]["data"]["truncated"] and entries[2]["data"]["truncated"]
    assert result["total_chars_returned"] == 1000 and result["truncated"]


def test_batch_default_budget_and_numbering_cost(env):
    put(env.app, "a.txt", "x" * 20000)
    result = data(read_files_batch_impl(env.registry, [
        {"filepath": "a.txt"}, {"filepath": "a.txt"}, {"filepath": "a.txt"},
    ]))
    assert result["max_total_chars"] == 24000 and result["total_chars_returned"] == 24000
    assert [len(entry["data"]["content"]) for entry in result["results"]] == [12000, 12000, 0]
    numbered = data(read_files_batch_impl(env.registry, [
        {"filepath": "a.txt", "line_numbers": True},
    ], max_total_chars=1000))
    assert numbered["total_chars_returned"] == 1000
    assert numbered["results"][0]["data"]["content"].startswith("1: ")


def test_batch_masking_and_validation_errors(env):
    put(env.app, ".env", "AUTH=password\n")
    put(env.app, "a.txt", "x")
    result = data(read_files_batch_impl(env.registry, [
        {"filepath": ".env"}, {"filepath": "a.txt", "max_chars": True},
        {"filepath": "a.txt", "unexpected": 1}, {"filepath": "a.txt"},
    ]))
    assert result["results"][0]["data"]["content"] == "AUTH=***\n"
    assert result["results"][0]["data"]["masked"]
    assert [entry["success"] for entry in result["results"]] == [True, False, False, True]
    for requests in (None, {}, [], [{"filepath": "a.txt"}] * 11):
        assert not read_files_batch_impl(env.registry, requests)["success"]
    for budget in (True, "24000", None):
        assert not read_files_batch_impl(env.registry, [{"filepath": "a.txt"}], budget)["success"]
    assert data(read_files_batch_impl(env.registry, [{"filepath": "a.txt"}], 1))["max_total_chars"] == 1000
    assert data(read_files_batch_impl(env.registry, [{"filepath": "a.txt"}], 999999))["max_total_chars"] == 80000
