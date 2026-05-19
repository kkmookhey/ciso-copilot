"""Tests for the stdlib HS256 state-JWT module."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

# Stub Secrets Manager BEFORE importing state_jwt so it can't reach AWS at import time.
@pytest.fixture(autouse=True)
def stub_secrets(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET_ARN", "arn:fake")
    # patch boto3 client used in state_jwt
    import boto3
    class _FakeSm:
        def get_secret_value(self, SecretId): return {"SecretString": "test-signing-key-not-secret"}
    monkeypatch.setattr(boto3, "client", lambda _name, **_kw: _FakeSm())
    # invalidate the in-module cache between tests
    import state_jwt as sj
    sj._signing_key_cache = None
    yield


def test_sign_and_verify_round_trip():
    import state_jwt as sj
    token = sj.sign({"tenant_id": "abc", "user_id": "u1"}, ttl_seconds=300)
    payload = sj.verify(token)
    assert payload["tenant_id"] == "abc"
    assert payload["user_id"] == "u1"
    assert "exp" in payload
    assert "nonce" in payload


def test_verify_rejects_tampered_signature():
    import state_jwt as sj
    token = sj.sign({"tenant_id": "abc"}, ttl_seconds=300)
    # flip the last char of the signature segment
    h, p, s = token.split(".")
    bad_s = s[:-1] + ("A" if s[-1] != "A" else "B")
    with pytest.raises(ValueError, match="signature"):
        sj.verify(f"{h}.{p}.{bad_s}")


def test_verify_rejects_expired_token():
    import state_jwt as sj
    token = sj.sign({"tenant_id": "abc"}, ttl_seconds=-1)  # already expired
    with pytest.raises(ValueError, match="expired"):
        sj.verify(token)


def test_verify_rejects_malformed_token():
    import state_jwt as sj
    with pytest.raises(ValueError):
        sj.verify("not.a.jwt.too.many.parts")
    with pytest.raises(ValueError):
        sj.verify("only-one-part")
