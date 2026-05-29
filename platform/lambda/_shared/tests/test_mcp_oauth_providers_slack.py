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


def test_exchange_code_user_scope_response(monkeypatch):
    """Slack OAuth v2 with USER scopes returns tokens nested under
    authed_user (NOT at the top level). This is the shape Shasta gets
    because the app registers user_scope only."""
    from mcp_oauth.providers import slack as s

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "ok": True,
        "app_id": "A123",
        "authed_user": {
            "id": "U0123",
            "scope": "chat:write,im:write,search:read",
            "access_token": "xoxp-real-token",
            "refresh_token": "xoxe-1-...",
            "expires_in": 43200,
            "token_type": "user",
        },
        "team": {"id": "T0123", "name": "Acme"},
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


def test_exchange_code_bot_scope_response_fallback(monkeypatch):
    """If/when bot scopes are also registered, top-level access_token
    is the fallback path. Keeps the provider robust to future config."""
    from mcp_oauth.providers import slack as s

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "ok": True,
        "access_token": "xoxb-bot-token",
        "refresh_token": "xoxe-bot-refresh",
        "expires_in": 43200,
        "scope": "chat:write,channels:read",
        "authed_user": {"id": "U0123"},
        "team": {"id": "T0123"},
    }
    fake_response.raise_for_status = MagicMock()
    monkeypatch.setattr(s.requests, "post", lambda *a, **kw: fake_response)

    result = s.exchange_code(code="ac", code_verifier="v",
                             client_id="c", client_secret="s",
                             redirect_uri="https://x/callback")
    assert result["access_token"] == "xoxb-bot-token"
    assert result["vendor_user_id"] == "U0123"
    assert result["vendor_workspace_id"] == "T0123"


def test_exchange_code_raises_when_no_access_token(monkeypatch):
    """Defensive: if neither shape provides access_token, raise a clear
    error pointing at the most likely cause."""
    import pytest
    from mcp_oauth.providers import slack as s

    fake_response = MagicMock()
    fake_response.json.return_value = {"ok": True, "team": {"id": "T0"}}
    fake_response.raise_for_status = MagicMock()
    monkeypatch.setattr(s.requests, "post", lambda *a, **kw: fake_response)

    with pytest.raises(RuntimeError, match="USER token scopes"):
        s.exchange_code(code="x", code_verifier="x",
                        client_id="x", client_secret="x",
                        redirect_uri="x")
