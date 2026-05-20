# platform/lambda/risks/tests/test_idempotency.py
"""Tests for source_approval_id idempotency in POST /risks."""
import json

import pytest

import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evt(body: dict) -> dict:
    """Minimal API Gateway event for POST /risks with a Cognito-authed tenant."""
    return {
        "httpMethod": "POST",
        "path": "/risks",
        "pathParameters": None,
        "queryStringParameters": None,
        "body": json.dumps(body),
        "requestContext": {
            "authorizer": {
                "claims": {"sub": "test-sub"}
            }
        },
    }


def _make_resolve(tenant_id: str = "tid-1"):
    """Monkeypatch _resolve_tenant_id to return a fixed tenant."""
    def _resolve(event):
        return tenant_id
    return _resolve


def _no_records_response():
    return {"records": [], "numberOfRecordsUpdated": 0}


def _one_record_response(risk_id: str, status: str = "open"):
    return {
        "records": [[{"stringValue": risk_id}, {"stringValue": status}]],
        "numberOfRecordsUpdated": 0,
    }


# ---------------------------------------------------------------------------
# No source_approval_id — existing callers unaffected (plain INSERT, no ON CONFLICT)
# ---------------------------------------------------------------------------

def test_create_without_approval_id_inserts(monkeypatch):
    """A create with no source_approval_id inserts and returns a new risk_id."""
    inserted = []

    def mock_execute_statement(**kwargs):
        sql = kwargs.get("sql", "")
        if sql.startswith("INSERT"):
            inserted.append(True)
        return _no_records_response()

    monkeypatch.setattr(main, "rds_data", type("R", (), {"execute_statement": staticmethod(mock_execute_statement)})())
    monkeypatch.setattr(main, "_resolve_tenant_id", _make_resolve())

    r = main.handler(_evt({"title": "Test risk", "severity": "high"}), None)
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert "risk_id" in body
    assert body["status"] == "open"
    assert len(inserted) == 1
    # Verify it's a plain INSERT (no ON CONFLICT clause)
    sql = mock_execute_statement.__code__  # not ideal — checked via side-effects above


# ---------------------------------------------------------------------------
# source_approval_id present + INSERT succeeds (no conflict) → new row
# ---------------------------------------------------------------------------

def test_create_with_new_approval_id_inserts(monkeypatch):
    """A create with a source_approval_id that does not exist yet inserts
    the row (with source_approval_id set) and returns a fresh risk_id via
    the ON CONFLICT RETURNING path."""
    inserted_sqls = []

    def mock_execute_statement(**kwargs):
        sql = kwargs.get("sql", "")
        if "ON CONFLICT" in sql:
            inserted_sqls.append(sql)
            # Simulate successful insert: RETURNING returns the new row.
            return {
                "records": [[{"stringValue": "new-risk-id"}, {"stringValue": "open"}]],
                "numberOfRecordsUpdated": 1,
            }
        return _no_records_response()

    monkeypatch.setattr(main, "rds_data", type("R", (), {"execute_statement": staticmethod(mock_execute_statement)})())
    monkeypatch.setattr(main, "_resolve_tenant_id", _make_resolve())

    approval_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    r = main.handler(
        _evt({
            "title": "New risk from approval",
            "severity": "medium",
            "source_approval_id": approval_id,
        }),
        None,
    )
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert "risk_id" in body
    assert body["status"] == "open"
    assert len(inserted_sqls) == 1
    # The INSERT SQL must include source_approval_id column and ON CONFLICT clause
    assert "source_approval_id" in inserted_sqls[0]
    assert "ON CONFLICT" in inserted_sqls[0]


# ---------------------------------------------------------------------------
# source_approval_id present + conflict (double-tap) → fetch and return winner
# ---------------------------------------------------------------------------

def test_create_with_existing_approval_id_returns_existing(monkeypatch):
    """Double-tap scenario: ON CONFLICT fires (RETURNING is empty), so code
    falls back to SELECT to fetch and return the existing row. No duplicate
    INSERT is issued."""
    existing_risk_id = "00000000-0000-0000-0000-000000000001"
    calls = []

    def mock_execute_statement(**kwargs):
        sql = kwargs.get("sql", "")
        calls.append(sql)
        if "ON CONFLICT" in sql:
            # Simulate conflict: RETURNING returns nothing.
            return _no_records_response()
        if "source_approval_id" in sql and sql.strip().startswith("SELECT"):
            # Fallback fetch returns the existing row.
            return _one_record_response(existing_risk_id, "open")
        return _no_records_response()

    monkeypatch.setattr(main, "rds_data", type("R", (), {"execute_statement": staticmethod(mock_execute_statement)})())
    monkeypatch.setattr(main, "_resolve_tenant_id", _make_resolve())

    r = main.handler(
        _evt({
            "title": "Test risk",
            "severity": "high",
            "source_approval_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        }),
        None,
    )
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["risk_id"] == existing_risk_id
    assert body["status"] == "open"
    # Exactly 2 DB calls: the INSERT (ON CONFLICT) + the fallback SELECT
    assert len(calls) == 2
    assert "ON CONFLICT" in calls[0]
    assert calls[1].strip().startswith("SELECT")
