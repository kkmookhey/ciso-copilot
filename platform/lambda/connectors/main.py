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

# Populate SSM-backed secrets into os.environ at cold-start (CFN can't
# inject SecureString params as env vars).
from connectors import _secrets  # noqa: F401


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


_ROUTES = []  # list of (method, regex, handler, requires_auth)


def _route(method: str, pattern: str, *, requires_auth: bool = True):
    def deco(fn):
        _ROUTES.append((method, re.compile(pattern), fn, requires_auth))
        return fn
    return deco


def handler(event: dict, context) -> dict:
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
    path = event.get("rawPath") or event.get("path") or ""
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}

    # Match route FIRST so we can decide whether auth is required.
    matched = None
    for m, rx, fn, requires_auth in _ROUTES:
        if m != method:
            continue
        match = rx.match(path)
        if not match:
            continue
        matched = (fn, match, requires_auth)
        break

    if matched is None:
        return _resp(404, {"error": "unknown_route", "path": path})

    fn, match, requires_auth = matched
    if requires_auth:
        subject = subject_from_claims(claims)
        if not subject:
            return _resp(401, {"error": "no_auth"})

    try:
        return fn(event, claims, match.groupdict())
    except Exception as e:
        print(f"[connectors] {method} {path} failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return _resp(500, {"error": "internal", "detail": str(e)[:200]})


def _resp(status: int, body: dict, *, headers: dict | None = None) -> dict:
    h = {"content-type": "application/json", "access-control-allow-origin": "*"}
    if headers:
        h.update(headers)
    return {"statusCode": status, "headers": h, "body": json.dumps(body)}


# Per-route handlers (decorate with @_route). Slack initiate / callback / etc.
# are registered in task 11+.
from connectors import handlers_slack  # noqa: F401,E402
from connectors import handlers_common  # noqa: F401,E402
from connectors import handlers_slack_workspace_bot  # noqa: F401,E402 — registers /connectors/{connect,callback}/slack-workspace-bot routes
from connectors import handlers_admin_slack_channels  # noqa: F401,E402 — registers /connectors/admin/slack/* routes
