"""Tests for mcp_oauth.admin_session — opens MCP session for the tenant's
admin-installed bot token (autonomous broadcast path)."""
from __future__ import annotations
from unittest.mock import MagicMock
import pytest


def test_lookup_tenant_bot_returns_active_row(monkeypatch):
    from mcp_oauth.admin_session import lookup_tenant_bot

    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.return_value = {
        "bot_id": "b-1", "access_token_enc": b"E:xoxb",
        "access_data_key_ct": b"DK", "access_expires_at": None,
        "mcp_server_url": "https://mcp.slack.com/mcp",
        "broadcast_channel_id": "C0X",
        "autonomous_rule_enabled": True,
    }
    monkeypatch.setattr("mcp_oauth.admin_session._db", lambda: fake_db)

    row = lookup_tenant_bot(tenant_id="t-1", kind="slack")
    assert row["bot_id"] == "b-1"
    assert row["broadcast_channel_id"] == "C0X"


def test_lookup_tenant_bot_missing_raises(monkeypatch):
    from mcp_oauth.admin_session import lookup_tenant_bot
    from mcp_oauth.session import ConnectorMissingError

    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.return_value = None
    monkeypatch.setattr("mcp_oauth.admin_session._db", lambda: fake_db)

    with pytest.raises(ConnectorMissingError):
        lookup_tenant_bot(tenant_id="t-1", kind="slack")
