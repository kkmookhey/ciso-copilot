from __future__ import annotations

import jwt
import pytest


def _kw(provider: str = "slack"):
    return dict(
        tenant_id="tenant-1",
        user_id="user-1",
        provider=provider,
        pkce_verifier_hash="hash-abc",
        nonce="nonce-1",
    )


def test_state_round_trips(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    from mcp_oauth.state import sign_state, verify_state

    token = sign_state(**_kw())
    claims = verify_state(token, expected_provider="slack")
    assert claims["tenant_id"] == "tenant-1"
    assert claims["provider"] == "slack"
    assert claims["pkce_verifier_hash"] == "hash-abc"
    assert claims["nonce"] == "nonce-1"
    # iss + aud are baked in and validated on decode.
    assert claims["iss"] == "shasta-connectors"
    assert claims["aud"] == "slack-callback"


def test_state_rejects_expired(monkeypatch):
    """No real sleep — drive `time.time` so PyJWT sees the token as past-exp."""
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    from mcp_oauth import state as st

    fake_time = [1_000_000.0]
    monkeypatch.setattr("mcp_oauth.state.time.time", lambda: fake_time[0])

    token = st.sign_state(ttl_seconds=300, **_kw())
    fake_time[0] += 301  # jump past expiry
    with pytest.raises(jwt.ExpiredSignatureError):
        st.verify_state(token, expected_provider="slack")


def test_state_rejects_tampered(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    from mcp_oauth.state import sign_state, verify_state

    token = sign_state(**_kw())
    head, payload, sig = token.split(".")
    bad = ".".join([head, payload, sig[:-1] + ("A" if sig[-1] != "A" else "B")])
    with pytest.raises(jwt.InvalidSignatureError):
        verify_state(bad, expected_provider="slack")


def test_state_rejects_wrong_audience(monkeypatch):
    """A JWT minted for slack cannot be used at the atlassian callback.
    Prevents cross-provider replay if STATE_JWT_SECRET is ever shared."""
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    from mcp_oauth.state import sign_state, verify_state

    token = sign_state(**_kw(provider="slack"))
    with pytest.raises(jwt.InvalidAudienceError):
        verify_state(token, expected_provider="atlassian")


def test_state_rejects_slack_user_jwt_at_slack_bot_callback(monkeypatch):
    """A JWT minted for the user OAuth flow (provider="slack") MUST NOT
    decode at the admin bot callback (expected_provider="slack-bot").

    Same protection as the cross-provider audience test, applied to the
    user-vs-admin variant of the same provider. Without this gate, a
    leaked user-flow state JWT could be replayed at the admin bot
    callback (or vice versa)."""
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    from mcp_oauth.state import sign_state, verify_state

    token = sign_state(**_kw(provider="slack"))
    with pytest.raises(jwt.InvalidAudienceError):
        verify_state(token, expected_provider="slack-bot")
