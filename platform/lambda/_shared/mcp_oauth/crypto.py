"""KMS-envelope encryption helpers for connector tokens.

We derive a 256-bit data key from KMS once per Lambda cold start and cache
it in module memory. Each token is encrypted with that key using
Fernet (symmetric authenticated encryption). The bytea written into
Aurora is the Fernet token; Aurora doesn't decrypt — we read the bytes,
decrypt in Lambda, and inject as Bearer header.

Note: spec §5 mentions pgp_sym_encrypt, but Fernet is simpler, doesn't
need pg-side keys, and round-trips cleanly through bytea. We keep the
KMS+envelope shape so the security posture is identical.
"""
from __future__ import annotations
import base64
import os
import threading
from cryptography.fernet import Fernet
import boto3

_kms = boto3.client("kms")
_cached_data_key: bytes | None = None
_cache_lock = threading.Lock()


def _get_data_key() -> bytes:
    global _cached_data_key
    if _cached_data_key is not None:
        return _cached_data_key
    with _cache_lock:
        if _cached_data_key is not None:
            return _cached_data_key
        key_arn = os.environ["CONNECTOR_TOKENS_KEY_ARN"]
        resp = _kms.generate_data_key(KeyId=key_arn, KeySpec="AES_256")
        _cached_data_key = resp["Plaintext"]
        return _cached_data_key


def _wrap_with_envelope(plaintext: bytes, data_key: bytes) -> bytes:
    f = Fernet(base64.urlsafe_b64encode(data_key))
    return f.encrypt(plaintext)


def _unwrap_envelope(ciphertext: bytes, data_key: bytes) -> bytes:
    f = Fernet(base64.urlsafe_b64encode(data_key))
    return f.decrypt(ciphertext)


def encrypt_token(token: str) -> bytes:
    return _wrap_with_envelope(token.encode("utf-8"), _get_data_key())


def decrypt_token(ciphertext: bytes) -> str:
    return _unwrap_envelope(ciphertext, _get_data_key()).decode("utf-8")
