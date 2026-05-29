"""Slack OAuth v2 provider config."""
from __future__ import annotations
import urllib.parse
import requests

# Per-user (user-token) scopes — analyst acts as themselves.
USER_SCOPES = "chat:write,im:write,im:history,search:read,users:read"

MCP_SERVER_URL = "https://mcp.slack.com/mcp"
AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
TOKEN_URL = "https://slack.com/api/oauth.v2.access"


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str,
                         code_challenge: str) -> str:
    qs = {
        "client_id": client_id,
        "user_scope": USER_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(qs)


def exchange_code(*, code: str, code_verifier: str, client_id: str,
                   client_secret: str, redirect_uri: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "code": code,
        "code_verifier": code_verifier,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"slack oauth: {body.get('error', 'unknown_error')}")
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_in": body.get("expires_in", 43200),
        "scopes": body.get("scope", "").split(","),
        "vendor_user_id": body["authed_user"]["id"],
        "vendor_workspace_id": body["team"]["id"],
        "mcp_server_url": MCP_SERVER_URL,
    }


def refresh_token(*, refresh_token: str, client_id: str, client_secret: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"slack oauth refresh: {body.get('error', 'unknown_error')}")
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", refresh_token),  # may rotate
        "expires_in": body.get("expires_in", 43200),
    }
