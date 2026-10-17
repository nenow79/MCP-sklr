"""Odtwarzalny cache publicznego OKF; brak integracji z runtime w etapie 1.

Brak raw body, rewizji API i klucza w SQLite. Cache nie nadaje dostępu.
Otwieranie/odczyt wymagają zewnętrznej kontroli wiązania i dostępu w serwisie.
Połączenie należy do jednego wątku (docelowo dedykowanego workera).
"""
import hashlib
import hmac
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .models import DocumentIdentity
from .paths import check_directory_chain, is_link
from .public_view import PublicDocumentView, mask_metadata, mask_text

SCHEMA_VERSION = 2
MAX_PAYLOAD_BYTES = 2 * 1048576
MAX_RECORDS = 5000
MAX_BUNDLES = 20
MAX_CACHE_PAYLOAD_BYTES = 64 * 1048576  # dane logiczne, nie fizyczny rozmiar SQLite
MAX_PAGE_RECORDS = 100
HEX64 = re.compile(r"[0-9a-f]{64}")


class CacheError(ValueError):
    """Wyłącznie bezpieczny kod; nigdy treść ani komunikat SQLite."""


def default_cache_paths(application_dir):
    """Katalog aplikacji podaje bootstrap; nie korzystamy z cwd i nic nie tworzymy."""
    directory = Path(application_dir)
    if not directory.is_absolute():
        raise CacheError("CACHE_PATH_INVALID")
    return directory / "knowledge_index.sqlite3", directory / "knowledge_cache.key"


def fingerprint(raw, key):
    """Prywatny HMAC; osobny klucz cache dostarczany przez właściciela.

    Wyniku nie publikować w API/telemetrii. To nie szyfrowanie ani RevisionSigner.
    """
    if not isinstance(raw, bytes) or not isinstance(key, bytes) or len(key) != 32:
        raise CacheError("CACHE_FINGERPRINT_INVALID")
    return hmac.new(key, b"sklr-cache-v1\0" + raw, hashlib.sha256).hexdigest()


def _text(value, maximum=1024):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CacheError("CACHE_RECORD_INVALID")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CacheError("CACHE_RECORD_INVALID")
    return value


def _json(value):
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        if len(result.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise CacheError("CACHE_RECORD_LIMIT")
        return result
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise CacheError("CACHE_RECORD_INVALID") from None


def _snapshot(value):
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise CacheError("CACHE_RECORD_INVALID")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in value):
        raise CacheError("CACHE_RECORD_INVALID")
    return tuple(value)  # dev, ino, size, mtime_ns; filtr, nie dowód identyczności


@dataclass(frozen=True)
class CacheRecord:
    identity: DocumentIdentity
    binding: str = field(repr=False)
    versions: str
    snapshot: tuple = field(repr=False)
    digest: str = field(repr=False)
    metadata: dict = field(repr=False)
    body: str = field(repr=False)
    body_start_line: int
    format: str = "okf"
    auth_tag: str = field(default=None, repr=False)


def _payload(record):
    if not isinstance(record, CacheRecord) or not isinstance(record.identity, DocumentIdentity):
        raise CacheError("CACHE_RECORD_INVALID")
    if record.auth_tag is not None and (
            not isinstance(record.auth_tag, str) or HEX64.fullmatch(record.auth_tag) is None):
        raise CacheError("CACHE_AUTH_INVALID")
    _text(record.binding, 4096)
    _text(record.versions, 256)
    _snapshot(record.snapshot)
    if not isinstance(record.digest, str) or HEX64.fullmatch(record.digest) is None:
        raise CacheError("CACHE_RECORD_INVALID")
    if (isinstance(record.body_start_line, bool) or not isinstance(record.body_start_line, int)
            or not 1 <= record.body_start_line <= 1048576 or record.format not in ("okf", "markdown")):
        raise CacheError("CACHE_RECORD_INVALID")
    if not isinstance(record.body, str) or not isinstance(record.metadata, dict):
        raise CacheError("CACHE_RECORD_INVALID")
    # Ograniczamy dane przed rekurencyjnym maskowaniem. Bez pickle/YAML w cache.
    _json({"metadata": record.metadata, "body": record.body})
    try:
        metadata, _, _ = mask_metadata(record.metadata)
        body = mask_text(record.body).text
    except (TypeError, ValueError, RecursionError):
        raise CacheError("CACHE_RECORD_INVALID") from None
    if record.format == "okf" and (not isinstance(metadata.get("type"), str)
                                   or not metadata["type"].strip()):
        raise CacheError("CACHE_RECORD_INVALID")
    if record.format == "markdown" and metadata:
        raise CacheError("CACHE_RECORD_INVALID")
    return _json({"metadata": metadata, "body": body,
                  "body_start_line": record.body_start_line, "format": record.format})


def record_from_public(view, binding, versions, snapshot, digest, document_format="okf"):
    if not isinstance(view, PublicDocumentView):
        raise CacheError("CACHE_PUBLIC_VIEW_REQUIRED")
    record = CacheRecord(view.identity, binding, versions, _snapshot(snapshot), digest,
                         view.metadata, view.body, view.body_start_line, document_format)
    # Kopia przez publiczny JSON; rekord nie zatrzymuje referencji do słownika widoku.
    payload = json.loads(_payload(record))
    return CacheRecord(record.identity, binding, versions, record.snapshot, digest,
                       payload["metadata"], payload["body"], payload["body_start_line"], payload["format"])


class CacheStore:
    """Transakcyjny upsert/delete, filtrowany odczyt; tylko jeden wątek właściciela.

    Baza i pomocnicze pliki muszą być lokalnymi zwykłymi plikami. Kontrole nie
    zapewniają odporności na wszystkie TOCTOU; cache nie jest zaufaną bazą uprawnień.
    """
    def __init__(self, path):
        self._connection = None
        try:
            path = Path(path)
            if not path.is_absolute():
                raise CacheError("CACHE_PATH_INVALID")
            check_directory_chain(path.parent)
            for target in (path, Path(str(path) + "-journal"),
                           Path(str(path) + "-wal"), Path(str(path) + "-shm")):
                if is_link(target) or (target.exists() and not target.is_file()):
                    raise CacheError("CACHE_PATH_INVALID")
            connection = self._connection = sqlite3.connect(str(path), timeout=2)
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA secure_delete=ON")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            if version == 0 and not tables:
                with connection:
                    connection.execute("CREATE TABLE documents (bundle TEXT NOT NULL, concept TEXT NOT NULL, "
                                       "binding TEXT NOT NULL, versions TEXT NOT NULL, snapshot TEXT NOT NULL, "
                                       "digest TEXT NOT NULL, payload TEXT NOT NULL, auth_tag TEXT, PRIMARY KEY(bundle, concept))")
                    connection.execute("PRAGMA user_version=2")
            elif version == 1 and tables == [("documents",)]:
                old_columns = [row[1] for row in connection.execute("PRAGMA table_info(documents)")]
                if old_columns != ["bundle", "concept", "binding", "versions", "snapshot", "digest", "payload"]:
                    raise CacheError("CACHE_SCHEMA_UNSUPPORTED")
                self._check_limits(connection)
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise CacheError("CACHE_CORRUPT")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute("ALTER TABLE documents ADD COLUMN auth_tag TEXT")
                    connection.execute("PRAGMA user_version=2")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            elif version != SCHEMA_VERSION or tables != [("documents",)]:
                raise CacheError("CACHE_SCHEMA_UNSUPPORTED")
            columns = [row[1] for row in connection.execute("PRAGMA table_info(documents)")]
            if columns != ["bundle", "concept", "binding", "versions", "snapshot", "digest", "payload", "auth_tag"]:
                raise CacheError("CACHE_SCHEMA_UNSUPPORTED")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise CacheError("CACHE_CORRUPT")
            self._check_limits(connection)
        except CacheError:
            self.close()
            raise
        except Exception:
            self.close()
            raise CacheError("CACHE_OPEN_FAILED") from None

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _db(self):
        if self._connection is None:
            raise CacheError("CACHE_CLOSED")
        return self._connection

    @staticmethod
    def _check_limits(db):
        bundles, payload_bytes = db.execute(
            "SELECT COUNT(DISTINCT bundle), COALESCE(SUM(length(CAST(payload AS BLOB))),0) "
            "FROM documents").fetchone()
        if bundles > MAX_BUNDLES or payload_bytes > MAX_CACHE_PAYLOAD_BYTES:
            raise CacheError("CACHE_TOTAL_LIMIT")
        if db.execute("SELECT 1 FROM documents GROUP BY bundle HAVING COUNT(*)>? LIMIT 1",
                      (MAX_RECORDS,)).fetchone():
            raise CacheError("CACHE_RECORD_LIMIT")

    def load_page(self, bundle_id, binding, versions, *, after=None, limit=50):
        """Keyset pagination; połączenie właściciela, bez długiej transakcji odczytu.

        Zwraca rekordy i cursor ostatniego rekordu. Pusta partia kończy odczyt.
        Właściciel nie powinien modyfikować zbioru podczas ładowania generacji;
        dostęp/config-version musi sprawdzić przed użyciem każdej partii.
        """
        try:
            DocumentIdentity(bundle_id, "cache-page")
            _text(binding, 4096)
            _text(versions, 256)
            if (isinstance(limit, bool) or not isinstance(limit, int)
                    or not 1 <= limit <= MAX_PAGE_RECORDS):
                raise CacheError("CACHE_PAGE_INVALID")
            if after is not None:
                DocumentIdentity(bundle_id, after)
            query = ("SELECT concept FROM documents WHERE bundle=? AND binding=? AND versions=?"
                     + (" AND concept>?" if after is not None else "")
                     + " ORDER BY concept LIMIT ?")
            args = (bundle_id, binding, versions) + (() if after is None else (after,)) + (limit,)
            names = [row[0] for row in self._db().execute(query, args)]
            records = tuple(self.load(DocumentIdentity(bundle_id, name), binding, versions)
                            for name in names)
            if any(record is None for record in records):
                raise CacheError("CACHE_PAGE_CHANGED")
            return records, names[-1] if names else None
        except CacheError:
            raise
        except Exception:
            raise CacheError("CACHE_READ_FAILED") from None

    def purge_bundle(self, bundle_id):
        """Usuwa logiczne wpisy zbioru; nie gwarantuje wymazania nośnika/backupów."""
        try:
            DocumentIdentity(bundle_id, "cache-purge")
            db = self._db()
            with db:
                cursor = db.execute("DELETE FROM documents WHERE bundle=?", (bundle_id,))
            return cursor.rowcount
        except CacheError:
            raise
        except Exception:
            raise CacheError("CACHE_WRITE_FAILED") from None

    def apply(self, records=(), deletes=()):
        """Cała partia albo rollback. Brak dostępu/config-version sprawdza właściciel.

        Nie ma jeszcze staging-generation ani integracji RAM w etapie 1.
        """
        try:
            records, deletes = tuple(records), tuple(deletes)
            if len(records) + len(deletes) > MAX_RECORDS:
                raise CacheError("CACHE_RECORD_LIMIT")
            rows, targets = [], set()
            for record in records:
                payload = _payload(record)
                identity = record.identity
                target = (identity.bundle_id, identity.concept_id)
                if target in targets:
                    raise CacheError("CACHE_DUPLICATE_TARGET")
                targets.add(target)
                rows.append((*target, record.binding, record.versions,
                             _json(_snapshot(record.snapshot)), record.digest, payload, record.auth_tag))
            delete_rows = []
            for identity in deletes:
                if not isinstance(identity, DocumentIdentity):
                    raise CacheError("CACHE_RECORD_INVALID")
                target = (identity.bundle_id, identity.concept_id)
                if target in targets:
                    raise CacheError("CACHE_DUPLICATE_TARGET")
                targets.add(target)
                delete_rows.append(target)
            db = self._db()
            with db:
                db.executemany("DELETE FROM documents WHERE bundle=? AND concept=?", delete_rows)
                db.executemany("INSERT INTO documents "
                               "(bundle,concept,binding,versions,snapshot,digest,payload,auth_tag) "
                               "VALUES (?,?,?,?,?,?,?,?) "
                               "ON CONFLICT(bundle,concept) DO UPDATE SET binding=excluded.binding, "
                               "versions=excluded.versions,snapshot=excluded.snapshot,digest=excluded.digest, "
                               "payload=excluded.payload,auth_tag=excluded.auth_tag", rows)
                self._check_limits(db)
        except CacheError:
            raise
        except Exception:
            raise CacheError("CACHE_WRITE_FAILED") from None

    def load(self, identity, binding, versions):
        """Nie sprawdza filesystem: serwis musi sprawdzić dostęp przed użyciem wyniku."""
        if not isinstance(identity, DocumentIdentity):
            raise CacheError("CACHE_RECORD_INVALID")
        _text(binding, 4096)
        _text(versions, 256)
        try:
            row = self._db().execute(
                "SELECT snapshot,digest,payload,auth_tag FROM documents "
                "WHERE bundle=? AND concept=? AND binding=? AND versions=?",
                (identity.bundle_id, identity.concept_id, binding, versions)).fetchone()
            if row is None:
                return None
            if len(row[2].encode("utf-8")) > MAX_PAYLOAD_BYTES:
                raise CacheError("CACHE_RECORD_LIMIT")
            payload = json.loads(row[2])
            if set(payload) != {"metadata", "body", "body_start_line", "format"}:
                raise CacheError("CACHE_RECORD_INVALID")
            record = CacheRecord(identity, binding, versions, _snapshot(json.loads(row[0])), row[1],
                                 payload["metadata"], payload["body"], payload["body_start_line"], payload["format"], row[3])
            clean = json.loads(_payload(record))  # ponowna walidacja i maskowanie z dysku
            return CacheRecord(identity, binding, versions, record.snapshot, record.digest,
                               clean["metadata"], clean["body"], clean["body_start_line"], clean["format"], record.auth_tag)
        except CacheError:
            raise
        except Exception:
            raise CacheError("CACHE_READ_FAILED") from None
