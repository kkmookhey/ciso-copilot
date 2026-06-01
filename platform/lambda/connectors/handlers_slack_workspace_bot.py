"""Admin Slack workspace bot OAuth handlers.

Distinct from the per-user Slack OAuth flow in handlers_slack.py:
  - Different scopes (bot scopes: chat:write, channels:read, groups:read)
  - Different token target (tenant_bot_connectors, not user_connectors)
  - Different state JWT audience ("slack-bot-callback")
  - Admin gate: caller must have users.role='admin'

Same Slack app, same SSM credentials.
"""
from __future__ import annotations
from connectors.main import subject_from_claims
from mcp_oauth.session import _db


def _require_admin(claims: dict) -> tuple[str | None, str | None]:
    """Resolve (tenant_id, user_id) only if the caller is a tenant admin.

    Mirrors handlers_slack._resolve_user_context but adds AND role='admin'
    to the WHERE clause. Returns (None, None) on:
      - no extractable sso_subject
      - no users row matching the subject
      - user exists but role != 'admin'
    """
    subject = subject_from_claims(claims)
    if not subject:
        return None, None
    row = _db().execute(
        "SELECT tenant_id, user_id FROM users "
        "WHERE sso_subject = :sub AND role = 'admin' LIMIT 1",
        [{"name": "sub", "value": {"stringValue": subject}}],
    ).fetchone()
    if not row:
        return None, None
    return str(row["tenant_id"]), str(row["user_id"])
