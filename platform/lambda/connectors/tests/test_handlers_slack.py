from __future__ import annotations
import json
from unittest.mock import patch


def test_initiate_returns_authorize_url(monkeypatch):
    """Initiate POST returns the Slack authorize URL with a signed state JWT.

    The CSRF double-submit cookie was removed (dead code in this cross-
    origin deployment); the signed state JWT IS the CSRF defense. See
    state.py module docstring.
    """
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

    # No Set-Cookie should be emitted — the CSRF cookie pattern was retired.
    headers = resp.get("headers") or {}
    assert "set-cookie" not in {k.lower() for k in headers}

    # State JWT must carry iss + aud pinned to slack.
    import urllib.parse, jwt
    state_tok = body["authorize_url"].split("state=")[1].split("&")[0]
    state_tok = urllib.parse.unquote(state_tok)
    claims = jwt.decode(state_tok, options={"verify_signature": False})
    assert claims["iss"] == "shasta-connectors"
    assert claims["aud"] == "slack-callback"
    assert claims["user_id"] == "u-uuid"


def test_callback_inserts_user_connector(monkeypatch):
    monkeypatch.setenv("SLACK_CLIENT_ID", "abc")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "xyz")
    monkeypatch.setenv("CONNECTORS_REDIRECT_BASE", "https://app.shasta.io/v1/connectors")
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    monkeypatch.setenv("WEB_BASE_URL", "https://app.shasta.io")

    from mcp_oauth import state as st, pkce
    from connectors import handlers_slack as h

    challenge = "ch-1"
    state_tok = st.sign_state(
        tenant_id="t-uuid", user_id="u-uuid", provider="slack",
        pkce_verifier_hash=pkce.challenge_hash(challenge),
        nonce="n-1",
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
    # KMS-envelope shape: encrypt_token returns (fernet_ct, data_key_ct).
    monkeypatch.setattr(h, "encrypt_token",
                         lambda t: (f"E:{t}".encode(), f"DK:{t}".encode()))

    # PKCE rebuild check: bypass by making challenge_hash deterministic.
    expected_pkce_hash = pkce.challenge_hash(challenge)
    monkeypatch.setattr(h.pkce, "challenge_hash", lambda c: expected_pkce_hash)

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/callback/slack",
        "queryStringParameters": {"code": "ac-1", "state": state_tok},
        "requestContext": {"authorizer": {"claims": {"sub": "subject-1"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 302
    assert resp["headers"]["location"].endswith("/settings?tab=connectors&ok=slack")
    # No Set-Cookie clear-header — the CSRF cookie was retired.
    assert "set-cookie" not in {k.lower() for k in resp["headers"]}
    assert "INSERT INTO user_connectors" in inserted["sql"]


def test_callback_rejects_state_minted_for_another_provider(monkeypatch):
    """State JWT minted with provider=atlassian must be rejected at the
    slack callback (aud claim mismatch). Replaces the old CSRF cookie
    test — same protection family, stronger guarantee."""
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    from mcp_oauth import state as st, pkce

    # Sign a state for atlassian and try to submit it at slack's callback.
    state_tok = st.sign_state(
        tenant_id="t", user_id="u", provider="atlassian",
        pkce_verifier_hash=pkce.challenge_hash("ch"),
        nonce="n-1",
    )
    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/callback/slack",
        "queryStringParameters": {"code": "ac-1", "state": state_tok},
        "requestContext": {"authorizer": {"claims": {"sub": "s"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "invalid_state"
