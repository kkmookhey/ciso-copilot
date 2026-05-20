"""Shared helpers for the streaming text turn (ChatStreamFn).

The streaming transport itself lives in app.py — a Starlette ASGI app served
under the Lambda Web Adapter. This module is the home for the auth + tenant
resolution helpers that app.py imports:

  _verify_jwt(token)        — verify a Cognito RS256 JWT against the pool JWKS
  _extract_bearer(headers)  — pull the bearer token out of request headers
  _resolve_from_claims      — resolve (tenant_id, user_id) from decoded claims

Auth context: the Function URL has AuthType=NONE at the AWS layer, so there is
no API Gateway authorizer in the path. JWT verification is performed HERE
against the Cognito user-pool JWKS (RS256).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error

from _db import _q, _subject_from_claims, _claim_value

# ---------------------------------------------------------------------------
# Env vars
# ---------------------------------------------------------------------------
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
AWS_REGION   = os.environ.get("AWS_REGION", "us-east-1")

# ---------------------------------------------------------------------------
# JWKS cache (one warm container reuses the cached keys)
# ---------------------------------------------------------------------------
_jwks_cache: dict | None = None
_jwks_fetched_at: float  = 0.0
_JWKS_TTL = 3600  # 1 hour


def _jwks() -> list[dict]:
    """Fetch (and cache) the Cognito user-pool public keys."""
    global _jwks_cache, _jwks_fetched_at
    now = time.time()
    if _jwks_cache is None or now - _jwks_fetched_at > _JWKS_TTL:
        url = (
            f"https://cognito-idp.{AWS_REGION}.amazonaws.com"
            f"/{USER_POOL_ID}/.well-known/jwks.json"
        )
        with urllib.request.urlopen(url, timeout=6) as r:
            _jwks_cache = json.loads(r.read()).get("keys", [])
        _jwks_fetched_at = now
    return _jwks_cache


# ---------------------------------------------------------------------------
# JWT verification
# ---------------------------------------------------------------------------

def _verify_jwt(token: str) -> dict | None:
    """Verify a Cognito RS256 JWT.  Return claims dict, or None on any failure.

    Verification steps (mirrors Cognito docs):
      1. Decode header (no verify) to get `kid`.
      2. Find matching key in JWKS.
      3. Construct RSA public key from JWK parameters.
      4. jwt.decode() with RS256, verify exp, iss, token_use.

    Requires: PyJWT[crypto]==2.10.1 (in requirements.txt).
    """
    if not token:
        return None
    if not USER_POOL_ID:
        # Env var missing — skip verification (should not happen in prod).
        print("WARN: USER_POOL_ID not set; JWT verification skipped")
        return None

    try:
        import jwt
        from jwt.algorithms import RSAAlgorithm

        # Decode header without verification to get kid.
        unverified = jwt.get_unverified_header(token)
        kid = unverified.get("kid")
        if not kid:
            return None

        # Match kid against JWKS.
        keys = _jwks()
        jwk = next((k for k in keys if k.get("kid") == kid), None)
        if jwk is None:
            print(f"JWT verify: kid {kid!r} not found in JWKS")
            return None

        # Build RSA public key from JWK.
        public_key = RSAAlgorithm.from_jwk(json.dumps(jwk))

        expected_issuer = (
            f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{USER_POOL_ID}"
        )

        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            # Cognito JWTs set `aud` to the app client_id for id tokens, but
            # access tokens set `client_id` instead and have no `aud`. We pass
            # options to skip audience check and verify token_use ourselves.
            options={"verify_aud": False},
            issuer=expected_issuer,
        )

        # Accept id_token or access_token; reject anything else.
        token_use = claims.get("token_use")
        if token_use not in ("id", "access"):
            print(f"JWT verify: unexpected token_use {token_use!r}")
            return None

        return claims

    except Exception as e:
        print(f"JWT verify failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Bearer extraction
# ---------------------------------------------------------------------------

def _extract_bearer(headers: dict) -> str | None:
    """Pull the bearer token from a headers mapping (case-insensitive)."""
    h = headers or {}
    auth = h.get("authorization") or h.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


# ---------------------------------------------------------------------------
# Tenant resolution (Function-URL path — no API Gateway authorizer)
# ---------------------------------------------------------------------------

def _resolve_from_claims(claims: dict) -> tuple[str | None, str | None]:
    """Return (tenant_id, user_id) by resolving sso_subject from claims.

    Mirrors _db._resolve_user_context normal path, but called here because
    we already have the decoded claims dict from our JWKS verification and
    there is no requestContext/authorizer wrapper around them.
    """
    sso_subject = _subject_from_claims(claims)
    if not sso_subject:
        return None, None
    rows = _q(
        "SELECT u.tenant_id::text, u.user_id::text "
        "FROM users u "
        "WHERE u.sso_subject = :s LIMIT 1",
        {"s": sso_subject},
    )
    if not rows:
        return None, None
    r = rows[0]
    tenant_id = _claim_value(r[0])
    user_id   = _claim_value(r[1])
    return tenant_id, user_id
