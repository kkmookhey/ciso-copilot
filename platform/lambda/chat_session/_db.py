"""Shared Data-API helpers for the chat_session Lambda.

Mirrors the _resp / tenant-resolution pattern used across the other
v1 Lambdas. CORS header is mandatory on every response — see HANDOFF.md
gotcha 11.

Tenant resolution follows the same pattern as policies/main.py and
voice_session/main.py: resolve sso_subject from the Cognito identities
claim (falling back to sub), then do a DB lookup on users.sso_subject.
There are NO custom:tenant_id / custom:user_id claims in this stack.
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

_CORS = {
    "content-type": "application/json",
    "access-control-allow-origin": "*",
}


def _resp(status: int, body: dict | list) -> dict:
    return {"statusCode": status, "headers": _CORS, "body": json.dumps(body)}


def _claim_value(field: dict):
    """Unwrap one Data-API column value."""
    if field.get("isNull"):
        return None
    for k in ("stringValue", "longValue", "booleanValue", "doubleValue"):
        if k in field:
            return field[k]
    return None


def _q(sql: str, params: dict | None = None) -> list[list[dict]]:
    """Run a parameterized statement; return raw Data-API records."""
    kwargs: dict[str, Any] = {
        "resourceArn": DB_CLUSTER_ARN,
        "secretArn":   DB_SECRET_ARN,
        "database":    DB_NAME,
        "sql":         sql,
    }
    if params:
        kwargs["parameters"] = [
            {"name": k, "value": _wrap(v)} for k, v in params.items()
        ]
    return rds_data.execute_statement(**kwargs).get("records", [])


def _wrap(v) -> dict:
    if v is None:
        return {"isNull": True}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"longValue": v}
    return {"stringValue": str(v)}


# ---------------------------------------------------------------------------
# Subject / tenant resolution
# ---------------------------------------------------------------------------

def _subject_from_claims(claims: dict) -> str | None:
    """Resolve sso_subject from Cognito claims.

    For federated users the upstream IdP userId lives in the 'identities'
    claim (JSON array). That is what we stored in users.sso_subject, so we
    prefer it over Cognito's own 'sub'. Mirrors voice_session + policies.
    """
    raw = claims.get("identities")
    if raw:
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(ids, dict):
                ids = [ids]
            if ids:
                return ids[0].get("userId") or claims.get("sub")
        except (TypeError, ValueError):
            pass
    return claims.get("sub")


def _resolve_tenant_id(event: dict) -> str | None:
    """Return the tenant_id for the authenticated caller.

    Fast path: when the streaming Function-URL handler has already verified
    the JWT and injected _tenant_id onto the event dict, return it directly
    (no DB call).

    Normal path (API Gateway): extract sso_subject from Cognito claims, then
    SELECT tenant_id FROM users — same as policies/main.py.
    """
    # Injection override (Function-URL / streaming path)
    if event.get("_tenant_id"):
        return event["_tenant_id"]

    claims = (
        (event.get("requestContext") or {})
        .get("authorizer", {})
        .get("claims") or {}
    )
    sso_subject = _subject_from_claims(claims)
    if not sso_subject:
        return None

    rows = _q(
        "SELECT tenant_id::text FROM users WHERE sso_subject = :s LIMIT 1",
        {"s": sso_subject},
    )
    return rows[0][0].get("stringValue") if rows else None


def _resolve_user_context(event: dict) -> tuple[str | None, str | None, str | None]:
    """Return (email, tenant_id, user_id) for the authenticated caller.

    Fast path: when _tenant_id / _user_id / _email are injected (Function-URL
    streaming path), return them directly.

    Normal path: JOIN users + tenants on sso_subject — mirrors voice_session.
    Returns user_id (UUID) rather than tenant display_name because chat
    messages need to store the author; callers that need tenant_name can call
    _resolve_tenant_id separately or extend this query.
    """
    if event.get("_tenant_id") and event.get("_user_id"):
        return (
            event.get("_email"),
            event["_tenant_id"],
            event["_user_id"],
        )

    claims = (
        (event.get("requestContext") or {})
        .get("authorizer", {})
        .get("claims") or {}
    )
    sso_subject = _subject_from_claims(claims)
    if not sso_subject:
        return None, None, None

    rows = _q(
        "SELECT u.email, u.tenant_id::text, u.user_id::text "
        "FROM users u "
        "WHERE u.sso_subject = :s LIMIT 1",
        {"s": sso_subject},
    )
    if not rows:
        return None, None, None
    r = rows[0]
    return (
        _claim_value(r[0]),
        _claim_value(r[1]),
        _claim_value(r[2]),
    )
