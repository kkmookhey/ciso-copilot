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
                  method: str = "POST", path: str = "/ai/connections/github/install_url",
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
    persisted_id = "11111111-1111-1111-1111-111111111111"
    def fake_execute(**kw):
        inserts.append(kw)
        return {"records": [[{"stringValue": persisted_id}]]}
    # rds_data is the boto3 client mock from the env fixture
    helpers.rds_data.execute_statement = fake_execute

    event = _event_authed("tenant-1", path="/ai/connections/github/complete",
                          body={"installation_id": 99999, "state": "stub.state"})
    out = main.handler(event, None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    uuid.UUID(body["connection_id"])  # is a valid UUID
    # one INSERT happened
    assert any("INSERT INTO ai_connections" in c["sql"] for c in inserts)


def test_complete_returns_persisted_id_on_conflict(monkeypatch):
    """Regression: when ON CONFLICT fires (re-install of the same GitHub App),
    the handler must return the EXISTING row's id from the RETURNING clause,
    not the freshly-generated UUID. Returning the local UUID was the cause of
    the /ai/connections/<id>/repos 404 caught on 2026-05-27."""
    import main, helpers, state_jwt, github_app
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    monkeypatch.setattr(state_jwt, "verify",
                        lambda token: {"tenant_id": "tenant-1", "user_sub": "u1"})
    monkeypatch.setattr(github_app, "_http_get",
                        lambda url, headers: (200, {"account": {"login": "kkmookhey", "type": "User"}}, {}))
    monkeypatch.setattr(github_app, "mint_app_jwt", lambda: "stub.jwt")

    # Simulate ON CONFLICT — RETURNING gives an EXISTING row id (from a prior install),
    # NOT the freshly-generated UUID the handler creates locally.
    existing_id = "ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb"
    helpers.rds_data.execute_statement = lambda **kw: {"records": [[{"stringValue": existing_id}]]}

    event = _event_authed("tenant-1", path="/ai/connections/github/complete",
                          body={"installation_id": 99999, "state": "stub.state"})
    out = main.handler(event, None)
    assert out["statusCode"] == 200
    assert json.loads(out["body"])["connection_id"] == existing_id


def test_complete_rejects_state_for_other_tenant(monkeypatch):
    import main, helpers, state_jwt
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    monkeypatch.setattr(state_jwt, "verify",
                        lambda token: {"tenant_id": "tenant-OTHER", "user_sub": "u1"})

    event = _event_authed("tenant-1", path="/ai/connections/github/complete",
                          body={"installation_id": 99999, "state": "stub.state"})
    out = main.handler(event, None)
    assert out["statusCode"] == 403


def test_complete_rejects_expired_state(monkeypatch):
    import main, helpers, state_jwt
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    def boom(_t): raise ValueError("token expired")
    monkeypatch.setattr(state_jwt, "verify", boom)

    event = _event_authed("tenant-1", path="/ai/connections/github/complete",
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

    event = _event_authed("tenant-1", method="GET", path="/ai/connections")
    out = main.handler(event, None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["connections"][0]["provider"] == "github"
    assert body["connections"][0]["github_org_name"] == "kkmookhey"


def test_repos_returns_paginated_list(monkeypatch):
    import main, helpers, github_app
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")

    # tenant ownership lookup: returns the installation_id
    def fake_execute(**kw):
        assert "SELECT github_installation_id" in kw["sql"]
        return {"records": [[{"longValue": 99999}]]}
    helpers.rds_data.execute_statement = fake_execute

    monkeypatch.setattr(github_app, "list_authorized_repos",
                        lambda installation_id, page, per_page: {
                            "repos": [{"full_name": "kk/foo", "default_branch": "main",
                                       "last_pushed_at": "2026-05-18T10:00:00Z", "size_kb": 1,
                                       "primary_language": "Python", "is_private": True}],
                            "next_page": None, "total_count": 1,
                        })
    event = _event_authed("tenant-1", method="GET",
                          path="/ai/connections/cid-1/repos",
                          path_params={"id": "11111111-1111-1111-1111-111111111111"})
    out = main.handler(event, None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["repos"][0]["full_name"] == "kk/foo"
    assert body["next_page"] is None


def test_repos_404_when_connection_not_owned_by_tenant(monkeypatch):
    import main, helpers
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    helpers.rds_data.execute_statement = lambda **kw: {"records": []}  # no rows == not found
    event = _event_authed("tenant-1", method="GET",
                          path="/ai/connections/cid-1/repos",
                          path_params={"id": "11111111-1111-1111-1111-111111111111"})
    out = main.handler(event, None)
    assert out["statusCode"] == 404


def test_delete_connection_flips_status_and_revokes_token(monkeypatch):
    import main, helpers, github_app
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")

    updates: list[dict] = []
    def fake_execute(**kw):
        updates.append(kw)
        if "SELECT github_installation_id" in kw["sql"]:
            return {"records": [[{"longValue": 99999}]]}
        return {"records": []}
    helpers.rds_data.execute_statement = fake_execute

    revoked: list[int] = []
    monkeypatch.setattr(github_app, "revoke_installation_token",
                        lambda iid: revoked.append(iid))

    event = _event_authed("tenant-1", method="DELETE",
                          path="/ai/connections/cid-1",
                          path_params={"id": "11111111-1111-1111-1111-111111111111"})
    out = main.handler(event, None)
    assert out["statusCode"] == 204
    assert revoked == [99999]
    # at least one UPDATE happened
    assert any("UPDATE ai_connections" in u["sql"] for u in updates)
