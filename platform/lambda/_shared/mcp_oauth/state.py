"""Signed-JWT state parameter for OAuth callbacks.

This JWT IS the CSRF defense for the callback. The original spec §6 added a
double-submit cookie alongside the state JWT, but the deployment is
cross-origin (shasta.transilience.cloud vs *.execute-api.*) so the browser
drops the Set-Cookie from the initiate fetch and the cookie check became
dead code. Rather than ship a CORS rewrite to rescue a belt-and-braces
mitigation, we removed the cookie and tightened the JWT instead.

The JWT carries everything the callback needs to validate without a DB
round-trip:
  - tenant_id, user_id, provider — what to insert into user_connectors
  - pkce_verifier_hash — must match the SHA256 of the verifier fetched
    from DDB (spec §6 PKCE)
  - nonce — keys the PKCE verifier in DDB; caller passes it in so we
    don't decode our own signed JWT just to read it back
  - iss, aud — pinned at sign and verified at decode so a JWT minted for
    one provider can't be replayed against another's callback if the
    STATE_JWT_SECRET is ever shared across flows
  - iat, exp — 5-minute window

Security relies on:
  1. STATE_JWT_SECRET is HS256-strong (≥32 bytes) and only the connectors
     Lambda has it via SSM.
  2. user_id is baked in at initiate time from the authenticated POST;
     an attacker without a valid Cognito JWT can't produce a state for a
     different user.
  3. The PKCE verifier is one-shot consumed in DDB on callback, so even
     a leaked (state, code) pair can't be replayed.
"""
from __future__ import annotations
import os
import time
import jwt

_ISSUER = "shasta-connectors"


def _audience(provider: str) -> str:
    return f"{provider}-callback"


def _secret() -> str:
    s = os.environ["STATE_JWT_SECRET"]
    if len(s) < 32:
        raise RuntimeError("STATE_JWT_SECRET must be at least 32 bytes")
    return s


def sign_state(*, tenant_id: str, user_id: str, provider: str,
               pkce_verifier_hash: str, nonce: str,
               ttl_seconds: int = 300) -> str:
    now = int(time.time())
    payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "provider": provider,
        "pkce_verifier_hash": pkce_verifier_hash,
        "nonce": nonce,
        "iat": now,
        "exp": now + ttl_seconds,
        "iss": _ISSUER,
        "aud": _audience(provider),
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def verify_state(token: str, *, expected_provider: str) -> dict:
    """Verify the state JWT and enforce issuer + audience.

    `expected_provider` MUST be the provider whose callback this is — the
    aud claim is pinned to that provider, so a JWT for slack can't be
    used at the atlassian callback.
    """
    return jwt.decode(
        token,
        _secret(),
        algorithms=["HS256"],
        issuer=_ISSUER,
        audience=_audience(expected_provider),
    )
