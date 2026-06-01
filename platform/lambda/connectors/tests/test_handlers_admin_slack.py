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
