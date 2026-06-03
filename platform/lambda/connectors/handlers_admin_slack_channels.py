"""Admin handlers: list Slack channels + set broadcast channel.

Both routes require:
  - users.role = 'admin' (enforced via _require_admin)
  - An active tenant_bot_connectors row for the tenant + Slack

Routes registered here:
  GET    /connectors/admin/slack/channels          — picker payload
  POST   /connectors/admin/slack/broadcast-channel — set broadcast channel (anti-tamper)
  PATCH  /connectors/admin/slack/autonomous-rule   — toggle autonomous rule on/off
  DELETE /connectors/admin/slack                   — revoke workspace bot install

Implementation note: the channel picker uses Slack's direct Web API
(https://slack.com/api/conversations.list, auth.revoke) instead of
mcp_oauth.get_admin_session, because Slack's MCP server
(https://mcp.slack.com/mcp) only accepts user-scope tokens (xoxp-...).
Bot tokens (xoxb-...) returned by oauth.v2.access for the admin install
get 401 from MCP. Direct Slack Web API accepts both token types and is
strictly simpler for fire-and-forget calls anyway.
"""
from __future__ import annotations
import json

import requests

from connectors.main import _route, _resp
import connectors.handlers_slack_workspace_bot as _wsbot
from mcp_oauth.session import _db, ConnectorMissingError
from mcp_oauth.crypto import decrypt_token


def _bot_token(tenant_id: str) -> str:
    """Decrypt and return the active bot token for the tenant.

    Raises ConnectorMissingError if no active row exists.
    """
    row = _db().execute("""
        SELECT access_token_enc, access_data_key_ct
        FROM tenant_bot_connectors
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
          AND status = 'active'
        LIMIT 1
    """, [{"name": "tid", "value": {"stringValue": tenant_id}}]).fetchone()
    if not row:
        raise ConnectorMissingError(f"no active slack bot for tenant {tenant_id}")
    return decrypt_token(row["access_token_enc"], row["access_data_key_ct"])


def _slack_get(method: str, token: str, params: dict | None = None) -> dict:
    """Call a Slack Web API method with the bot token; raise on non-ok."""
    resp = requests.get(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"slack {method}: {body.get('error', 'unknown')}")
    return body


def _slack_post(method: str, token: str, data: dict | None = None) -> dict:
    """Call a Slack Web API method (POST form-encoded) with the bot token."""
    resp = requests.post(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        data=data or {},
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"slack {method}: {body.get('error', 'unknown')}")
    return body


def _list_channels(tenant_id: str) -> list[dict]:
    """Return [{id, name, is_private}] for channels the bot is in or can see."""
    token = _bot_token(tenant_id)
    # Need both public + private channels (groups). One call with types= covers both.
    body = _slack_get("conversations.list", token, params={
        "types": "public_channel,private_channel",
        "limit": 1000,
        "exclude_archived": "true",
    })
    return [
        {"id": c["id"], "name": c["name"], "is_private": bool(c.get("is_private"))}
        for c in body.get("channels", [])
    ]


def _channel_exists(tenant_id: str, channel_id: str) -> bool:
    """Anti-tamper: confirm the supplied channel_id is in the bot's
    conversations.list response. Prevents setting broadcast_channel_id
    to a channel the bot can't post to."""
    try:
        return channel_id in {c["id"] for c in _list_channels(tenant_id)}
    except ConnectorMissingError:
        return False


@_route("GET", r"^/connectors/admin/slack/channels$")
def list_channels(event, claims, _params):
    tenant_id, _user_id = _wsbot._require_admin(claims)
    if not tenant_id:
        return _resp(403, {"error": "admin_required"})

    try:
        channels = _list_channels(tenant_id)
    except ConnectorMissingError:
        return _resp(409, {"error": "bot_not_installed"})

    return _resp(200, {"channels": channels})


@_route("POST", r"^/connectors/admin/slack/broadcast-channel$")
def set_broadcast_channel(event, claims, _params):
    tenant_id, _user_id = _wsbot._require_admin(claims)
    if not tenant_id:
        return _resp(403, {"error": "admin_required"})

    body = json.loads(event.get("body") or "{}")
    channel_id = body.get("channel_id")
    channel_name = body.get("channel_name", "")
    if not channel_id:
        return _resp(400, {"error": "missing_channel_id"})

    if not _channel_exists(tenant_id, channel_id):
        return _resp(400, {"error": "channel_not_in_workspace"})

    _db().execute("""
        UPDATE tenant_bot_connectors
        SET broadcast_channel_id = :chan,
            broadcast_channel_name = :chname
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
          AND status = 'active'
    """, [
        {"name": "tid",    "value": {"stringValue": tenant_id}},
        {"name": "chan",   "value": {"stringValue": channel_id}},
        {"name": "chname", "value": {"stringValue": channel_name}},
    ])
    return _resp(200, {"ok": True, "channel_id": channel_id})


@_route("PATCH", r"^/connectors/admin/slack/autonomous-rule$")
def toggle_autonomous_rule(event, claims, _params):
    """Flip autonomous_rule_enabled on the tenant_bot_connectors row."""
    tenant_id, _user_id = _wsbot._require_admin(claims)
    if not tenant_id:
        return _resp(403, {"error": "admin_required"})

    body = json.loads(event.get("body") or "{}")
    enabled = bool(body.get("enabled", True))
    _db().execute("""
        UPDATE tenant_bot_connectors
        SET autonomous_rule_enabled = :en
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
          AND status = 'active'
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "en",  "value": {"booleanValue": enabled}},
    ])
    return _resp(200, {"ok": True, "enabled": enabled})


@_route("DELETE", r"^/connectors/admin/slack$")
def revoke_workspace_bot(event, claims, _params):
    """Revoke the admin bot install. Marks status='revoked' locally;
    Slack's revoke endpoint is best-effort."""
    tenant_id, _user_id = _wsbot._require_admin(claims)
    if not tenant_id:
        return _resp(403, {"error": "admin_required"})

    # Best-effort Slack auth.revoke — if it fails, the local revoke still
    # happens. Uses direct Web API for the same reason _list_channels does
    # (MCP rejects bot tokens with 401).
    try:
        token = _bot_token(tenant_id)
        _slack_get("auth.revoke", token)
    except Exception as e:
        print(f"[connectors] Slack auth.revoke failed: {e!r} (continuing)")

    _db().execute("""
        UPDATE tenant_bot_connectors
        SET status = 'revoked', revoked_at = now()
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
    """, [{"name": "tid", "value": {"stringValue": tenant_id}}])
    return _resp(200, {"revoked": True})


@_route("GET", r"^/connectors/admin/slack/status$")
def admin_bot_status(event, claims, _params):
    """Returns the admin's tenant_bot_connectors row state — used by
    the web Settings UI to render the right install/picker/configured
    block state. Admin-only."""
    tenant_id, _user_id = _wsbot._require_admin(claims)
    if not tenant_id:
        return _resp(403, {"error": "admin_required"})

    row = _db().execute("""
        SELECT broadcast_channel_id, broadcast_channel_name,
               autonomous_rule_enabled, status
        FROM tenant_bot_connectors
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
    """, [{"name": "tid", "value": {"stringValue": tenant_id}}]).fetchone()

    if not row or row["status"] != "active":
        return _resp(200, {
            "installed": False,
            "broadcast_channel_id": None,
            "broadcast_channel_name": None,
            "autonomous_rule_enabled": False,
        })
    return _resp(200, {
        "installed": True,
        "broadcast_channel_id": row.get("broadcast_channel_id"),
        "broadcast_channel_name": row.get("broadcast_channel_name"),
        "autonomous_rule_enabled": bool(row["autonomous_rule_enabled"]),
    })
