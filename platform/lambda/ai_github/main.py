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
