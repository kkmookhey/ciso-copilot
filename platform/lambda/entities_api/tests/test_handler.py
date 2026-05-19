"""Per-route handler tests for the entities_api Lambda."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN",    "arn:db")
    monkeypatch.setenv("DB_SECRET_ARN",     "arn:secret")
    monkeypatch.setenv("DB_NAME",           "ciso_copilot")
    monkeypatch.setenv("AI_SCAN_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/x/ai-scan-queue")
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
    """Queue of execute_statement responses, popped FIFO. Falls back to
    {'records': []} once the queue drains."""
    import helpers
    queue = list(responses)
    def fake_exec(**kw):
        return queue.pop(0) if queue else {"records": []}
    monkeypatch.setattr(helpers.rds_data, "execute_statement", fake_exec)


def _tenant_row(tenant_id):
    return {"records": [[{"stringValue": tenant_id}]]}


# ----------------------------------------------------------------------------
# Dispatch / 404
# ----------------------------------------------------------------------------

def test_unknown_route_404(monkeypatch):
    _stub_rds(monkeypatch, [])
    import main
    out = main.handler(_evt("GET", "/ai/nonsense"), None)
    assert out["statusCode"] == 404


# ----------------------------------------------------------------------------
# POST /ai/scans
# ----------------------------------------------------------------------------

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
    persisted_repo_id = "44444444-4444-4444-4444-444444444444"
    _stub_rds(monkeypatch, [
        # 1) tenant lookup
        _tenant_row("11111111-1111-1111-1111-111111111111"),
        # 2) installation_id lookup
        {"records": [[{"longValue": 99999}]]},
        # 3) entity upsert RETURNING id::text
        {"records": [[{"stringValue": persisted_repo_id}]]},
        # 4) ai_scans insert (no RETURNING needed)
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
    # Uses the PERSISTED id from the upsert RETURNING, not the client UUID.
    # Regression #2 from spec section 9.3.
    assert queued["repo_asset_id"]   == persisted_repo_id


# ----------------------------------------------------------------------------
# GET /ai/scans
# ----------------------------------------------------------------------------

def test_list_scans(monkeypatch):
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
    assert scans[0]["repo_full_name"] == "kk/foo"


def test_get_scan_404(monkeypatch):
    _stub_rds(monkeypatch, [_tenant_row("t1"), {"records": []}])
    import main
    out = main.handler(_evt("GET", "/ai/scans/abc", path_params={"id": "abc"}), None)
    assert out["statusCode"] == 404


# ----------------------------------------------------------------------------
# GET /entities
# ----------------------------------------------------------------------------

def test_list_entities_pagination(monkeypatch):
    extra_rows = [[
        {"stringValue": f"e{i}"},
        {"stringValue": "ai_framework"},
        {"stringValue": f"langchain@0.1.{i}"},
        {"stringValue": "LangChain"},
        {"stringValue": "ai"},
        {"stringValue": "ai.detectors.framework"},
        {"stringValue": "2026-05-19T00:00:00Z"},
        {"stringValue": "2026-05-19T00:00:00Z"},
        {"stringValue": "{\"source_path\": \"app/agent.py\"}"},
    ] for i in range(51)]
    _stub_rds(monkeypatch, [_tenant_row("t1"), {"records": extra_rows}])
    import main
    out = main.handler(_evt("GET", "/entities",
                              query={"page": "1", "per_page": "50"}), None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert len(body["entities"]) == 50
    assert body["next_page"] == 2
    # source_path is hoisted out of attributes for UI convenience.
    assert body["entities"][0]["source_path"] == "app/agent.py"


def test_list_entities_with_repo_filter(monkeypatch):
    _stub_rds(monkeypatch, [_tenant_row("t1"), {"records": []}])
    import main
    out = main.handler(_evt("GET", "/entities", query={"repo": "r1"}), None)
    assert out["statusCode"] == 200
    assert json.loads(out["body"])["entities"] == []


# ----------------------------------------------------------------------------
# GET /entities/{id}
# ----------------------------------------------------------------------------

def test_get_entity_returns_evidence_packet(monkeypatch):
    _stub_rds(monkeypatch, [
        _tenant_row("t1"),
        {"records": [[
            {"stringValue": "e1"},
            {"stringValue": "ai_framework"},
            {"stringValue": "langchain@0.1.0"},
            {"stringValue": "LangChain"},
            {"stringValue": "ai"},
            {"stringValue": "ai.detectors.framework"},
            {"stringValue": "2026-05-19T00:00:00Z"},
            {"stringValue": "2026-05-19T00:00:00Z"},
            {"stringValue": "{\"imports_seen\": 2}"},
            {"stringValue": "{\"version\":\"0.1\"}"},
            {"stringValue": "c1"},
        ]]},
    ])
    import main
    out = main.handler(_evt("GET", "/entities/e1", path_params={"id": "e1"}), None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["attributes"]      == {"imports_seen": 2}
    assert body["evidence_packet"] == {"version": "0.1"}
    assert body["connection_id"]   == "c1"


def test_get_entity_404(monkeypatch):
    _stub_rds(monkeypatch, [_tenant_row("t1"), {"records": []}])
    import main
    out = main.handler(_evt("GET", "/entities/nope", path_params={"id": "nope"}), None)
    assert out["statusCode"] == 404


# ----------------------------------------------------------------------------
# GET /entities/{id}/graph
# ----------------------------------------------------------------------------

def _node_row(eid, kind, label, attrs="{}"):
    return [
        {"stringValue": eid},
        {"stringValue": kind},
        {"stringValue": label},
        {"stringValue": attrs},
    ]


def _edge_row(edge_id, source, target, kind):
    return [
        {"stringValue": edge_id},
        {"stringValue": source},
        {"stringValue": target},
        {"stringValue": kind},
    ]


def test_entity_graph_returns_cytoscape_shape(monkeypatch):
    _stub_rds(monkeypatch, [
        _tenant_row("t1"),
        # nodes query
        {"records": [
            _node_row("e1", "github_repo",  "kk/foo",  "{\"_stub\": false}"),
            _node_row("e2", "ai_framework", "LangChain"),
            _node_row("e3", "ai_model",     "openai:gpt-4o"),
        ]},
        # edges query
        {"records": [
            _edge_row("ed1", "e1", "e2", "uses"),
            _edge_row("ed2", "e2", "e3", "calls"),
        ]},
    ])
    import main
    out = main.handler(
        _evt("GET", "/entities/e1/graph", path_params={"id": "e1"}),
        None,
    )
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert len(body["nodes"]) == 3
    assert len(body["edges"]) == 2
    # Cytoscape shape: nodes wrap {data: {id, label, type, attributes}}
    n0 = body["nodes"][0]["data"]
    assert n0["id"]    == "e1"
    assert n0["label"] == "kk/foo"
    assert n0["type"]  == "github_repo"
    # Edges wrap {data: {id, source, target, label}}
    e0 = body["edges"][0]["data"]
    assert e0["source"] == "e1" and e0["target"] == "e2" and e0["label"] == "uses"
    assert body["meta"]["root_id"]    == "e1"
    assert body["meta"]["node_count"] == 3
    assert body["meta"]["truncated"]  is False


def test_entity_graph_truncates_at_max_nodes(monkeypatch):
    # max_nodes default is 500; we send 501 rows back from the nodes query.
    nodes = [_node_row(f"n{i}", "ai_framework", f"label{i}") for i in range(501)]
    _stub_rds(monkeypatch, [
        _tenant_row("t1"),
        {"records": nodes},
        {"records": []},   # edges
    ])
    import main
    out = main.handler(
        _evt("GET", "/entities/n0/graph", path_params={"id": "n0"}),
        None,
    )
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert len(body["nodes"])         == 500
    assert body["meta"]["truncated"]  is True
    assert body["meta"]["node_count"] == 500


def test_entity_graph_root_not_found(monkeypatch):
    _stub_rds(monkeypatch, [
        _tenant_row("t1"),
        {"records": []},   # nodes — empty, root doesn't exist
    ])
    import main
    out = main.handler(
        _evt("GET", "/entities/missing/graph", path_params={"id": "missing"}),
        None,
    )
    assert out["statusCode"] == 404


# ----------------------------------------------------------------------------
# GET /entities/{id}/relationships
# ----------------------------------------------------------------------------

def test_entity_relationships_outgoing(monkeypatch):
    _stub_rds(monkeypatch, [
        _tenant_row("t1"),
        {"records": [[
            {"stringValue": "ed1"},
            {"stringValue": "uses"},
            {"stringValue": "outgoing"},
            {"stringValue": "e2"},
            {"stringValue": "ai_framework"},
            {"stringValue": "langchain@0.1.0"},
            {"stringValue": "LangChain"},
        ]]},
    ])
    import main
    out = main.handler(
        _evt("GET", "/entities/e1/relationships",
             query={"direction": "outgoing"},
             path_params={"id": "e1"}),
        None,
    )
    assert out["statusCode"] == 200
    rels = json.loads(out["body"])["relationships"]
    assert len(rels) == 1
    assert rels[0]["kind"]      == "uses"
    assert rels[0]["direction"] == "outgoing"
    assert rels[0]["other_entity"]["id"]           == "e2"
    assert rels[0]["other_entity"]["display_name"] == "LangChain"


def test_entity_relationships_bad_direction(monkeypatch):
    _stub_rds(monkeypatch, [_tenant_row("t1")])
    import main
    out = main.handler(
        _evt("GET", "/entities/e1/relationships",
             query={"direction": "sideways"},
             path_params={"id": "e1"}),
        None,
    )
    assert out["statusCode"] == 400
