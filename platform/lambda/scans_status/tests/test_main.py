"""Tests for scans_status/main.py.

The finding_count field must come from `scans.stats.findings` (the
authoritative count the scanner wrote at completion), NOT from
`SELECT count(*) FROM findings WHERE scan_id=X`. The latter undercounts
historical scans because unified_writer's ON CONFLICT clause reassigns
findings.scan_id to the most-recent emitting scan.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

# stub env vars before main is imported (module-level boto3 client init).
os.environ.setdefault("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("DB_NAME",        "test")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _claims_event(scan_id: str = "11111111-1111-1111-1111-111111111111") -> dict:
    return {
        "requestContext": {"authorizer": {"claims": {"sub": "sub-1"}}},
        "pathParameters":  {"scan_id": scan_id},
    }


def _scan_row(stats_json: str | None) -> list:
    """SELECT tier, status, phase, scope, stats, started_at::text, finished_at::text"""
    return [
        {"stringValue": "quick"},
        {"stringValue": "completed"},
        {"stringValue": "done"},
        {"isNull": True},                              # scope
        {"isNull": True} if stats_json is None else {"stringValue": stats_json},
        {"stringValue": "2026-05-21T00:00:00"},
        {"stringValue": "2026-05-21T00:05:00"},
    ]


def test_finding_count_comes_from_stats_when_present():
    """Historical-scan demo case: stats.findings=116, live count=0 (rows
    migrated to a later scan via ON CONFLICT). Endpoint must return 116."""
    import main

    mock_rds = MagicMock()
    mock_rds.execute_statement.side_effect = [
        {"records": [_scan_row(stats_json='{"findings": 116, "entities": 30}')]},
    ]
    with patch("main.rds_data", mock_rds), \
         patch("main._resolve_tenant_id", return_value="tenant-1"):
        resp = main.handler(_claims_event(), None)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["finding_count"] == 116
    # And no second query against findings — stats was authoritative.
    assert mock_rds.execute_statement.call_count == 1


def test_finding_count_falls_back_to_live_count_when_stats_missing():
    """Running-scan case: stats not yet written. Must fall back to
    count(*) FROM findings — which is accurate for the in-flight scan
    (no later scan has reassigned its rows yet)."""
    import main

    mock_rds = MagicMock()
    mock_rds.execute_statement.side_effect = [
        {"records": [_scan_row(stats_json=None)]},
        {"records": [[{"longValue": 7}]]},  # live findings count
    ]
    with patch("main.rds_data", mock_rds), \
         patch("main._resolve_tenant_id", return_value="tenant-1"):
        resp = main.handler(_claims_event(), None)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["finding_count"] == 7
    assert mock_rds.execute_statement.call_count == 2


def test_finding_count_falls_back_when_stats_has_no_findings_key():
    """Edge case: stats is set but lacks the `findings` key (e.g., a scan
    that errored before update_scan stats={...} fired)."""
    import main

    mock_rds = MagicMock()
    mock_rds.execute_statement.side_effect = [
        {"records": [_scan_row(stats_json='{"phase_hint": "first_signal"}')]},
        {"records": [[{"longValue": 0}]]},
    ]
    with patch("main.rds_data", mock_rds), \
         patch("main._resolve_tenant_id", return_value="tenant-1"):
        resp = main.handler(_claims_event(), None)

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["finding_count"] == 0
    assert mock_rds.execute_statement.call_count == 2


def test_404_when_scan_not_found():
    import main

    mock_rds = MagicMock()
    mock_rds.execute_statement.side_effect = [{"records": []}]
    with patch("main.rds_data", mock_rds), \
         patch("main._resolve_tenant_id", return_value="tenant-1"):
        resp = main.handler(_claims_event(), None)

    assert resp["statusCode"] == 404
