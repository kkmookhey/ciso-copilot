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
                         code_challenge: str,
                         scope: str = "",
                         user_scope: str = USER_SCOPES) -> str:
    """Build the Slack OAuth v2 authorize URL.

    By default produces a user-token URL (user_scope=USER_SCOPES, no scope).
    Pass scope=<bot scopes> and user_scope="" for a bot-token install.
    When user_scope is empty it is omitted from the URL; when scope is empty
    it is likewise omitted.
    """
    qs: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        qs["scope"] = scope
    if user_scope:
        qs["user_scope"] = user_scope
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
    # Slack OAuth v2 response shape differs by scope kind:
    # - With bot scopes registered, the bot token is at top-level
    #   (access_token, refresh_token, expires_in, scope).
    # - With user scopes (what Shasta uses), the analyst's token is
    #   nested under authed_user (access_token, refresh_token,
    #   expires_in, scope). Top-level fields are absent.
    # We prefer authed_user when present, otherwise fall back to
    # top-level — this keeps the implementation robust if we later
    # register bot scopes too.
    au = body.get("authed_user") or {}
    access_token = au.get("access_token") or body.get("access_token")
    if not access_token:
        raise RuntimeError(
            "slack oauth: no access_token in response — "
            "confirm USER token scopes are registered on the Slack App"
        )
    refresh_token_val = au.get("refresh_token") or body.get("refresh_token", "")
    expires_in = au.get("expires_in") or body.get("expires_in", 43200)
    scope_str = au.get("scope") or body.get("scope", "")
    return {
        "access_token": access_token,
        "refresh_token": refresh_token_val,
        "expires_in": int(expires_in),
        "scopes": [s for s in scope_str.split(",") if s],
        "vendor_user_id": au.get("id") or body.get("authed_user", {}).get("id", ""),
        "vendor_workspace_id": body["team"]["id"],
        "mcp_server_url": MCP_SERVER_URL,
    }


def exchange_code_bot(*, code: str, code_verifier: str,
                      client_id: str, client_secret: str,
                      redirect_uri: str) -> dict:
    """OAuth code exchange for the admin BOT install.

    Slack's oauth.v2.access returns both a user token (in authed_user)
    and a bot token (top-level). For the workspace-bot flow we want the
    top-level bot token (xoxb-...) and the team.id.
    """
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "code_verifier": code_verifier,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"slack oauth (bot): {body.get('error', 'unknown')}")
    return {
        "access_token":   body["access_token"],       # xoxb-...
        "team_id":        body["team"]["id"],
        "scopes":         [s for s in (body.get("scope") or "").split(",") if s],
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
