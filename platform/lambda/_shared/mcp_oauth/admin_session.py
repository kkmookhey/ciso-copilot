"""MCP session helper for tenant-level admin-installed bots.

Mirrors mcp_oauth.session.get_session but resolves against
tenant_bot_connectors instead of user_connectors. Used by:
  - findings_subscriber Lambda (autonomous broadcast)
  - connectors/handlers_admin_slack channel picker (conversations.list)

Token refresh + KMS-envelope decrypt reuses the helpers from session.py
to avoid duplication. The advisory-lock key is bot_id (not conn_id) but
the lock pattern is identical.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Literal

from .session import (
    _db, _zip_record, decrypt_token,
    ConnectorMissingError, ConnectorRevokedError,
)


BotKind = Literal["slack"]


def lookup_tenant_bot(*, tenant_id: str, kind: BotKind) -> dict:
    """Return the active tenant_bot_connectors row for (tenant, provider).

    Raises ConnectorMissingError if no active row exists.
    """
    sql = """
        SELECT bot_id, access_token_enc, access_data_key_ct,
               access_expires_at, mcp_server_url,
               vendor_workspace_id, broadcast_channel_id,
               autonomous_rule_enabled
        FROM tenant_bot_connectors
        WHERE tenant_id = :tid::uuid
          AND oauth_provider = :provider
          AND status = 'active'
    """
    row = _db().execute(sql, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "provider", "value": {"stringValue": kind}},
    ]).fetchone()
    if not row:
        raise ConnectorMissingError(f"no active {kind} bot for tenant {tenant_id}")
    return row


@asynccontextmanager
async def get_admin_session(tenant_id: str, kind: BotKind = "slack"):
    """Open an MCP session against the tenant's admin-installed bot.

    Used for autonomous broadcast and the post-install channel picker.
    """
    row = lookup_tenant_bot(tenant_id=tenant_id, kind=kind)
    # No refresh path in Slice 2 — Slack bot tokens issued without
    # token_rotation_enabled don't expire. (If rotation is later enabled
    # on the Shasta Slack App's bot scopes, copy the refresh_if_near_expiry
    # pattern from session.py keyed by bot_id.)
    access_token = decrypt_token(row["access_token_enc"],
                                 row["access_data_key_ct"])

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(
        row["mcp_server_url"],
        headers={"Authorization": f"Bearer {access_token}"},
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session
