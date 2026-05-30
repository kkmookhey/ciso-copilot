"""Tests for mcp_oauth.crypto. KMS is mocked; the Fernet primitive is real."""
from __future__ import annotations
from unittest.mock import MagicMock


class _FakeKMS:
    """Stand-in for boto3 kms client. GenerateDataKey returns a deterministic
    (Plaintext, CiphertextBlob) pair so tests can assert the envelope shape;
    Decrypt looks up by CiphertextBlob and returns the matching Plaintext."""

    def __init__(self):
        self.generated = []   # list of (plaintext, ciphertext_blob)
        self.decrypts = []    # list of ciphertext_blobs requested
        self._counter = 0

    def generate_data_key(self, *, KeyId, KeySpec):
        self._counter += 1
        plaintext = bytes([self._counter]) * 32
        blob = f"BLOB-{self._counter}".encode()
        self.generated.append((plaintext, blob))
        return {"Plaintext": plaintext, "CiphertextBlob": blob}

    def decrypt(self, *, CiphertextBlob, KeyId):
        self.decrypts.append(CiphertextBlob)
        for plain, blob in self.generated:
            if blob == CiphertextBlob:
                return {"Plaintext": plain}
        raise RuntimeError(f"unknown blob: {CiphertextBlob!r}")


def _install_fake_kms(monkeypatch):
    monkeypatch.setenv("CONNECTOR_TOKENS_KEY_ARN", "arn:aws:kms:us-east-1:x:key/abc")
    from mcp_oauth import crypto as c
    # Reset the cache between tests so cross-test state doesn't leak.
    c._decrypted_key_cache.clear()
    fake = _FakeKMS()
    monkeypatch.setattr(c, "_kms", fake)
    return c, fake


def test_encrypt_returns_ciphertext_and_data_key_blob(monkeypatch):
    c, fake = _install_fake_kms(monkeypatch)

    ct, blob = c.encrypt_token("xoxp-secret")
    assert ct != b"xoxp-secret"          # actually encrypted
    assert blob == b"BLOB-1"             # CiphertextBlob persisted
    assert len(fake.generated) == 1      # one GenerateDataKey


def test_decrypt_round_trips_via_kms_decrypt(monkeypatch):
    """Critical envelope invariant: a token written by one 'cold start' must
    be decryptable by a different 'cold start' that holds the same
    CiphertextBlob. This is what the previous design got wrong — it cached
    only Plaintext, so a second container had a different key."""
    c, fake = _install_fake_kms(monkeypatch)

    ct, blob = c.encrypt_token("xoxp-real")

    # Simulate a new container: clear the in-memory cache so decrypt has to
    # call kms.Decrypt(CiphertextBlob) to recover the key.
    c._decrypted_key_cache.clear()

    plain = c.decrypt_token(ct, blob)
    assert plain == "xoxp-real"
    assert fake.decrypts == [blob]


def test_decrypt_uses_cache_on_second_read(monkeypatch):
    """Reading the same row twice in the same warm container hits the LRU
    instead of calling KMS Decrypt every time."""
    c, fake = _install_fake_kms(monkeypatch)
    ct, blob = c.encrypt_token("v")

    # First decrypt: not cached → one KMS Decrypt.
    c._decrypted_key_cache.clear()
    c.decrypt_token(ct, blob)
    assert len(fake.decrypts) == 1

    # Second decrypt for the same blob: cache hit → no additional Decrypt.
    c.decrypt_token(ct, blob)
    assert len(fake.decrypts) == 1


def test_each_row_has_its_own_data_key(monkeypatch):
    """Two encrypts must produce two distinct CiphertextBlobs. The previous
    design generated one Plaintext per cold start and reused it across all
    tokens — exposing every row to a single key compromise."""
    c, _ = _install_fake_kms(monkeypatch)
    _, blob1 = c.encrypt_token("token-A")
    _, blob2 = c.encrypt_token("token-B")
    assert blob1 != blob2


def test_decrypt_with_wrong_data_key_blob_fails(monkeypatch):
    """A blob from row B cannot decrypt row A's ciphertext — proves the
    KMS-envelope binding is per-row, not global."""
    import pytest
    from cryptography.fernet import InvalidToken
    c, _ = _install_fake_kms(monkeypatch)

    ct_a, _blob_a = c.encrypt_token("token-A")
    _ct_b, blob_b = c.encrypt_token("token-B")
    c._decrypted_key_cache.clear()
    with pytest.raises(InvalidToken):
        c.decrypt_token(ct_a, blob_b)
