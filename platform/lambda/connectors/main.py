"""Connectors Lambda — OAuth orchestration for MCP integrations.

Routes (all under /v1/connectors):
  POST   /connect/{kind}        initiate OAuth, returns authorize_url
  GET    /callback/{kind}       handle vendor redirect, store tokens
  DELETE /{conn_id}             revoke connection
  GET    /me                    list current user's active connectors

Per-kind specifics live in mcp_oauth.providers.{kind}. This module owns
HTTP shape, auth, route dispatch, and the DB write/delete operations.
"""
from __future__ import annotations
import json
import re
import traceback


# Reuse the canonical subject-extraction helper from the tools Lambda pattern.
def subject_from_claims(claims: dict) -> str | None:
    """Resolve the upstream-IdP subject for users.sso_subject JOINs.

    For federated logins (Microsoft/Google), `claims.sub` is the Cognito-
    user-pool sub — NOT the upstream IdP sub. `identities[0].userId` is the
    upstream value. Returns None when neither is present (caller should
    return 401).
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


_ROUTES = []  # list of (method, regex, handler)


def _route(method: str, pattern: str):
    def deco(fn):
        _ROUTES.append((method, re.compile(pattern), fn))
        return fn
    return deco


def handler(event: dict, context) -> dict:
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
    path = event.get("rawPath") or event.get("path") or ""
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}

    subject = subject_from_claims(claims)
    if not subject:
        return _resp(401, {"error": "no_auth"})

    for m, rx, fn in _ROUTES:
        if m != method:
            continue
        match = rx.match(path)
        if not match:
            continue
        try:
            return fn(event, claims, match.groupdict())
        except Exception as e:
            print(f"[connectors] {method} {path} failed: {type(e).__name__}: {e}")
            traceback.print_exc()
            return _resp(500, {"error": "internal", "detail": str(e)[:200]})

    return _resp(404, {"error": "unknown_route", "path": path})


def _resp(status: int, body: dict, *, headers: dict | None = None) -> dict:
    h = {"content-type": "application/json", "access-control-allow-origin": "*"}
    if headers:
        h.update(headers)
    return {"statusCode": status, "headers": h, "body": json.dumps(body)}


# Per-route handlers (decorate with @_route). Slack initiate / callback / etc.
# are registered in task 11+.
from connectors import handlers_slack  # noqa: F401,E402
from connectors import handlers_common  # noqa: F401,E402
