"""Tests for findings_list/main.py."""
from __future__ import annotations

import json
import sys
import os

# Make the lambda directory importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock


def _claims_event(sub: str = "sub-1", qp: dict | None = None) -> dict:
    return {
        "requestContext": {"authorizer": {"claims": {"sub": sub}}},
        "queryStringParameters": qp or {},
    }


def _stmt(rows: list) -> dict:
    return {"records": rows}


def _finding_row(
    finding_id: str = "f-1",
    check_id: str = "chk-1",
    title: str = "Test check",
    severity: str = "low",
    status: str = "pass",
    frameworks_json: str = '{"nist_ai_rmf": ["GV-1"]}',
) -> list:
    return [
        {"stringValue": finding_id},
        {"stringValue": check_id},
        {"stringValue": title},
        {"isNull": True},          # description
        {"stringValue": severity},
        {"stringValue": status},
        {"isNull": True},          # resource_arn
        {"isNull": True},          # resource_type
        {"isNull": True},          # region
        {"stringValue": "ai"},     # domain
        {"stringValue": frameworks_json},
        {"isNull": True},          # remediation
        {"stringValue": "2026-01-01T00:00:00"},
        {"stringValue": "2026-01-02T00:00:00"},
    ]


def test_handler_returns_401_with_no_subject():
    import main
    resp = main.handler({"requestContext": {}}, None)
    assert resp["statusCode"] == 401


def test_invalid_framework_key_returns_400():
    """Regex guard rejects keys that could be SQL injection vectors."""
    import main

    with patch("main._resolve_tenant_id", return_value="tenant-1"):
        resp = main.handler(
            _claims_event(qp={"framework": "'; DROP TABLE findings;--"}),
            None,
        )
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "invalid_framework_key"


def test_framework_filter_added_to_sql_and_params():
    """When framework param is valid, the JSONB ? clause reaches execute_statement."""
    import main

    mock_rds = MagicMock()
    # call 1: tenant lookup; call 2: main query; call 3: count query
    mock_rds.execute_statement.side_effect = [
        _stmt([[{"stringValue": "tenant-1"}]]),
        _stmt([_finding_row()]),
        _stmt([[{"longValue": 1}]]),
    ]

    with patch("main.rds_data", mock_rds):
        resp = main.handler(
            _claims_event(qp={"framework": "nist_ai_rmf", "status": "pass", "limit": "200"}),
            None,
        )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["count"] == 1
    assert body["findings"][0]["frameworks"] == {"nist_ai_rmf": ["GV-1"]}

    # Verify the JSONB ? operator appeared in the main query SQL.
    main_call_sql = mock_rds.execute_statement.call_args_list[1][1]["sql"]
    assert "f.frameworks ? :framework" in main_call_sql

    # Verify the framework param was sent with the main query.
    main_call_params = mock_rds.execute_statement.call_args_list[1][1]["parameters"]
    fw_param = next((p for p in main_call_params if p["name"] == "framework"), None)
    assert fw_param is not None
    assert fw_param["value"]["stringValue"] == "nist_ai_rmf"

    # Verify the JSONB ? operator appeared in the count query SQL.
    count_call_sql = mock_rds.execute_statement.call_args_list[2][1]["sql"]
    assert "f.frameworks ? :framework" in count_call_sql

    # Verify framework param present in count query (limit/offset stripped, framework kept).
    count_call_params = mock_rds.execute_statement.call_args_list[2][1]["parameters"]
    fw_count_param = next((p for p in count_call_params if p["name"] == "framework"), None)
    assert fw_count_param is not None
    assert "limit" not in [p["name"] for p in count_call_params]
    assert "offset" not in [p["name"] for p in count_call_params]


def test_no_framework_param_omits_jsonb_clause():
    """Without framework param, the ? clause must NOT appear (backward compat)."""
    import main

    mock_rds = MagicMock()
    mock_rds.execute_statement.side_effect = [
        _stmt([[{"stringValue": "tenant-1"}]]),
        _stmt([]),
        _stmt([[{"longValue": 0}]]),
    ]

    with patch("main.rds_data", mock_rds):
        resp = main.handler(_claims_event(qp={"status": "fail"}), None)

    assert resp["statusCode"] == 200
    main_call_sql = mock_rds.execute_statement.call_args_list[1][1]["sql"]
    assert "? :framework" not in main_call_sql
