"""Lokalny klucz cache; import niczego nie tworzy.

POSIX: surowy klucz w pliku właściciela 0600. Windows: DPAPI CurrentUser,
bez CRYPTPROTECT_LOCAL_MACHINE. DPAPI nie zastępuje ACL ani nie szyfruje bazy.
Klucza nie używać jako RevisionSigner, nie logować ani przenosić między OS.
Kontrole filesystem ograniczają TOCTOU, nie eliminują wszystkich wyścigów.
"""
import ctypes
import os
import secrets
import stat
from pathlib import Path

from .cache_store import CacheError
from .paths import check_directory_chain

WINDOWS = os.name == 'nt'
MAGIC = b'SKLR-CACHE-KEY-1\0'
MAX_KEY_FILE_BYTES = 16384


def _dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise CacheError('CACHE_KEY_PLATFORM')

    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer)
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    # CRYPTPROTECT_UI_FORBIDDEN; aucune protection machine-wide.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise CacheError('CACHE_KEY_PROTECTION_FAILED')
    try:
        if not 1 <= output.size <= MAX_KEY_FILE_BYTES:
            raise CacheError('CACHE_KEY_INVALID')
        return ctypes.string_at(output.data, output.size)
    finally:
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def _regular(info):
    if (not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or info.st_nlink != 1):
        raise CacheError('CACHE_KEY_PATH_INVALID')
    if not WINDOWS and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise CacheError('CACHE_KEY_PERMISSIONS')


def _snapshot(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _path(value):
    path = Path(value)
    if not path.is_absolute():
        raise CacheError('CACHE_KEY_PATH_INVALID')
    check_directory_chain(path.parent)
    return path


def load_cache_key(path):
    """Świeży odczyt; nie naprawia uprawnień ani nie zastępuje uszkodzonego klucza."""
    try:
        path = _path(path)
        before = path.lstat()
        _regular(before)
        if not 1 <= before.st_size <= MAX_KEY_FILE_BYTES:
            raise CacheError('CACHE_KEY_INVALID')
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            _regular(opened)
            if _snapshot(before) != _snapshot(opened):
                raise CacheError('CACHE_KEY_CHANGED')
            payload = stream.read(MAX_KEY_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
        final = path.lstat()
        _regular(final)
        if (_snapshot(opened) != _snapshot(after) or _snapshot(after) != _snapshot(final)
                or len(payload) != after.st_size):
            raise CacheError('CACHE_KEY_CHANGED')
        if WINDOWS:
            if not payload.startswith(MAGIC):
                raise CacheError('CACHE_KEY_INVALID')
            key = _dpapi(payload[len(MAGIC):], decrypt=True)
        else:
            key = payload
        if len(key) != 32:
            raise CacheError('CACHE_KEY_INVALID')
        return key
    except CacheError:
        raise
    except Exception:
        raise CacheError('CACHE_KEY_READ_FAILED') from None


def create_cache_key(path):
    """Wyłączne utworzenie. Istniejący cel (także link) nigdy nie jest nadpisywany.

    Błąd zapisu może zostawić niepełny plik: nie usuwamy go automatycznie,
    bo ścieżka mogła zostać podmieniona. Kolejne wywołanie ma odmówić.
    """
    try:
        path = _path(path)
        key = secrets.token_bytes(32)
        payload = MAGIC + _dpapi(key) if WINDOWS else key
        if not 1 <= len(payload) <= MAX_KEY_FILE_BYTES:
            raise CacheError('CACHE_KEY_INVALID')
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
        flags |= getattr(os, 'O_NOFOLLOW', 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            raise CacheError('CACHE_KEY_EXISTS') from None
        with os.fdopen(fd, 'wb') as stream:
            _regular(os.fstat(stream.fileno()))
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        verified = load_cache_key(path)
        if not secrets.compare_digest(key, verified):
            raise CacheError('CACHE_KEY_CHANGED')
        return verified
    except CacheError:
        raise
    except Exception:
        raise CacheError('CACHE_KEY_CREATE_FAILED') from None
