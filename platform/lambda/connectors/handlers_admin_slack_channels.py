"""Admin handlers: list Slack channels + set broadcast channel.

Both routes require:
  - users.role = 'admin' (enforced via _require_admin)
  - An active tenant_bot_connectors row for the tenant + Slack

Routes registered here:
  GET    /connectors/admin/slack/channels          — picker payload
  POST   /connectors/admin/slack/broadcast-channel — set broadcast channel (anti-tamper)
  PATCH  /connectors/admin/slack/autonomous-rule   — toggle autonomous rule on/off
  DELETE /connectors/admin/slack                   — revoke workspace bot install
"""
from __future__ import annotations
import asyncio
import json

from connectors.main import _route, _resp
import connectors.handlers_slack_workspace_bot as _wsbot
from mcp_oauth import get_admin_session
from mcp_oauth.session import _db, ConnectorMissingError


def _channel_exists(tenant_id: str, channel_id: str) -> bool:
    """Return True iff channel_id appears in the bot's conversations_list.

    Anti-tamper: prevent an attacker (or buggy UI) from setting
    broadcast_channel_id to a channel the bot doesn't have access to.
    Re-runs conversations_list — cheap (cached in Slack's MCP for ~30s).
    """
    try:
        async def _check():
            async with get_admin_session(tenant_id, "slack") as session:
                result = await session.call_tool("conversations_list", {})
                payload = json.loads(result.content[0].text)
                ids = {c["id"] for c in payload.get("channels", [])}
                return channel_id in ids
        return asyncio.run(_check())
    except ConnectorMissingError:
        return False


@_route("GET", r"^/connectors/admin/slack/channels$")
def list_channels(event, claims, _params):
    tenant_id, _user_id = _wsbot._require_admin(claims)
    if not tenant_id:
        return _resp(403, {"error": "admin_required"})

    try:
        async def _fetch():
            async with get_admin_session(tenant_id, "slack") as session:
                result = await session.call_tool("conversations_list", {})
                return json.loads(result.content[0].text)
        payload = asyncio.run(_fetch())
    except ConnectorMissingError:
        return _resp(409, {"error": "bot_not_installed"})

    return _resp(200, {"channels": payload.get("channels", [])})


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

    # Best-effort Slack auth.revoke — if it fails the local revoke still happens.
    try:
        async def _revoke_upstream():
            async with get_admin_session(tenant_id, "slack") as session:
                await session.call_tool("auth_revoke", {})
        asyncio.run(_revoke_upstream())
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
