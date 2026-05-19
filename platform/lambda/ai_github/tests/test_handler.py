"""Per-route tests for the ai_github Lambda handler."""
from __future__ import annotations

import json
import uuid
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


def test_complete_inserts_row_and_returns_connection_id(monkeypatch):
    import main, helpers, state_jwt, github_app
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    monkeypatch.setattr(state_jwt, "verify",
                        lambda token: {"tenant_id": "tenant-1", "user_sub": "u1"})

    # GitHub /app/installations/{id} response — stub the http client
    def fake_get(url, headers):
        assert url.endswith("/app/installations/99999")
        return 200, {"account": {"login": "kkmookhey", "type": "User"}}, {}
    monkeypatch.setattr(github_app, "_http_get", fake_get)
    monkeypatch.setattr(github_app, "mint_app_jwt", lambda: "stub.jwt")

    inserts: list[dict] = []
    def fake_execute(**kw):
        inserts.append(kw)
        return {"records": []}
    # rds_data is the boto3 client mock from the env fixture
    helpers.rds_data.execute_statement = fake_execute

    event = _event_authed("tenant-1", path="/v1/ai/connections/github/complete",
                          body={"installation_id": 99999, "state": "stub.state"})
    out = main.handler(event, None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    uuid.UUID(body["connection_id"])  # is a valid UUID
    # one INSERT happened
    assert any("INSERT INTO ai_connections" in c["sql"] for c in inserts)


def test_complete_rejects_state_for_other_tenant(monkeypatch):
    import main, helpers, state_jwt
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    monkeypatch.setattr(state_jwt, "verify",
                        lambda token: {"tenant_id": "tenant-OTHER", "user_sub": "u1"})

    event = _event_authed("tenant-1", path="/v1/ai/connections/github/complete",
                          body={"installation_id": 99999, "state": "stub.state"})
    out = main.handler(event, None)
    assert out["statusCode"] == 403


def test_complete_rejects_expired_state(monkeypatch):
    import main, helpers, state_jwt
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    def boom(_t): raise ValueError("token expired")
    monkeypatch.setattr(state_jwt, "verify", boom)

    event = _event_authed("tenant-1", path="/v1/ai/connections/github/complete",
                          body={"installation_id": 99999, "state": "stub.state"})
    out = main.handler(event, None)
    assert out["statusCode"] == 400


def test_list_connections_returns_tenant_rows(monkeypatch):
    import main, helpers
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")

    def fake_execute(**kw):
        assert ":tid" in kw["sql"]
        return {"records": [[
            {"stringValue": "11111111-1111-1111-1111-111111111111"},
            {"stringValue": "github"},
            {"stringValue": "active"},
            {"stringValue": "kkmookhey"},
            {"stringValue": "2026-05-18T10:00:00Z"},
        ]]}
    helpers.rds_data.execute_statement = fake_execute

    event = _event_authed("tenant-1", method="GET", path="/v1/ai/connections")
    out = main.handler(event, None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["connections"][0]["provider"] == "github"
    assert body["connections"][0]["github_org_name"] == "kkmookhey"
