"""Signed-JWT state parameter for OAuth callbacks.

Carries enough to validate the callback without a DB round-trip:
  - tenant_id, user_id, provider: what to insert into user_connectors
  - pkce_verifier_hash: must match the SHA256 of the verifier fetched
    from DDB (spec §6 PKCE)
  - csrf_token_hash: must match SHA256 of the CSRF cookie set at
    initiate time (spec §6 CSRF binding)
  - nonce: keys the PKCE verifier in DDB; caller passes it in so we
    don't decode our own signed JWT just to read it back
"""
from __future__ import annotations
import os
import time
import jwt


def _secret() -> str:
    s = os.environ["STATE_JWT_SECRET"]
    if len(s) < 32:
        raise RuntimeError("STATE_JWT_SECRET must be at least 32 bytes")
    return s


def sign_state(*, tenant_id: str, user_id: str, provider: str,
               pkce_verifier_hash: str, csrf_token_hash: str,
               nonce: str, ttl_seconds: int = 300) -> str:
    now = int(time.time())
    payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "provider": provider,
        "pkce_verifier_hash": pkce_verifier_hash,
        "csrf_token_hash": csrf_token_hash,
        "nonce": nonce,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def verify_state(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=["HS256"])
