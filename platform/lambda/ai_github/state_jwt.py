"""Stdlib HS256 short-lived state JWTs for the GitHub App install flow.

Matches the pattern in lambda/post_confirmation/main.py:269 to avoid a
PyJWT dependency on a Lambda that only needs HMAC.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import boto3

STATE_JWT_SECRET_ARN = os.environ["STATE_JWT_SECRET_ARN"]

_sm = boto3.client("secretsmanager")
_signing_key_cache: bytes | None = None


def sign(payload: dict, ttl_seconds: int) -> str:
    """Return a compact `<header>.<payload>.<sig>` JWT."""
    now = int(time.time())
    full_payload = {
        **payload,
        "iat":   now,
        "exp":   now + ttl_seconds,
        "nonce": secrets.token_urlsafe(16),
    }
    header = {"alg": "HS256", "typ": "JWT"}
    h = _b64url(json.dumps(header,       separators=(",", ":")).encode())
    p = _b64url(json.dumps(full_payload, separators=(",", ":")).encode())
    sig = hmac.new(_signing_key(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def verify(token: str) -> dict:
    """Return the decoded payload, or raise ValueError."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed token")
    h, p, s = parts
    expected = hmac.new(_signing_key(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url(expected), s):
        raise ValueError("bad signature")
    try:
        payload = json.loads(_b64url_decode(p))
    except (ValueError, TypeError):
        raise ValueError("malformed payload")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("token expired")
    return payload


def _signing_key() -> bytes:
    global _signing_key_cache
    if _signing_key_cache is None:
        v = _sm.get_secret_value(SecretId=STATE_JWT_SECRET_ARN)
        _signing_key_cache = v["SecretString"].encode()
    return _signing_key_cache


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)
