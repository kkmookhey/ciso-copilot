# platform/lambda/tools/revoke_oauth_grant.py
"""Revoke a user's OAuth grant for a specific application in Entra.

Uses Microsoft Graph DELETE /oauth2PermissionGrants/{id}.
Requires the Entra app to have DelegatedPermissionGrant.ReadWrite.All.
"""
from __future__ import annotations
import datetime as dt
import os

import requests
import msal

from tools.main import register


_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TENANT_ID  = os.environ.get("ENTRA_TENANT_ID")
_CLIENT_ID  = os.environ.get("ENTRA_CLIENT_ID")
_CLIENT_SECRET = os.environ.get("ENTRA_CLIENT_SECRET")
_token_cache: str | None = None


@register("revoke_oauth_grant")
def handle(args: dict, claims: dict) -> dict:
    user_object_id = args["user_object_id"]
    app_id         = args["app_id"]

    grant_id = _find_grant_id(user_object_id=user_object_id, app_id=app_id)
    if not grant_id:
        return {
            "revoked":   False,
            "reason":    "no_grant_found",
            "speakable": "No active OAuth grant found for that user and app.",
        }
    _graph_delete(grant_id)
    return {
        "revoked":    True,
        "revoked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "speakable":  "OAuth grant revoked — confirmed via Graph.",
    }


def _find_grant_id(*, user_object_id: str, app_id: str) -> str | None:
    token = _graph_token()
    url = (f"{_GRAPH_BASE}/oauth2PermissionGrants"
           f"?$filter=principalId eq '{user_object_id}' "
           f"and clientId eq '{app_id}'")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    r.raise_for_status()
    items = r.json().get("value", [])
    return items[0]["id"] if items else None


def _graph_delete(grant_id: str) -> None:
    token = _graph_token()
    url = f"{_GRAPH_BASE}/oauth2PermissionGrants/{grant_id}"
    r = requests.delete(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    r.raise_for_status()


def _graph_token() -> str:
    global _token_cache
    if _token_cache:
        return _token_cache
    if not (_TENANT_ID and _CLIENT_ID and _CLIENT_SECRET):
        raise RuntimeError("ENTRA_TENANT_ID / ENTRA_CLIENT_ID / ENTRA_CLIENT_SECRET must be set")
    app = msal.ConfidentialClientApplication(
        client_id=_CLIENT_ID,
        client_credential=_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{_TENANT_ID}",
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Graph token mint failed: {result.get('error_description')}")
    _token_cache = result["access_token"]
    return _token_cache
