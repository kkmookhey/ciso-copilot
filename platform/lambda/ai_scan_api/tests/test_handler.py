"""Per-route handler tests for the ai_scan_api Lambda."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN",     "arn:db")
    monkeypatch.setenv("DB_SECRET_ARN",      "arn:secret")
    monkeypatch.setenv("DB_NAME",            "ciso_copilot")
    monkeypatch.setenv("AI_SCAN_QUEUE_URL",  "https://sqs.us-east-1.amazonaws.com/x/ai-scan-queue")
    import boto3
    monkeypatch.setattr(boto3, "client", lambda _n, **_kw: MagicMock())


def _evt(method, path, body=None, query=None, path_params=None):
    return {
        "httpMethod": method,
        "path": path,
        "body": json.dumps(body) if body else None,
        "queryStringParameters": query,
        "pathParameters": path_params,
        "requestContext": {
            "authorizer": {
                "claims": {"sub": "google-oauth2|123", "identities": []},
            },
        },
    }


def _stub_rds(monkeypatch, responses):
    import helpers
    queue = list(responses)
    def fake_exec(**kw):
        return queue.pop(0) if queue else {"records": []}
    monkeypatch.setattr(helpers.rds_data, "execute_statement", fake_exec)


def _tenant_row(tenant_id):
    return {"records": [[{"stringValue": tenant_id}]]}


def test_unknown_route_404(monkeypatch):
    _stub_rds(monkeypatch, [])
    import main
    out = main.handler(_evt("GET", "/ai/nonsense"), None)
    assert out["statusCode"] == 404


def test_start_scan_requires_auth(monkeypatch):
    _stub_rds(monkeypatch, [{"records": []}])
    import main
    out = main.handler(_evt("POST", "/ai/scans",
                              body={"connection_id": "c", "repo_full_name": "kk/foo"}),
                       None)
    assert out["statusCode"] == 401


def test_start_scan_missing_fields(monkeypatch):
    _stub_rds(monkeypatch, [_tenant_row("t1")])
    import main
    out = main.handler(_evt("POST", "/ai/scans", body={}), None)
    assert out["statusCode"] == 400


def test_start_scan_connection_not_found(monkeypatch):
    _stub_rds(monkeypatch, [_tenant_row("t1"), {"records": []}])
    import main
    out = main.handler(_evt("POST", "/ai/scans",
                              body={"connection_id": "c", "repo_full_name": "kk/foo"}),
                       None)
    assert out["statusCode"] == 404


def test_start_scan_happy_path(monkeypatch):
    _stub_rds(monkeypatch, [
        _tenant_row("11111111-1111-1111-1111-111111111111"),
        {"records": [[{"longValue": 99999}]]},
        {"records": []},
        {"records": []},
        {"records": []},
    ])
    import main
    sent = {}
    def fake_send(**kw):
        sent.update(kw)
        return {"MessageId": "m"}
    monkeypatch.setattr(main._sqs, "send_message", fake_send)

    out = main.handler(_evt("POST", "/ai/scans", body={
        "connection_id":  "33333333-3333-3333-3333-333333333333",
        "repo_full_name": "kk/foo",
        "default_branch": "main",
    }), None)
    assert out["statusCode"] == 202
    payload = json.loads(out["body"])
    assert "scan_id" in payload
    queued = json.loads(sent["MessageBody"])
    assert queued["repo_full_name"]  == "kk/foo"
    assert queued["installation_id"] == 99999


def test_list_scans_filters(monkeypatch):
    _stub_rds(monkeypatch, [
        _tenant_row("t1"),
        {"records": [[
            {"stringValue": "s1"}, {"stringValue": "kk/foo"}, {"stringValue": "success"},
            {"stringValue": "2026-05-19T00:00:00Z"}, {"stringValue": "2026-05-19T00:01:00Z"},
            {"stringValue": ""},
            {"longValue": 12}, {"longValue": 4}, {"longValue": 2},
        ]]},
    ])
    import main
    out = main.handler(_evt("GET", "/ai/scans", query={"connection_id": "c1"}), None)
    assert out["statusCode"] == 200
    scans = json.loads(out["body"])["scans"]
    assert len(scans) == 1
    assert scans[0]["status"] == "success"


def test_get_scan_404(monkeypatch):
    _stub_rds(monkeypatch, [_tenant_row("t1"), {"records": []}])
    import main
    out = main.handler(_evt("GET", "/ai/scans/abc", path_params={"id": "abc"}), None)
    assert out["statusCode"] == 404


def test_list_assets_pagination(monkeypatch):
    extra_rows = [[
        {"stringValue": f"a{i}"}, {"stringValue": "framework"}, {"stringValue": f"n{i}"},
        {"stringValue": "repo1"}, {"stringValue": "kk/foo"},
        {"stringValue": f"p{i}.py"}, {"stringValue": "ai.detectors.framework"},
        {"stringValue": "2026-05-19T00:00:00Z"}, {"stringValue": "2026-05-19T00:00:00Z"},
    ] for i in range(51)]
    _stub_rds(monkeypatch, [_tenant_row("t1"), {"records": extra_rows}])
    import main
    out = main.handler(_evt("GET", "/ai/assets", query={"page": "1", "per_page": "50"}), None)
    body = json.loads(out["body"])
    assert len(body["assets"]) == 50
    assert body["next_page"] == 2


def test_get_asset_returns_evidence_packet(monkeypatch):
    _stub_rds(monkeypatch, [
        _tenant_row("t1"),
        {"records": [[
            {"stringValue": "a1"}, {"stringValue": "framework"}, {"stringValue": "langchain"},
            {"stringValue": "repo1"}, {"stringValue": "kk/foo"},
            {"stringValue": "app/agent.py"}, {"stringValue": "ai.detectors.framework"},
            {"stringValue": "2026-05-19T00:00:00Z"}, {"stringValue": "2026-05-19T00:00:00Z"},
            {"stringValue": "{\"imports_seen\": 2}"},
            {"stringValue": "{\"version\":\"0.1\"}"},
            {"stringValue": "c1"},
        ]]},
    ])
    import main
    out = main.handler(_evt("GET", "/ai/assets/a1", path_params={"id": "a1"}), None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["attributes"] == {"imports_seen": 2}
    assert body["evidence_packet"]["version"] == "0.1"
