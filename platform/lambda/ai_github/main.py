"""Lambda handler for /v1/ai/connections/github/* and /v1/ai/connections/*

Routes (path, method):
  POST  /v1/ai/connections/github/install_url
  POST  /v1/ai/connections/github/complete
  GET   /v1/ai/connections
  GET   /v1/ai/connections/{id}/repos
  DELETE /v1/ai/connections/{id}
"""
from __future__ import annotations

import json
import os
import urllib.parse
import uuid

import github_app
import helpers
import state_jwt

GITHUB_APP_SLUG  = os.environ["GITHUB_APP_SLUG"]
WEB_CALLBACK_URL = os.environ["WEB_CALLBACK_URL"]
STATE_TTL_SECONDS = 300  # 5 minutes


def handler(event: dict, context) -> dict:
    method = event.get("httpMethod") or ""
    path   = event.get("path") or ""

    try:
        if method == "POST" and path == "/v1/ai/connections/github/install_url":
            return _install_url(event)
        if method == "POST" and path == "/v1/ai/connections/github/complete":
            return _complete(event)
        return helpers.resp(404, {"error": "not_found", "path": path, "method": method})
    except Exception as e:  # noqa: BLE001 — top-level fence
        # Surface message; production observability already logs to CloudWatch.
        return helpers.resp(500, {"error": "internal", "detail": str(e)})


# ----------------------------------------------------------------------------
# POST /v1/ai/connections/github/install_url
# ----------------------------------------------------------------------------

def _install_url(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})

    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    user_sub = claims.get("sub") or ""

    state = state_jwt.sign(
        {"tenant_id": tenant_id, "user_sub": user_sub},
        ttl_seconds=STATE_TTL_SECONDS,
    )
    install_url = (
        f"https://github.com/apps/{GITHUB_APP_SLUG}/installations/new"
        f"?state={urllib.parse.quote(state)}"
    )
    return helpers.resp(200, {"install_url": install_url})


# ----------------------------------------------------------------------------
# POST /v1/ai/connections/github/complete
# ----------------------------------------------------------------------------

def _complete(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return helpers.resp(400, {"error": "invalid_json"})

    installation_id = body.get("installation_id")
    state           = body.get("state")
    if not isinstance(installation_id, int) or not isinstance(state, str):
        return helpers.resp(400, {"error": "missing_fields"})

    try:
        decoded = state_jwt.verify(state)
    except ValueError as e:
        return helpers.resp(400, {"error": "bad_state", "detail": str(e)})

    if decoded.get("tenant_id") != tenant_id:
        return helpers.resp(403, {"error": "tenant_mismatch"})

    # Validate the installation exists + grab org metadata.
    app_jwt = github_app.mint_app_jwt()
    status, gh_body, _ = github_app._http_get(
        f"{github_app.GITHUB_API_BASE}/app/installations/{installation_id}",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept":        "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if status != 200:
        return helpers.resp(400, {"error": "installation_lookup_failed",
                                  "github_status": status})

    account     = gh_body.get("account") or {}
    org_name    = account.get("login")
    account_typ = account.get("type")  # 'User' | 'Organization'

    conn_id = str(uuid.uuid4())
    helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "INSERT INTO ai_connections "
            "  (id, tenant_id, provider, status, github_installation_id, "
            "   github_org_name, github_account_type) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), 'github', 'active', "
            "        :inst, :org, :acct) "
            "ON CONFLICT (tenant_id, provider, github_installation_id) "
            "  DO UPDATE SET status='active', github_org_name=EXCLUDED.github_org_name, "
            "                github_account_type=EXCLUDED.github_account_type, "
            "                updated_at=NOW() "
            "RETURNING id::text"
        ),
        parameters=[
            {"name": "id",   "value": {"stringValue": conn_id}},
            {"name": "tid",  "value": {"stringValue": tenant_id}},
            {"name": "inst", "value": {"longValue":   installation_id}},
            {"name": "org",  "value": {"stringValue": org_name or ""}},
            {"name": "acct", "value": {"stringValue": account_typ or ""}},
        ],
    )
    return helpers.resp(200, {"connection_id": conn_id})
