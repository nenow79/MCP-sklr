"""Bootstrap wyłącznie tmp_path; brak importu main i produkcyjnej bazy."""
import pytest

from okf_engine.cache_bootstrap import open_application_cache
from okf_engine.cache_store import CacheError, default_cache_paths


def test_bootstrap_creates_local_files_and_reopens(tmp_path):
    db, key = default_cache_paths(tmp_path)
    assert not db.exists() and not key.exists()
    backend = open_application_cache(tmp_path)
    try:
        assert db.is_file() and key.is_file()
        assert backend.status()['alive']
    finally:
        assert backend.close()
    previous = key.read_bytes()
    backend = open_application_cache(tmp_path)
    try:
        assert key.read_bytes() == previous
    finally:
        assert backend.close()


def test_database_without_key_does_not_regenerate_key(tmp_path):
    db, key = default_cache_paths(tmp_path)
    db.write_bytes(b'EXISTING')
    with pytest.raises(CacheError, match='CACHE_KEY_MISSING'):
        open_application_cache(tmp_path)
    assert not key.exists() and db.read_bytes() == b'EXISTING'


def test_corrupt_db_is_reported_before_backend_returned(tmp_path):
    db, key = default_cache_paths(tmp_path)
    backend = open_application_cache(tmp_path)
    assert backend.close()
    previous = key.read_bytes()
    db.write_bytes(b'CORRUPT_SENTINEL')
    with pytest.raises(CacheError, match='CACHE_OPEN_FAILED'):
        open_application_cache(tmp_path)
    assert key.read_bytes() == previous
    assert db.read_bytes() == b'CORRUPT_SENTINEL'


def test_bad_key_is_not_replaced(tmp_path):
    _, key = default_cache_paths(tmp_path)
    key.write_bytes(b'INVALID')
    import os
    if os.name != 'nt':
        key.chmod(0o600)
    with pytest.raises(CacheError):
        open_application_cache(tmp_path)
    assert key.read_bytes() == b'INVALID'
