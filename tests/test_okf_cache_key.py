"""Klucz cache tylko tmp_path; test DPAPI na Windows bez produkcyjnych danych."""
import os

import pytest

from okf_engine import cache_key as module
from okf_engine.cache_store import CacheError, fingerprint


def test_create_load_and_never_overwrite(tmp_path):
    path = tmp_path / 'key'
    key = module.create_cache_key(path)
    assert len(key) == 32 and module.load_cache_key(path) == key
    before = path.read_bytes()
    with pytest.raises(CacheError, match='CACHE_KEY_EXISTS'):
        module.create_cache_key(path)
    assert path.read_bytes() == before
    if os.name == 'nt':
        assert before.startswith(module.MAGIC) and before != key
    else:
        assert path.stat().st_mode & 0o777 == 0o600


def test_different_keys_and_fingerprints(tmp_path):
    first = module.create_cache_key(tmp_path / 'first')
    second = module.create_cache_key(tmp_path / 'second')
    assert first != second
    assert fingerprint(b'content', first) != fingerprint(b'content', second)


def test_invalid_key_not_replaced(tmp_path):
    path = tmp_path / 'key'
    path.write_bytes(b'INVALID_SENTINEL')
    if os.name != 'nt':
        path.chmod(0o600)
    with pytest.raises(CacheError):
        module.load_cache_key(path)
    with pytest.raises(CacheError, match='CACHE_KEY_EXISTS'):
        module.create_cache_key(path)
    assert path.read_bytes() == b'INVALID_SENTINEL'


def test_relative_paths_rejected():
    for function in (module.create_cache_key, module.load_cache_key):
        with pytest.raises(CacheError, match='CACHE_KEY_PATH_INVALID'):
            function('relative')


def test_symlink_rejected(tmp_path):
    target = tmp_path / 'target'
    module.create_cache_key(target)
    link = tmp_path / 'link'
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip('symlink unavailable')
    with pytest.raises(CacheError, match='CACHE_KEY_PATH_INVALID'):
        module.load_cache_key(link)
    with pytest.raises(CacheError, match='CACHE_KEY_EXISTS'):
        module.create_cache_key(link)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX mode policy')
def test_unsafe_permissions_not_silently_repaired(tmp_path):
    path = tmp_path / 'key'
    module.create_cache_key(path)
    path.chmod(0o644)
    with pytest.raises(CacheError, match='CACHE_KEY_PERMISSIONS'):
        module.load_cache_key(path)
    assert path.stat().st_mode & 0o777 == 0o644


def test_protection_failure_creates_no_file(tmp_path, monkeypatch):
    path = tmp_path / 'key'
    monkeypatch.setattr(module, 'WINDOWS', True)

    def broken(*args, **kwargs):
        raise CacheError('CACHE_KEY_PROTECTION_FAILED')

    monkeypatch.setattr(module, '_dpapi', broken)
    with pytest.raises(CacheError, match='CACHE_KEY_PROTECTION_FAILED'):
        module.create_cache_key(path)
    assert not path.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_dpapi_rejects_corrupt_blob(tmp_path):
    path = tmp_path / 'key'
    path.write_bytes(module.MAGIC + b'INVALID_SENTINEL')
    with pytest.raises(CacheError, match='CACHE_KEY_PROTECTION_FAILED'):
        module.load_cache_key(path)
