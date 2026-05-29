"""Slack OAuth handlers — registered with the dispatcher."""
from __future__ import annotations
import os
from connectors.main import _route, _resp, subject_from_claims
from mcp_oauth import pkce
from mcp_oauth import state as state_jwt
from mcp_oauth.providers import slack as slack_provider


def _resolve_user_id(subject: str, tenant_id: str) -> str:
    from mcp_oauth.session import _db
    row = _db().execute("""
        SELECT user_id FROM users
        WHERE tenant_id = :tid AND sso_subject = :sub
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "sub", "value": {"stringValue": subject}},
    ]).fetchone()
    if not row:
        raise RuntimeError(f"no users row for subject={subject}")
    return str(row["user_id"])


@_route("POST", r"^/v1/connectors/connect/slack$")
def initiate_slack(event, claims, _params):
    """Initiate Slack OAuth.

    Generates: PKCE verifier+challenge, CSRF random token, fresh nonce.
    Stores: PKCE verifier in DDB keyed by nonce.
    Sets: shasta_oauth_csrf cookie (HttpOnly, Secure, SameSite=Lax) so the
          callback can prove the redirecting browser is the one that
          started the flow (spec §6 CSRF binding).
    Returns: { authorize_url } for the web client to redirect to.
    """
    import hashlib
    import secrets
    subject = subject_from_claims(claims)
    tenant_id = claims.get("custom:tenant_id")
    if not tenant_id:
        return _resp(400, {"error": "missing_tenant_id"})
    user_id = _resolve_user_id(subject, tenant_id)

    client_id = os.environ["SLACK_CLIENT_ID"]
    redirect_uri = f"{os.environ['CONNECTORS_REDIRECT_BASE']}/callback/slack"

    verifier, challenge = pkce.generate_pair()
    nonce = secrets.token_urlsafe(16)
    csrf_token = secrets.token_urlsafe(32)
    csrf_token_hash = hashlib.sha256(csrf_token.encode()).hexdigest()

    pkce.store_verifier(nonce=nonce, verifier=verifier)

    state = state_jwt.sign_state(
        tenant_id=tenant_id,
        user_id=user_id,
        provider="slack",
        pkce_verifier_hash=pkce.challenge_hash(challenge),
        csrf_token_hash=csrf_token_hash,
        nonce=nonce,
    )

    url = slack_provider.build_authorize_url(
        client_id=client_id, redirect_uri=redirect_uri,
        state=state, code_challenge=challenge,
    )

    cookie = (
        f"shasta_oauth_csrf={csrf_token}; HttpOnly; Secure; "
        f"SameSite=Lax; Path=/v1/connectors; Max-Age=600"
    )
    return _resp(200, {"authorize_url": url}, headers={"set-cookie": cookie})
