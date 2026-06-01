"""Admin Slack workspace bot OAuth handlers.

Distinct from the per-user Slack OAuth flow in handlers_slack.py:
  - Different scopes (bot scopes: chat:write, channels:read, groups:read)
  - Different token target (tenant_bot_connectors, not user_connectors)
  - Different state JWT audience ("slack-bot-callback")
  - Admin gate: caller must have users.role='admin'

Same Slack app, same SSM credentials.
"""
from __future__ import annotations
import os
import secrets

from connectors.main import _route, _resp, subject_from_claims
from mcp_oauth import pkce
from mcp_oauth import state as state_jwt
from mcp_oauth.providers import slack as slack_provider
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


BOT_SCOPES = ["chat:write", "channels:read", "groups:read"]


@_route("POST", r"^/connectors/connect/slack-workspace-bot$")
def initiate_workspace_bot(event, claims, _params):
    """Admin-gated initiate: returns Slack authorize URL with bot scopes.

    Auth: admin role required. PKCE + signed-state-JWT identical to the
    user OAuth flow, but the state JWT carries provider="slack-bot" so
    its audience pin (slack-bot-callback) prevents replay at the user
    callback and vice versa.
    """
    tenant_id, user_id = _require_admin(claims)
    if not tenant_id or not user_id:
        return _resp(403, {"error": "admin_required"})

    client_id = os.environ["SLACK_CLIENT_ID"]
    redirect_uri = (
        f"{os.environ['CONNECTORS_REDIRECT_BASE']}/callback/slack-workspace-bot"
    )

    verifier, challenge = pkce.generate_pair()
    nonce = secrets.token_urlsafe(16)
    pkce.store_verifier(nonce=nonce, verifier=verifier)

    state = state_jwt.sign_state(
        tenant_id=tenant_id,
        user_id=user_id,
        provider="slack-bot",
        pkce_verifier_hash=pkce.challenge_hash(challenge),
        nonce=nonce,
    )

    # Reuse the user-flow URL builder but override scope/user_scope kwargs —
    # the per-user URL builder defaults to user_scope=USER_SCOPES; we want
    # bot scope only (scope=BOT_SCOPES, user_scope omitted).
    url = slack_provider.build_authorize_url(
        client_id=client_id, redirect_uri=redirect_uri,
        state=state, code_challenge=challenge,
        scope=",".join(BOT_SCOPES),
        user_scope="",
    )
    return _resp(200, {"authorize_url": url})
