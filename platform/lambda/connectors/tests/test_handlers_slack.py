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
         patch("connectors.handlers_slack._resolve_user_id", return_value="u-uuid"):
        from connectors import main as m
        ev = {
            "httpMethod": "POST",
            "rawPath": "/v1/connectors/connect/slack",
            "requestContext": {"authorizer": {"claims": {
                "sub": "subject-1", "custom:tenant_id": "t-uuid"
            }}},
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
