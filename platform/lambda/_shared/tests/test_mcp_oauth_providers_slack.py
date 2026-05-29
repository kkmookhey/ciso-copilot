from __future__ import annotations
from unittest.mock import patch, MagicMock


def test_build_authorize_url():
    from mcp_oauth.providers.slack import build_authorize_url

    url = build_authorize_url(
        client_id="abc123",
        redirect_uri="https://app.shasta.io/v1/connectors/callback/slack",
        state="state-token",
        code_challenge="challenge-string",
    )
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=abc123" in url
    assert "state=state-token" in url
    assert "code_challenge=challenge-string" in url
    assert "code_challenge_method=S256" in url
    assert "user_scope=" in url  # per-user scopes, not bot scopes


def test_exchange_code_for_token(monkeypatch):
    from mcp_oauth.providers import slack as s

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "ok": True,
        "access_token": "xoxp-real-token",
        "refresh_token": "xoxe-1-...",
        "expires_in": 43200,
        "scope": "chat:write,im:write,search:read",
        "authed_user": {"id": "U0123"},
        "team": {"id": "T0123"},
    }
    fake_response.raise_for_status = MagicMock()
    monkeypatch.setattr(s.requests, "post", lambda *a, **kw: fake_response)

    result = s.exchange_code(code="auth-code", code_verifier="verifier",
                             client_id="cid", client_secret="csec",
                             redirect_uri="https://x/callback")
    assert result["access_token"] == "xoxp-real-token"
    assert result["refresh_token"] == "xoxe-1-..."
    assert result["vendor_user_id"] == "U0123"
    assert result["vendor_workspace_id"] == "T0123"
    assert result["expires_in"] == 43200
    assert "chat:write" in result["scopes"]
