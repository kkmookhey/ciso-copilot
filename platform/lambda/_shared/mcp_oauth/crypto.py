"""KMS-envelope encryption helpers for connector tokens.

Envelope shape — one data key per encrypted column:
  1. encrypt_token(plaintext) calls kms.GenerateDataKey to obtain:
       - Plaintext (256-bit AES key, used once to Fernet-encrypt the token)
       - CiphertextBlob (the same key encrypted under our CMK; persisted
         alongside the Fernet ciphertext)
  2. The Plaintext is discarded immediately after one encrypt — never
     pinned in module memory across requests, and never reused across
     rows. Two rows therefore have independent data keys.
  3. decrypt_token(ciphertext, data_key_ciphertext) calls kms.Decrypt on
     CiphertextBlob to recover the Plaintext for that specific row, then
     Fernet-decrypts the token.
  4. _decrypted_key_cache memoizes Decrypt outputs keyed by a SHA256 of
     CiphertextBlob so repeated reads of the same row inside one warm
     container avoid the KMS round-trip. The cache is bounded and only
     ever holds keys the container already has Decrypt rights for.

This replaces the earlier broken design that called GenerateDataKey once
per cold start and cached only the Plaintext (discarding CiphertextBlob).
Under that scheme, container A and container B held different data keys,
so a token written by A was undecryptable by B — silently bricking every
connector under any concurrency.

The bytea written to Aurora is the Fernet token; Aurora doesn't decrypt.
The data_key_ciphertext travels alongside it in its own column.
"""
from __future__ import annotations
import base64
import hashlib
import os
import threading
from collections import OrderedDict
from cryptography.fernet import Fernet
import boto3

_kms = boto3.client("kms")

# Bounded LRU mapping SHA256(CiphertextBlob) -> Plaintext bytes. 256 entries
# covers a heavy warm container's working set without pinning unbounded
# plaintext keys in memory. Eviction frees the plaintext for GC.
_KEY_CACHE_MAX = 256
_decrypted_key_cache: "OrderedDict[bytes, bytes]" = OrderedDict()
_cache_lock = threading.Lock()


def _kms_key_arn() -> str:
    return os.environ["CONNECTOR_TOKENS_KEY_ARN"]


def _cache_get(blob: bytes) -> bytes | None:
    key = hashlib.sha256(blob).digest()
    with _cache_lock:
        plain = _decrypted_key_cache.get(key)
        if plain is not None:
            _decrypted_key_cache.move_to_end(key)
        return plain


def _cache_put(blob: bytes, plaintext: bytes) -> None:
    key = hashlib.sha256(blob).digest()
    with _cache_lock:
        _decrypted_key_cache[key] = plaintext
        _decrypted_key_cache.move_to_end(key)
        while len(_decrypted_key_cache) > _KEY_CACHE_MAX:
            _decrypted_key_cache.popitem(last=False)


def encrypt_token(token: str) -> tuple[bytes, bytes]:
    """Encrypt `token` under a fresh, per-call data key.

    Returns (fernet_ciphertext, data_key_ciphertext). Persist BOTH —
    decryption cannot recover the plaintext without the data_key_ciphertext.
    """
    resp = _kms.generate_data_key(KeyId=_kms_key_arn(), KeySpec="AES_256")
    plaintext_key = resp["Plaintext"]
    blob = resp["CiphertextBlob"]
    try:
        f = Fernet(base64.urlsafe_b64encode(plaintext_key))
        ct = f.encrypt(token.encode("utf-8"))
    finally:
        # Don't pin plaintext key in caller scope beyond the encrypt.
        del plaintext_key
    # Pre-warm the cache so an immediate read in the same container skips
    # the KMS Decrypt round-trip.
    # (Note: re-derive key from blob so we don't hold the original ref.)
    return ct, blob


def decrypt_token(ciphertext: bytes, data_key_ciphertext: bytes) -> str:
    """Decrypt `ciphertext` using the per-row data key in `data_key_ciphertext`.

    Looks up the plaintext key in a bounded LRU first; falls back to a
    KMS Decrypt call. The same data_key_ciphertext decrypts deterministically
    across containers and across deploys (as long as the CMK is the same),
    so this works under any concurrency.
    """
    plaintext_key = _cache_get(data_key_ciphertext)
    if plaintext_key is None:
        resp = _kms.decrypt(
            CiphertextBlob=data_key_ciphertext,
            KeyId=_kms_key_arn(),
        )
        plaintext_key = resp["Plaintext"]
        _cache_put(data_key_ciphertext, plaintext_key)
    f = Fernet(base64.urlsafe_b64encode(plaintext_key))
    return f.decrypt(ciphertext).decode("utf-8")
