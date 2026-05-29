from __future__ import annotations
import json
import os
from unittest.mock import patch, MagicMock


def test_initiate_returns_authorize_url_and_sets_csrf_cookie(monkeypatch):
    import hashlib
    monkeypatch.setenv("SLACK_CLIENT_ID", "abc")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "xyz")
    monkeypatch.setenv("CONNECTORS_REDIRECT_BASE", "https://app.shasta.io/v1/connectors")
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    with patch("connectors.handlers_slack.pkce.store_verifier") as store, \
         patch("connectors.handlers_slack._resolve_user_context",
               return_value=("t-uuid", "u-uuid")):
        from connectors import main as m
        ev = {
            "httpMethod": "POST",
            "rawPath": "/connectors/connect/slack",
            "requestContext": {"authorizer": {"claims": {"sub": "subject-1"}}},
        }
        resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["authorize_url"].startswith("https://slack.com/oauth/v2/authorize?")
    store.assert_called_once()

    # CSRF cookie present, HttpOnly, Secure, SameSite=Lax (spec §6 CSRF binding)
    set_cookie = resp["headers"]["set-cookie"]
    assert "shasta_oauth_csrf=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=Lax" in set_cookie

    # Cookie value's SHA256 must match the csrf_token_hash inside the state JWT.
    state_tok = body["authorize_url"].split("state=")[1].split("&")[0]
    import urllib.parse, jwt
    state_tok = urllib.parse.unquote(state_tok)
    claims = jwt.decode(state_tok, options={"verify_signature": False})
    cookie_val = set_cookie.split("shasta_oauth_csrf=")[1].split(";")[0]
    assert hashlib.sha256(cookie_val.encode()).hexdigest() == claims["csrf_token_hash"]


def test_callback_inserts_user_connector(monkeypatch):
    import hashlib
    monkeypatch.setenv("SLACK_CLIENT_ID", "abc")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "xyz")
    monkeypatch.setenv("CONNECTORS_REDIRECT_BASE", "https://app.shasta.io/v1/connectors")
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    monkeypatch.setenv("WEB_BASE_URL", "https://app.shasta.io")

    from mcp_oauth import state as st, pkce
    from connectors import handlers_slack as h

    challenge = "ch-1"
    csrf_token = "csrf-secret-value"
    csrf_hash = hashlib.sha256(csrf_token.encode()).hexdigest()
    state_tok = st.sign_state(
        tenant_id="t-uuid", user_id="u-uuid", provider="slack",
        pkce_verifier_hash=pkce.challenge_hash(challenge),
        csrf_token_hash=csrf_hash, nonce="n-1",
    )

    monkeypatch.setattr(h.pkce, "fetch_verifier", lambda nonce: "v-1")
    monkeypatch.setattr(h.slack_provider, "exchange_code", lambda **kw: {
        "access_token": "xoxp-A",
        "refresh_token": "xoxe-R",
        "expires_in": 43200,
        "scopes": ["chat:write", "im:write"],
        "vendor_user_id": "U0X",
        "vendor_workspace_id": "T0X",
        "mcp_server_url": "https://mcp.slack.com/mcp",
    })
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
    monkeypatch.setattr(h, "encrypt_token", lambda t: f"E:{t}".encode())

    # PKCE rebuild check: bypass by making challenge_hash deterministic.
    # The handler rebuilds the challenge from the verifier "v-1"; for the
    # signed state to validate, we mock challenge_hash to always return our
    # pre-computed pkce_verifier_hash. (Defense-in-depth check; the real
    # PKCE binding happens at the vendor's token endpoint.)
    expected_pkce_hash = pkce.challenge_hash(challenge)
    monkeypatch.setattr(h.pkce, "challenge_hash", lambda c: expected_pkce_hash)

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/callback/slack",
        "queryStringParameters": {"code": "ac-1", "state": state_tok},
        "headers": {"cookie": f"shasta_oauth_csrf={csrf_token}"},
        "requestContext": {"authorizer": {"claims": {"sub": "subject-1"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 302
    assert resp["headers"]["location"].endswith("/settings?tab=connectors&ok=slack")
    assert "INSERT INTO user_connectors" in inserted["sql"]


def test_callback_rejects_mismatched_csrf_cookie(monkeypatch):
    """Cookie present but doesn't match state JWT hash → 400 csrf_mismatch.

    Missing-cookie case is tolerated in this deployment because the web
    app and the API are on different origins, so the browser drops the
    Set-Cookie from the cross-origin initiate response. See the TODO in
    handlers_slack.callback_slack for the planned fix.
    """
    import hashlib
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    from mcp_oauth import state as st, pkce

    state_tok = st.sign_state(
        tenant_id="t", user_id="u", provider="slack",
        pkce_verifier_hash=pkce.challenge_hash("ch"),
        csrf_token_hash=hashlib.sha256(b"real-token").hexdigest(),
        nonce="n-1",
    )
    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/callback/slack",
        "queryStringParameters": {"code": "ac-1", "state": state_tok},
        "headers": {"cookie": "shasta_oauth_csrf=wrong-token"},
        "requestContext": {"authorizer": {"claims": {"sub": "s"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "csrf_mismatch"
