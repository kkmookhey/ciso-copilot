"""Slack OAuth handlers — registered with the dispatcher."""
from __future__ import annotations
import datetime as dt
import os
import urllib.parse
from connectors.main import _route, _resp, subject_from_claims
from mcp_oauth import pkce
from mcp_oauth import state as state_jwt
from mcp_oauth.crypto import encrypt_token
from mcp_oauth.providers import slack as slack_provider
from mcp_oauth.session import _db


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


def _read_cookie(event: dict, name: str) -> str | None:
    """Read a single cookie value from API Gateway v2 event headers.
    Header key is lower-cased; value is `k1=v1; k2=v2`."""
    headers = event.get("headers") or {}
    raw = headers.get("cookie") or headers.get("Cookie") or ""
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


@_route("GET", r"^/v1/connectors/callback/slack$")
def callback_slack(event, claims, _params):
    import hashlib as _hashlib, base64 as _b64
    qs = event.get("queryStringParameters") or {}
    code = qs.get("code")
    state = qs.get("state")
    if not code or not state:
        return _resp(400, {"error": "missing_code_or_state"})

    try:
        s = state_jwt.verify_state(state)
    except Exception as e:
        return _resp(400, {"error": "invalid_state", "detail": str(e)[:120]})

    tenant_id = s["tenant_id"]
    user_id = s["user_id"]
    nonce = s["nonce"]
    pkce_hash = s["pkce_verifier_hash"]

    # CSRF binding — spec §6. Read cookie set at initiate, compare hash to
    # state.csrf_token_hash. Defeats forged-state callback attacks where the
    # attacker mints their own state JWT and tricks the victim into hitting
    # the callback URL.
    csrf_token = _read_cookie(event, "shasta_oauth_csrf")
    if not csrf_token:
        return _resp(400, {"error": "csrf_missing"})
    if _hashlib.sha256(csrf_token.encode()).hexdigest() != s.get("csrf_token_hash"):
        return _resp(400, {"error": "csrf_mismatch"})

    verifier = pkce.fetch_verifier(nonce)
    if not verifier:
        return _resp(400, {"error": "verifier_expired_or_missing"})

    # Defense in depth: rebuild the challenge from the verifier and verify the
    # signed hash matches.
    rebuilt_challenge = _b64.urlsafe_b64encode(
        _hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    if pkce.challenge_hash(rebuilt_challenge) != pkce_hash:
        return _resp(400, {"error": "pkce_mismatch"})

    client_id = os.environ["SLACK_CLIENT_ID"]
    client_secret = os.environ["SLACK_CLIENT_SECRET"]
    redirect_uri = f"{os.environ['CONNECTORS_REDIRECT_BASE']}/callback/slack"

    tokens = slack_provider.exchange_code(
        code=code, code_verifier=verifier,
        client_id=client_id, client_secret=client_secret,
        redirect_uri=redirect_uri,
    )

    access_enc = encrypt_token(tokens["access_token"])
    refresh_enc = encrypt_token(tokens["refresh_token"])
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(seconds=int(tokens["expires_in"]))

    db = _db()
    # Upsert: if a revoked/error row exists, overwrite it.
    db.execute("""
        INSERT INTO user_connectors (
            tenant_id, user_id, oauth_provider, mcp_server_url,
            vendor_user_id, vendor_workspace_id,
            access_token_enc, refresh_token_enc, access_expires_at,
            scopes, status
        ) VALUES (
            :tid, :uid, :provider, :mcp,
            :vu, :vw,
            :a, :r, :e,
            :scopes, 'active'
        )
        ON CONFLICT (tenant_id, user_id, oauth_provider) DO UPDATE SET
            access_token_enc = EXCLUDED.access_token_enc,
            refresh_token_enc = EXCLUDED.refresh_token_enc,
            access_expires_at = EXCLUDED.access_expires_at,
            scopes = EXCLUDED.scopes,
            status = 'active',
            last_error = NULL,
            revoked_at = NULL
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "uid", "value": {"stringValue": user_id}},
        {"name": "provider", "value": {"stringValue": "slack"}},
        {"name": "mcp", "value": {"stringValue": tokens["mcp_server_url"]}},
        {"name": "vu", "value": {"stringValue": tokens["vendor_user_id"]}},
        {"name": "vw", "value": {"stringValue": tokens["vendor_workspace_id"]}},
        {"name": "a", "value": {"blobValue": access_enc}},
        {"name": "r", "value": {"blobValue": refresh_enc}},
        {"name": "e", "value": {"stringValue": expires_at.isoformat()}},
        # Aurora Data API: TEXT[] columns require arrayValue payloads,
        # not stringValue with PG array-literal syntax. Latter fails with
        # "expression of type text vs column of type text[]".
        {"name": "scopes",
         "value": {"arrayValue": {"stringValues": list(tokens["scopes"])}}},
    ])

    web_base = os.environ["WEB_BASE_URL"]
    return {
        "statusCode": 302,
        "headers": {
            "location": f"{web_base}/settings?tab=connectors&ok=slack",
            # Clear the CSRF cookie post-callback.
            "set-cookie": "shasta_oauth_csrf=; HttpOnly; Secure; "
                          "SameSite=Lax; Path=/v1/connectors; Max-Age=0",
        },
        "body": "",
    }
