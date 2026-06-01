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
