"""Tests for admin-gated Slack workspace bot handlers."""
from __future__ import annotations
from unittest.mock import MagicMock


def test_require_admin_returns_tenant_user_for_admin(monkeypatch):
    """Admin role → returns (tenant_id, user_id)."""
    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.return_value = {
        "tenant_id": "t-1", "user_id": "u-1",
    }
    monkeypatch.setattr("connectors.handlers_slack_workspace_bot._db", lambda: fake_db)

    from connectors.handlers_slack_workspace_bot import _require_admin
    result = _require_admin({"sub": "subject-admin"})
    assert result == ("t-1", "u-1")


def test_require_admin_rejects_non_admin(monkeypatch):
    """role != 'admin' → returns (None, None) (or whatever no-admin sentinel)."""
    fake_db = MagicMock()
    # SQL filters role='admin' so no row when caller isn't admin.
    fake_db.execute.return_value.fetchone.return_value = None
    monkeypatch.setattr("connectors.handlers_slack_workspace_bot._db", lambda: fake_db)

    from connectors.handlers_slack_workspace_bot import _require_admin
    result = _require_admin({"sub": "subject-member"})
    assert result == (None, None)


def test_require_admin_returns_none_when_no_subject():
    """No sso_subject extractable → (None, None)."""
    from connectors.handlers_slack_workspace_bot import _require_admin
    assert _require_admin({}) == (None, None)


def test_initiate_workspace_bot_returns_authorize_url(monkeypatch):
    """Admin caller → 200 with Slack authorize URL containing bot scopes."""
    import json
    from unittest.mock import patch
    monkeypatch.setenv("SLACK_CLIENT_ID", "abc")
    monkeypatch.setenv("CONNECTORS_REDIRECT_BASE",
                       "https://app.shasta.io/v1/connectors")
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    with patch("connectors.handlers_slack_workspace_bot._require_admin",
               return_value=("t-1", "u-1")), \
         patch("connectors.handlers_slack_workspace_bot.pkce.store_verifier") as store:
        from connectors import main as m
        ev = {
            "httpMethod": "POST",
            "rawPath": "/connectors/connect/slack-workspace-bot",
            "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
        }
        resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    url = body["authorize_url"]
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    # Bot scopes appear in scope= (URL-encoded). At minimum:
    assert "scope=" in url
    assert "chat%3Awrite" in url or "chat:write" in url
    assert "channels%3Aread" in url or "channels:read" in url
    store.assert_called_once()


def test_initiate_workspace_bot_403_for_non_admin(monkeypatch):
    """Non-admin caller → 403 admin_required."""
    import json
    from unittest.mock import patch
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    with patch("connectors.handlers_slack_workspace_bot._require_admin",
               return_value=(None, None)):
        from connectors import main as m
        ev = {
            "httpMethod": "POST",
            "rawPath": "/connectors/connect/slack-workspace-bot",
            "requestContext": {"authorizer": {"claims": {"sub": "member"}}},
        }
        resp = m.handler(ev, None)
    assert resp["statusCode"] == 403
    assert json.loads(resp["body"])["error"] == "admin_required"


def test_callback_workspace_bot_inserts_tenant_bot_row(monkeypatch):
    """Successful bot callback → INSERT INTO tenant_bot_connectors with
    bot token + team_id, status='active', autonomous_rule_enabled=true,
    broadcast_channel_id=NULL."""
    import base64
    import hashlib
    from mcp_oauth import state as st, pkce
    from connectors import handlers_slack_workspace_bot as h

    monkeypatch.setenv("SLACK_CLIENT_ID", "abc")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "xyz")
    monkeypatch.setenv("CONNECTORS_REDIRECT_BASE",
                       "https://app.shasta.io/v1/connectors")
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    monkeypatch.setenv("WEB_BASE_URL", "https://app.shasta.io")

    # Use a real verifier/challenge pair so the PKCE rebuild check passes
    # without needing to monkeypatch the pure challenge_hash function.
    real_verifier, real_challenge = pkce.generate_pair()
    state_tok = st.sign_state(
        tenant_id="t-1", user_id="u-1", provider="slack-bot",
        pkce_verifier_hash=pkce.challenge_hash(real_challenge),
        nonce="n-bot",
    )

    monkeypatch.setattr(h.pkce, "fetch_verifier", lambda nonce: real_verifier)
    monkeypatch.setattr(h.slack_provider, "exchange_code_bot",
                        lambda **kw: {
                            "access_token": "xoxb-BOT",
                            "team_id": "T0XBOT",
                            "scopes": ["chat:write", "channels:read",
                                       "groups:read"],
                            "mcp_server_url": "https://mcp.slack.com/mcp",
                        })
    monkeypatch.setattr(h, "encrypt_token",
                        lambda t: (f"E:{t}".encode(), f"DK:{t}".encode()))

    inserted = {}
    class FakeDB:
        def execute(self, sql, params=None):
            if sql.strip().startswith("INSERT"):
                inserted["sql"] = sql
                inserted["params"] = params
            class R:
                def fetchone(self_inner): return None
            return R()
    monkeypatch.setattr(h, "_db", lambda: FakeDB())

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/callback/slack-workspace-bot",
        "queryStringParameters": {"code": "ac-bot", "state": state_tok},
        "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 302
    assert resp["headers"]["location"].endswith(
        "/settings?tab=connectors&ok=slack-bot")
    assert "INSERT INTO tenant_bot_connectors" in inserted["sql"]
    # autonomous_rule_enabled default = true on schema; we don't bind it.
    # broadcast_channel_id must NOT be set yet — channel picker fires next.
    param_names = {p["name"] for p in inserted["params"]}
    assert "channel" not in param_names, "broadcast_channel_id set too early"


def test_list_channels_returns_picker_payload(monkeypatch):
    """Admin caller → MCP conversations_list via the bot's session →
    returns [{id, name, is_private}]."""
    import json
    from unittest.mock import AsyncMock, MagicMock
    import contextlib

    fake_session = AsyncMock()
    fake_session.call_tool.return_value = MagicMock(content=[
        MagicMock(text=json.dumps({
            "channels": [
                {"id": "C1", "name": "general", "is_private": False},
                {"id": "C2", "name": "shasta-alerts", "is_private": False},
            ]
        }))
    ])

    @contextlib.asynccontextmanager
    async def fake_admin_session(*a, **kw):
        yield fake_session

    monkeypatch.setattr(
        "connectors.handlers_admin_slack_channels.get_admin_session",
        fake_admin_session)
    monkeypatch.setattr(
        "connectors.handlers_slack_workspace_bot._require_admin",
        lambda claims: ("t-1", "u-1"))

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/admin/slack/channels",
        "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["channels"] == [
        {"id": "C1", "name": "general", "is_private": False},
        {"id": "C2", "name": "shasta-alerts", "is_private": False},
    ]


def test_set_broadcast_channel_updates_row(monkeypatch):
    """Admin posts a valid channel_id → tenant_bot_connectors row updates."""
    import json
    from unittest.mock import MagicMock

    updated = {}
    class FakeDB:
        def execute(self, sql, params=None):
            if sql.strip().startswith("UPDATE"):
                updated["sql"] = sql
                updated["params"] = params
            class R:
                def fetchone(self_inner): return None
            return R()
    monkeypatch.setattr(
        "connectors.handlers_admin_slack_channels._db", lambda: FakeDB())
    monkeypatch.setattr(
        "connectors.handlers_slack_workspace_bot._require_admin",
        lambda claims: ("t-1", "u-1"))

    # Mock the anti-tamper channel validation: claim C2 IS in the bot's list.
    monkeypatch.setattr(
        "connectors.handlers_admin_slack_channels._channel_exists",
        lambda tenant_id, channel_id: True)

    from connectors import main as m
    ev = {
        "httpMethod": "POST",
        "rawPath": "/connectors/admin/slack/broadcast-channel",
        "body": json.dumps({"channel_id": "C2", "channel_name": "shasta-alerts"}),
        "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    assert "UPDATE tenant_bot_connectors" in updated["sql"]
    p = {p["name"]: p["value"] for p in updated["params"]}
    assert p["chan"]["stringValue"] == "C2"


def test_set_broadcast_channel_rejects_unknown_id(monkeypatch):
    """Anti-tamper: if channel_id isn't in conversations_list, reject."""
    import json

    monkeypatch.setattr(
        "connectors.handlers_slack_workspace_bot._require_admin",
        lambda claims: ("t-1", "u-1"))
    monkeypatch.setattr(
        "connectors.handlers_admin_slack_channels._channel_exists",
        lambda tenant_id, channel_id: False)

    from connectors import main as m
    ev = {
        "httpMethod": "POST",
        "rawPath": "/connectors/admin/slack/broadcast-channel",
        "body": json.dumps({"channel_id": "C-FAKE", "channel_name": "x"}),
        "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "channel_not_in_workspace"


def test_admin_bot_status_returns_installed_false_when_no_row(monkeypatch):
    """No tenant_bot_connectors row → installed=false response."""
    import json

    class FakeDB:
        def execute(self, sql, params=None):
            class R:
                def fetchone(self_inner): return None
            return R()
    monkeypatch.setattr("connectors.handlers_admin_slack_channels._db",
                        lambda: FakeDB())
    monkeypatch.setattr(
        "connectors.handlers_slack_workspace_bot._require_admin",
        lambda claims: ("t-1", "u-1"))

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/admin/slack/status",
        "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["installed"] is False


def test_admin_bot_status_returns_full_state_when_row_active(monkeypatch):
    """Active row with channel + autonomous toggle → installed=true + state."""
    import json

    class FakeDB:
        def execute(self, sql, params=None):
            class R:
                def fetchone(self_inner): return {
                    "broadcast_channel_id": "C-XYZ",
                    "broadcast_channel_name": "shasta-alerts",
                    "autonomous_rule_enabled": True,
                    "status": "active",
                }
            return R()
    monkeypatch.setattr("connectors.handlers_admin_slack_channels._db",
                        lambda: FakeDB())
    monkeypatch.setattr(
        "connectors.handlers_slack_workspace_bot._require_admin",
        lambda claims: ("t-1", "u-1"))

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/admin/slack/status",
        "requestContext": {"authorizer": {"claims": {"sub": "admin"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["installed"] is True
    assert body["broadcast_channel_id"] == "C-XYZ"
    assert body["autonomous_rule_enabled"] is True
