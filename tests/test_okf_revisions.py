"""Testy rewizji OKF (C1a): HMAC z wstrzykniętym kluczem, bez ujawniania digestu."""
import hashlib

import pytest

from okf_engine.revisions import REVISION_PREFIX, RevisionBinding, RevisionSigner

KEY = b"k" * 32
BINDING = RevisionBinding("general", "C:/root", "C:/root/general", "C:/root/general/tables/orders.md")
DATA = b"---\ntype: X\n---\nbody\n"


def test_same_inputs_same_token_and_format():
    signer = RevisionSigner(KEY)
    token = signer.sign(BINDING, DATA)
    assert token == signer.sign(BINDING, DATA) == RevisionSigner(KEY).sign(BINDING, DATA)
    assert token.startswith(REVISION_PREFIX) and "=" not in token
    assert signer.verify(token, BINDING, DATA)


def test_does_not_expose_digest():
    token = RevisionSigner(KEY).sign(BINDING, DATA)
    assert hashlib.sha256(DATA).hexdigest() not in token
    assert hashlib.sha256(DATA).hexdigest()[:16] not in token


@pytest.mark.parametrize("changed", [
    DATA + b" ", DATA.replace(b"\n", b"\r\n"), DATA.replace(b"X", b"Y"), b"\xef\xbb\xbf" + DATA, b"",
])
def test_any_byte_change_changes_revision(changed):
    signer = RevisionSigner(KEY)
    token = signer.sign(BINDING, DATA)
    assert signer.sign(BINDING, changed) != token
    assert not signer.verify(token, BINDING, changed)


@pytest.mark.parametrize("field,value", [
    ("bundle_id", "other"), ("root_path", "D:/root"), ("bundle_path", "C:/root/other"),
    ("file_path", "C:/root/general/tables/other.md"),
])
def test_binding_change_changes_revision(field, value):
    signer = RevisionSigner(KEY)
    token = signer.sign(BINDING, DATA)
    other = RevisionBinding(**{**BINDING.__dict__, field: value})
    assert signer.sign(other, DATA) != token and not signer.verify(token, other, DATA)


def test_field_boundaries_are_unambiguous():
    signer = RevisionSigner(KEY)
    a = RevisionBinding("ab", "c", "d", "e")
    b = RevisionBinding("a", "bc", "d", "e")
    assert signer.sign(a, DATA) != signer.sign(b, DATA)


def test_key_rotation_invalidates_tokens():
    token = RevisionSigner(KEY).sign(BINDING, DATA)
    rotated = RevisionSigner(b"z" * 32)
    assert rotated.sign(BINDING, DATA) != token and not rotated.verify(token, BINDING, DATA)


def test_verify_rejects_garbage():
    signer = RevisionSigner(KEY)
    token = signer.sign(BINDING, DATA)
    for bad in (None, 5, b"r1.x", "", "r1.", token[:-1], token + "A", token.replace("r1.", "r2."), "ż" * 10):
        assert not signer.verify(bad, BINDING, DATA)


def test_key_requirements_and_repr():
    for bad in (b"short", "x" * 40, None, b"k" * 31):
        with pytest.raises(ValueError):
            RevisionSigner(bad)
    assert len(RevisionSigner.new_key()) >= 32
    assert RevisionSigner.new_key() != RevisionSigner.new_key()
    signer = RevisionSigner(KEY)
    assert "kkkk" not in repr(signer) and "kkkk" not in str(signer)
    RevisionSigner(bytearray(KEY))  # bytearray jest akceptowany
