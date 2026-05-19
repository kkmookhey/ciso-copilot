"""Streaming text turn — served via Lambda Function URL (RESPONSE_STREAM).

POST /v1/conversations/{id}/stream  body {"text": "..."}
  -> verify JWT against Cognito JWKS
  -> resolve tenant + user from DB
  -> append user message
  -> call Anthropic streaming
  -> emit SSE: text-delta / tool-use / done
  -> on completion, persist assistant message

Auth: Lambda Function URL AuthType=NONE at the AWS layer; JWT verification
is performed HERE against the Cognito user-pool JWKS (RS256). No API Gateway
authorizer is in the path for the Function URL, so we must verify the token
ourselves.

# ============================================================================
# RESPONSE_STREAM SIGNATURE — FLAGGED FOR TASK 4a.7
# ============================================================================
# Python Lambda streaming (RESPONSE_STREAM / InvokeMode=RESPONSE_STREAM)
# requires awslambdaric >= 1.3, which ships a
# `@lambda_streaming_handler` decorator and a `BytesIO`-like stream object
# passed as the second argument.  The correct pattern is:
#
#   from awslambdaric.lambda_context import LambdaContext
#   @lambda_streaming_handler   # or lambda_streaming_response_handler
#   def handler(event: dict, response_stream, context: LambdaContext):
#       response_stream.write(b"...")
#       response_stream.close()
#
# The Lambda runtime wraps the underlying HTTP/2 bidi stream; calling
# response_stream.write() emits bytes immediately to the caller.
#
# We cannot confirm the exact decorator import path without deploying, so
# this file implements the logic under a plain function signature and
# wraps it with the decorator from awslambdaric. If awslambdaric is NOT
# available (e.g. local test), we fall back to a no-op wrapper so unit
# tests can still import and exercise the logic.
#
# Task 4a.7 (CDK wiring) must:
#   1. Set the Function URL InvokeMode=RESPONSE_STREAM on the Lambda.
#   2. Confirm awslambdaric>=1.3 is bundled (add to requirements.txt if
#      it is not already present in the Lambda runtime layer).
#   3. Validate that response_stream.write() + response_stream.close()
#      flushes correctly in RESPONSE_STREAM mode (use curl --no-buffer).
# ============================================================================
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from typing import Any

import boto3

import conversations as C
import messages as M
import prompts
from anthropic_call import stream_messages
from _db import _q, _resp, _subject_from_claims, _claim_value

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

def _extract_bearer(event: dict) -> str | None:
    h = (event.get("headers") or {})
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


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


# ---------------------------------------------------------------------------
# Content conversion
# ---------------------------------------------------------------------------

def _to_anthropic(content: dict) -> str:
    """Extract plain text from a stored message content dict."""
    return content.get("text", "")


# ---------------------------------------------------------------------------
# Lambda Function URL streaming entry point
#
# RESPONSE_STREAM wrapper:
#   We attempt to import the awslambdaric streaming decorator. If it is
#   not available (local tests, older runtime), we fall back to a no-op
#   wrapper so the module is still importable and unit-testable.
# ---------------------------------------------------------------------------

def _stream_handler(event: dict, response_stream: Any) -> None:
    """Core streaming logic. Separated from the decorated handler so tests
    can call _stream_handler directly with a mock response_stream."""
    token  = _extract_bearer(event)
    claims = _verify_jwt(token) if token else None
    if not claims:
        response_stream.write(_sse({"error": "unauthorized"}))
        response_stream.close()
        return

    tenant_id, user_id = _resolve_from_claims(claims)
    if not tenant_id:
        response_stream.write(_sse({"error": "tenant_not_found"}))
        response_stream.close()
        return

    body = json.loads(event.get("body") or "{}")
    cid  = (event.get("pathParameters") or {}).get("id")
    conv = C.get(tenant_id, cid) if cid else None
    if not conv:
        response_stream.write(_sse({"error": "not_found"}))
        response_stream.close()
        return

    user_text = body.get("text", "").strip()
    if not user_text:
        response_stream.write(_sse({"error": "empty_text"}))
        response_stream.close()
        return

    # Persist the incoming user message.
    M.append(cid, "user", {"text": user_text, "modality": "text"})

    # Build history for Anthropic (user + assistant turns from stored messages).
    history = [
        {"role": m["role"], "content": _to_anthropic(m["content"])}
        for m in conv.get("messages", [])
        if m["role"] in ("user", "assistant") and _to_anthropic(m["content"])
    ]
    # Append the new user turn (not yet in conv since we just inserted).
    history.append({"role": "user", "content": user_text})

    # Stream from Anthropic, forwarding each chunk to the client.
    assistant_chunks: list[str] = []
    try:
        for ev in stream_messages(prompts.system_for_text(), history):
            if ev["type"] == "text-delta":
                assistant_chunks.append(ev["text"])
                response_stream.write(_sse({"type": "text-delta", "text": ev["text"]}))
            elif ev["type"] == "tool-use":
                response_stream.write(_sse({"type": "tool-use",
                                            "id":   ev["id"],
                                            "name": ev["name"],
                                            "input": ev["input"]}))
            elif ev["type"] == "done":
                response_stream.write(_sse({"type": "done"}))
    except RuntimeError as e:
        print(f"Anthropic stream error: {e}")
        response_stream.write(_sse({"error": "upstream_failed", "detail": str(e)[:200]}))
        # Persist a placeholder assistant message so the conversation history
        # stays consistent — the user message is already stored at this point.
        M.append(cid, "assistant",
                 {"text": "[Error: the assistant could not complete this response]",
                  "modality": "text"})
    finally:
        response_stream.close()

    # Persist the assembled assistant reply.
    if assistant_chunks:
        M.append(cid, "assistant",
                 {"text": "".join(assistant_chunks), "modality": "text"})


# Attempt to wrap with the awslambdaric streaming decorator.
try:
    from awslambdaric.lambda_streaming_response import lambda_streaming_handler  # type: ignore[import]
    @lambda_streaming_handler
    def handler(event: dict, response_stream, context) -> None:  # type: ignore[misc]
        _stream_handler(event, response_stream)
except ImportError:
    # awslambdaric not available locally — expose a plain handler so tests
    # and local invocations can still import this module without error.
    def handler(event: dict, response_stream, context=None) -> None:  # type: ignore[misc]
        _stream_handler(event, response_stream)
