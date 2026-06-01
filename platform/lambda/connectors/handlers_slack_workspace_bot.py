"""Admin Slack workspace bot OAuth handlers.

Distinct from the per-user Slack OAuth flow in handlers_slack.py:
  - Different scopes (bot scopes: chat:write, channels:read, groups:read)
  - Different token target (tenant_bot_connectors, not user_connectors)
  - Different state JWT audience ("slack-bot-callback")
  - Admin gate: caller must have users.role='admin'

Same Slack app, same SSM credentials.
"""
from __future__ import annotations
import base64
import hashlib
import os
import secrets

from connectors.main import _route, _resp, subject_from_claims
from mcp_oauth import pkce
from mcp_oauth import state as state_jwt
from mcp_oauth.crypto import encrypt_token
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


@_route("GET", r"^/connectors/callback/slack-workspace-bot$",
        requires_auth=False)
def callback_workspace_bot(event, claims, _params):
    """Admin-bot OAuth callback.

    Auth: unauthenticated (state JWT is the gate; Slack redirects the
    user's browser here). Provider="slack-bot" pinned at JWT verify
    time prevents replay of a user-flow JWT at this endpoint.
    """
    qs = event.get("queryStringParameters") or {}
    code = qs.get("code")
    state = qs.get("state")
    if not code or not state:
        return _resp(400, {"error": "missing_code_or_state"})

    try:
        s = state_jwt.verify_state(state, expected_provider="slack-bot")
    except Exception as e:
        return _resp(400, {"error": "invalid_state", "detail": str(e)[:120]})

    tenant_id = s["tenant_id"]
    user_id = s["user_id"]
    nonce = s["nonce"]
    pkce_hash = s["pkce_verifier_hash"]

    verifier = pkce.fetch_verifier(nonce)
    if not verifier:
        return _resp(400, {"error": "verifier_expired_or_missing"})

    rebuilt_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    if pkce.challenge_hash(rebuilt_challenge) != pkce_hash:
        return _resp(400, {"error": "pkce_mismatch"})

    client_id = os.environ["SLACK_CLIENT_ID"]
    client_secret = os.environ["SLACK_CLIENT_SECRET"]
    redirect_uri = (
        f"{os.environ['CONNECTORS_REDIRECT_BASE']}/callback/slack-workspace-bot"
    )

    tokens = slack_provider.exchange_code_bot(
        code=code, code_verifier=verifier,
        client_id=client_id, client_secret=client_secret,
        redirect_uri=redirect_uri,
    )

    access_enc, access_dk = encrypt_token(tokens["access_token"])
    scopes_literal = "{" + ",".join(tokens["scopes"]) + "}"

    _db().execute("""
        INSERT INTO tenant_bot_connectors (
            tenant_id, oauth_provider, mcp_server_url, vendor_workspace_id,
            access_token_enc, access_data_key_ct,
            scopes, installed_by_user_id, status
        ) VALUES (
            :tid::uuid, :provider, :mcp, :vw,
            :a, :adk, :scopes::text[], :uid::uuid, 'active'
        )
        ON CONFLICT (tenant_id, oauth_provider) DO UPDATE SET
            access_token_enc   = EXCLUDED.access_token_enc,
            access_data_key_ct = EXCLUDED.access_data_key_ct,
            mcp_server_url     = EXCLUDED.mcp_server_url,
            vendor_workspace_id = EXCLUDED.vendor_workspace_id,
            scopes             = EXCLUDED.scopes,
            status             = 'active',
            revoked_at         = NULL
    """, [
        {"name": "tid",      "value": {"stringValue": tenant_id}},
        {"name": "provider", "value": {"stringValue": "slack"}},
        {"name": "mcp",      "value": {"stringValue": tokens["mcp_server_url"]}},
        {"name": "vw",       "value": {"stringValue": tokens["team_id"]}},
        {"name": "a",        "value": {"blobValue": access_enc}},
        {"name": "adk",      "value": {"blobValue": access_dk}},
        {"name": "scopes",   "value": {"stringValue": scopes_literal}},
        {"name": "uid",      "value": {"stringValue": user_id}},
    ])

    web_base = os.environ["WEB_BASE_URL"]
    return {
        "statusCode": 302,
        "headers": {
            "location": f"{web_base}/settings?tab=connectors&ok=slack-bot",
        },
        "body": "",
    }
