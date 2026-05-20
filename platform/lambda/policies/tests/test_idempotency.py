# platform/lambda/policies/tests/test_idempotency.py
"""Tests for source_approval_id idempotency in POST /policies."""
import json

import pytest

import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evt(body: dict) -> dict:
    return {
        "httpMethod": "POST",
        "path": "/policies",
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
    def _resolve(event):
        return tenant_id
    return _resolve


def _no_records_response():
    return {"records": [], "numberOfRecordsUpdated": 0}


def _one_record_response(policy_id: str, status: str = "draft"):
    return {
        "records": [[{"stringValue": policy_id}, {"stringValue": status}]],
        "numberOfRecordsUpdated": 0,
    }


# ---------------------------------------------------------------------------
# No source_approval_id — existing callers unaffected (plain INSERT, no ON CONFLICT)
# ---------------------------------------------------------------------------

def test_create_without_approval_id_inserts(monkeypatch):
    """A create with no source_approval_id inserts and returns a new policy_id."""
    inserted = []

    def mock_execute_statement(**kwargs):
        sql = kwargs.get("sql", "")
        if sql.strip().startswith("INSERT"):
            inserted.append(sql)
        return _no_records_response()

    monkeypatch.setattr(main, "rds_data", type("R", (), {"execute_statement": staticmethod(mock_execute_statement)})())
    monkeypatch.setattr(main, "_resolve_tenant_id", _make_resolve())

    r = main.handler(_evt({"template_key": "access_control", "vars": {"company_name": "Acme"}}), None)
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert "policy_id" in body
    assert body["status"] == "draft"
    assert len(inserted) == 1
    # Verify it's a plain INSERT (no ON CONFLICT clause)
    assert "ON CONFLICT" not in inserted[0]


# ---------------------------------------------------------------------------
# source_approval_id present + INSERT succeeds (no conflict) → new row
# ---------------------------------------------------------------------------

def test_create_with_new_approval_id_inserts(monkeypatch):
    """A create with a source_approval_id that does not exist yet inserts
    the row (with source_approval_id set) and returns a fresh policy_id via
    the ON CONFLICT RETURNING path."""
    inserted_sqls = []

    def mock_execute_statement(**kwargs):
        sql = kwargs.get("sql", "")
        if "ON CONFLICT" in sql:
            inserted_sqls.append(sql)
            # Simulate successful insert: RETURNING returns the new row.
            return {
                "records": [[{"stringValue": "new-policy-id"}, {"stringValue": "draft"}]],
                "numberOfRecordsUpdated": 1,
            }
        return _no_records_response()

    monkeypatch.setattr(main, "rds_data", type("R", (), {"execute_statement": staticmethod(mock_execute_statement)})())
    monkeypatch.setattr(main, "_resolve_tenant_id", _make_resolve())

    approval_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    r = main.handler(
        _evt({
            "template_key": "access_control",
            "vars": {"company_name": "Acme"},
            "source_approval_id": approval_id,
        }),
        None,
    )
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert "policy_id" in body
    assert body["status"] == "draft"
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
    existing_policy_id = "11111111-1111-1111-1111-111111111111"
    calls = []

    def mock_execute_statement(**kwargs):
        sql = kwargs.get("sql", "")
        calls.append(sql)
        if "ON CONFLICT" in sql:
            # Simulate conflict: RETURNING returns nothing.
            return _no_records_response()
        if "source_approval_id" in sql and sql.strip().startswith("SELECT"):
            # Fallback fetch returns the existing row.
            return _one_record_response(existing_policy_id, "draft")
        return _no_records_response()

    monkeypatch.setattr(main, "rds_data", type("R", (), {"execute_statement": staticmethod(mock_execute_statement)})())
    monkeypatch.setattr(main, "_resolve_tenant_id", _make_resolve())

    r = main.handler(
        _evt({
            "template_key": "access_control",
            "vars": {"company_name": "Acme"},
            "source_approval_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        }),
        None,
    )
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["policy_id"] == existing_policy_id
    assert body["status"] == "draft"
    # Exactly 2 DB calls: the INSERT (ON CONFLICT) + the fallback SELECT
    assert len(calls) == 2
    assert "ON CONFLICT" in calls[0]
    assert calls[1].strip().startswith("SELECT")
