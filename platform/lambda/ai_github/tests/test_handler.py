"""Per-route tests for the ai_github Lambda handler."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:db")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:secret")
    monkeypatch.setenv("DB_NAME", "ciso_copilot")
    monkeypatch.setenv("STATE_JWT_SECRET_ARN", "arn:state")
    monkeypatch.setenv("GITHUB_APP_SECRET_ARN", "arn:gh")
    monkeypatch.setenv("GITHUB_APP_SLUG", "ciso-copilot")
    monkeypatch.setenv("WEB_CALLBACK_URL", "https://app.settlingforless.com/ai/install/callback")
    # Mock boto3 for state_jwt before it's imported
    import boto3
    class _FakeSm:
        def get_secret_value(self, SecretId): return {"SecretString": "test-signing-key-not-secret"}
    monkeypatch.setattr(boto3, "client", lambda _name, **_kw: _FakeSm())


def _event_authed(tenant_id: str, sub: str = "user-sub-1",
                  method: str = "POST", path: str = "/v1/ai/connections/github/install_url",
                  body: dict | None = None, path_params: dict | None = None,
                  query: dict | None = None) -> dict:
    return {
        "httpMethod": method,
        "path":       path,
        "body":       json.dumps(body or {}),
        "pathParameters":  path_params or {},
        "queryStringParameters": query or {},
        "requestContext": {"authorizer": {"claims": {"sub": sub}}},
    }


def test_install_url_returns_signed_github_url(monkeypatch):
    import main, helpers, state_jwt
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    monkeypatch.setattr(state_jwt, "sign", lambda payload, ttl_seconds: "stub.state.jwt")

    out = main.handler(_event_authed("tenant-1"), None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["install_url"].startswith(
        "https://github.com/apps/ciso-copilot/installations/new"
    )
    assert "state=stub.state.jwt" in body["install_url"]


def test_install_url_401_when_no_tenant(monkeypatch):
    import main, helpers
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: None)

    out = main.handler(_event_authed("tenant-x"), None)
    assert out["statusCode"] == 401
