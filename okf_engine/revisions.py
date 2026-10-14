"""Nieprzejrzyste rewizje dokumentów: r1.<base64url(HMAC-SHA256)>.

Klucz (min. 32 bajty) jest zależnością wstrzykiwaną i żyje tylko w pamięci właściciela serwisu;
restart z nowym kluczem unieważnia wszystkie tokeny. Surowy digest pliku nigdy nie jest zwracany.
"""
import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

REVISION_PREFIX = "r1."
MIN_KEY_BYTES = 32
_DOMAIN = b"sklr-okf-revision-v1"


@dataclass(frozen=True)
class RevisionBinding:
    """Wiązanie rewizji: bundle i przypięte kanoniczne ścieżki (jako napisy, bez dostępu do dysku)."""
    bundle_id: str
    root_path: str
    bundle_path: str
    file_path: str


def _field(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value  # jednoznaczne kodowanie (długość + bajty)


def _encode(binding: RevisionBinding, digest: bytes) -> bytes:
    parts = [_DOMAIN]
    for text in (binding.bundle_id, binding.root_path, binding.bundle_path, binding.file_path):
        parts.append(text.encode("utf-8", errors="surrogatepass"))
    parts.append(digest)
    return b"".join(_field(part) for part in parts)


class RevisionSigner:
    def __init__(self, key: bytes) -> None:
        if not isinstance(key, (bytes, bytearray)) or len(key) < MIN_KEY_BYTES:
            raise ValueError(f"key must be at least {MIN_KEY_BYTES} bytes")
        self._key = bytes(key)

    @staticmethod
    def new_key() -> bytes:
        return secrets.token_bytes(MIN_KEY_BYTES)

    def __repr__(self) -> str:
        return "RevisionSigner()"

    def sign(self, binding: RevisionBinding, data: bytes) -> str:
        digest = hashlib.sha256(bytes(data)).digest()
        mac = hmac.new(self._key, _encode(binding, digest), hashlib.sha256).digest()
        return REVISION_PREFIX + base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")

    def verify(self, token: object, binding: RevisionBinding, data: bytes) -> bool:
        if not isinstance(token, str):
            return False
        expected = self.sign(binding, data)
        return hmac.compare_digest(token.encode("utf-8", errors="surrogatepass"), expected.encode("ascii"))
