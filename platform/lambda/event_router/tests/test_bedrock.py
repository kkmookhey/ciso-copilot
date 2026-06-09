"""Bedrock InvokeModel handler — per-call entity upserts + detectors + daily rollup.

Tasks 1.3.2 / 1.3.3 / 1.3.4 of the AI Security Slice 1 plan.
"""
from __future__ import annotations

import json
import os
import sys

# Stub env vars required by main.py's module-level reads before importing main.
os.environ.setdefault("DB_CLUSTER_ARN",    "arn:aws:rds:us-east-1:000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",     "arn:aws:secretsmanager:us-east-1:000:secret:test")
os.environ.setdefault("DB_NAME",           "test")
os.environ.setdefault("RAW_EVENTS_BUCKET", "test-bucket")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock
import pytest
import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bedrock_invoke_event(
    account_id: str = "111111111111",
    region: str = "us-east-1",
    model_id: str = "anthropic.claude-3-opus-20240229-v1:0",
    principal_arn: str = "arn:aws:iam::111111111111:role/PlatformTeam",
    event_name: str = "InvokeModel",
) -> dict:
    return {
        "detail-type": "AWS API Call via CloudTrail",
        "source":      "aws.bedrock-runtime",
        "account":     account_id,
        "detail": {
            "eventName":          event_name,
            "eventTime":          "2026-06-05T10:00:00Z",
            "awsRegion":          region,
            "recipientAccountId": account_id,
            "userIdentity":       {"arn": principal_arn, "type": "AssumedRole"},
            "requestParameters":  {"modelId": model_id},
            "sourceIPAddress":    "10.0.0.42",
        },
    }


def _non_bedrock_cloudtrail_event() -> dict:
    return {
        "detail-type": "AWS API Call via CloudTrail",
        "source":      "aws.ec2",
        "account":     "999999999999",
        "detail": {
            "eventName":          "AuthorizeSecurityGroupIngress",
            "eventTime":          "2026-06-05T10:00:00Z",
            "awsRegion":          "us-east-1",
            "recipientAccountId": "999999999999",
            "userIdentity":       {"arn": "arn:aws:iam::999999999999:user/x"},
            "requestParameters":  {"groupId": "sg-abc"},
        },
    }


@pytest.fixture(autouse=True)
def reset_main():
    """Ensure main is freshly imported for each test to avoid mock bleed-through."""
    import importlib
    # Only reload if it was previously imported; otherwise just ensure it's loaded
    if "main" in sys.modules:
        importlib.reload(main)
    yield


@pytest.fixture
def mock_rds(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(main, "rds_data", fake)
    return fake


# ---------------------------------------------------------------------------
# Task 1.3.2 — predicate tests
# ---------------------------------------------------------------------------

def test_is_bedrock_event_recognizes_InvokeModel():
    assert main._is_bedrock_event(_bedrock_invoke_event(event_name="InvokeModel")) is True


def test_synthetic_scan_id_is_deterministic_per_tenant_conn():
    """Same (tenant, conn) → same scan_id. Different inputs → different scan_id."""
    a = main._bedrock_synthetic_scan_id("t-1", "c-1")
    b = main._bedrock_synthetic_scan_id("t-1", "c-1")
    c = main._bedrock_synthetic_scan_id("t-1", "c-2")
    d = main._bedrock_synthetic_scan_id("t-2", "c-1")
    assert a == b
    assert a != c
    assert a != d
    # Must be a real UUID (parses)
    import uuid as _u
    _u.UUID(a)


def test_ensure_bedrock_runtime_scan_issues_insert_on_conflict(mock_rds):
    """The synthetic-scan helper INSERTs into scans with ON CONFLICT DO NOTHING."""
    mock_rds.execute_statement.return_value = {"records": []}
    sid = main._ensure_bedrock_runtime_scan("t-1", "c-1")
    assert sid == main._bedrock_synthetic_scan_id("t-1", "c-1")
    call = mock_rds.execute_statement.call_args
    sql = call.kwargs["sql"]
    assert "INSERT INTO scans" in sql
    assert "ON CONFLICT (scan_id) DO NOTHING" in sql
    assert "'runtime'" in sql  # trigger column value


def test_is_bedrock_event_recognizes_all_names():
    for name in ("InvokeModelWithResponseStream", "Converse", "ConverseStream",
                 "InvokeAgent", "Retrieve", "RetrieveAndGenerate"):
        assert main._is_bedrock_event(_bedrock_invoke_event(event_name=name)) is True, name


def test_is_bedrock_event_ignores_non_bedrock_cloudtrail():
    """AuthorizeSecurityGroupIngress must NOT be dispatched to Bedrock branch."""
    assert main._is_bedrock_event(_non_bedrock_cloudtrail_event()) is False


def test_is_bedrock_event_ignores_config_change():
    evt = {"detail-type": "Configuration Item Change Notification",
           "detail": {"eventName": "InvokeModel"}}
    assert main._is_bedrock_event(evt) is False


# ---------------------------------------------------------------------------
# Task 1.3.2 — entity upserts
# ---------------------------------------------------------------------------

def _entity_only_side_effects(include_finding: bool = True) -> list:
    """Return the standard mock call sequence for _handle_bedrock with no allowed list.

    Call order in _handle_bedrock:
      1. _find_connection_by_account
      2. _upsert_bedrock_entity (bedrock_model)
      3. _upsert_invocation_rollup
      4. _upsert_bedrock_entity (iam_principal)
      5. _upsert_bedrock_edge
      6. _bedrock_allowed_principals (evidence_packet lookup by conn_id)
      For each emitted finding:
        N.  _ensure_bedrock_runtime_scan (INSERT ON CONFLICT DO NOTHING — no rows)
        N+1. _emit_bedrock_finding (INSERT/UPSERT — returns finding_id)
    """
    effects = [
        {"records": [[{"stringValue": "c-1"}, {"stringValue": "t-1"}]]},  # conn lookup
        {"records": [[{"stringValue": "e-1"}]]},  # bedrock_model upsert
        {"records": [[{"stringValue": "e-2"}]]},  # bedrock_invocation rollup
        {"records": [[{"stringValue": "e-3"}]]},  # iam_principal upsert
        {"records": []},                           # edge upsert
        {"records": [[{"stringValue": "{}"}]]},    # evidence_packet (no allowed list)
    ]
    if include_finding:
        effects.append({"records": []})                              # synthetic scan ON CONFLICT
        effects.append({"records": [[{"stringValue": "f-1"}]]})      # model_inventory finding
    return effects


def test_handle_bedrock_upserts_bedrock_model_entity(mock_rds):
    """The second RDS call should INSERT into entities with kind=bedrock_model."""
    mock_rds.execute_statement.side_effect = _entity_only_side_effects()
    resp = main.handler(_bedrock_invoke_event(), None)
    assert resp.get("ok") is True or resp.get("status") == "ok"

    calls = mock_rds.execute_statement.call_args_list
    entity_calls = [c for c in calls if "INSERT INTO entities" in (c.kwargs.get("sql") or "")]
    assert len(entity_calls) >= 1
    # First entity insert must be bedrock_model
    first_entity_sql = entity_calls[0].kwargs["sql"]
    assert "bedrock_model" in first_entity_sql or ":kind" in first_entity_sql


def test_handle_bedrock_upserts_invocation_rollup(mock_rds):
    """A bedrock_invocation entity must be upserted for the rollup counter."""
    mock_rds.execute_statement.side_effect = _entity_only_side_effects()
    main.handler(_bedrock_invoke_event(), None)
    calls = mock_rds.execute_statement.call_args_list
    # Find the rollup upsert — must contain the invocation_count counter SQL
    sqls = [c.kwargs.get("sql", "") for c in calls]
    rollup_sql = next((s for s in sqls if "invocation_count" in s or "bedrock_invocation" in s), None)
    assert rollup_sql is not None, f"No bedrock_invocation rollup upsert found. SQLs: {sqls}"


def test_handle_bedrock_returns_no_tenant_when_account_unknown(mock_rds):
    """No matching cloud_connection → short-circuit with status no_tenant."""
    mock_rds.execute_statement.return_value = {"records": []}
    resp = main.handler(_bedrock_invoke_event(), None)
    assert resp.get("ok") is False or "no_tenant" in str(resp)


# ---------------------------------------------------------------------------
# Task 1.3.3 — per-event detectors
# ---------------------------------------------------------------------------

def test_unsanctioned_principal_emits_finding(mock_rds):
    """Principal NOT in allowed-list → aws_bedrock_invoke_unsanctioned finding.

    Call order in _handle_bedrock:
      1. conn lookup (returns conn_id + tenant_id)
      2. bedrock_model upsert
      3. invocation rollup upsert
      4. iam_principal upsert
      5. edge upsert
      6. evidence_packet lookup (_bedrock_allowed_principals — uses conn_id)
      7. unsanctioned finding INSERT (principal not in allowed list)
      8. model_inventory finding INSERT (always)
    """
    allowed_ep = json.dumps({"bedrock_allowed_principals":
                             ["arn:aws:iam::111111111111:role/AllowedRole"]})
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "c-1"}, {"stringValue": "t-1"}]]},  # conn lookup
        {"records": [[{"stringValue": "e-1"}]]},                           # bedrock_model
        {"records": [[{"stringValue": "e-2"}]]},                           # invocation rollup
        {"records": [[{"stringValue": "e-3"}]]},                           # iam_principal
        {"records": []},                                                    # edge
        {"records": [[{"stringValue": allowed_ep}]]},                      # evidence_packet
        {"records": []},                                                    # synthetic scan (unsanctioned)
        {"records": [[{"stringValue": "f-unsanctioned"}]]},                # unsanctioned finding
        {"records": []},                                                    # synthetic scan (inventory)
        {"records": [[{"stringValue": "f-inventory"}]]},                   # model_inventory finding
    ]
    main.handler(_bedrock_invoke_event(), None)
    calls = mock_rds.execute_statement.call_args_list
    finding_calls = [c for c in calls if "INSERT INTO findings" in (c.kwargs.get("sql") or "")]
    assert len(finding_calls) >= 1
    params = {p["name"]: p["value"] for p in finding_calls[0].kwargs["parameters"]}
    assert params["check_id"]["stringValue"] == "aws_bedrock_invoke_unsanctioned"


def test_unsanctioned_principal_no_finding_when_in_allowed_list(mock_rds):
    """Principal IS in allowed-list → no unsanctioned finding, but model_inventory still fires."""
    allowed_ep = json.dumps({"bedrock_allowed_principals":
                             ["arn:aws:iam::111111111111:role/PlatformTeam"]})
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "c-1"}, {"stringValue": "t-1"}]]},  # conn lookup
        {"records": [[{"stringValue": "e-1"}]]},                           # bedrock_model
        {"records": [[{"stringValue": "e-2"}]]},                           # invocation rollup
        {"records": [[{"stringValue": "e-3"}]]},                           # iam_principal
        {"records": []},                                                    # edge
        {"records": [[{"stringValue": allowed_ep}]]},                      # evidence_packet
        {"records": []},                                                    # synthetic scan (inventory)
        {"records": [[{"stringValue": "f-inventory"}]]},                   # model_inventory finding
    ]
    main.handler(_bedrock_invoke_event(), None)
    calls = mock_rds.execute_statement.call_args_list
    finding_calls = [c for c in calls if "INSERT INTO findings" in (c.kwargs.get("sql") or "")]
    unsanctioned = [c for c in finding_calls
                    if any(p.get("value", {}).get("stringValue") == "aws_bedrock_invoke_unsanctioned"
                           for p in c.kwargs.get("parameters", []))]
    assert len(unsanctioned) == 0


def test_no_allowed_list_means_no_unsanctioned_finding(mock_rds):
    """If evidence_packet has no bedrock_allowed_principals key, never emit the unsanctioned finding."""
    mock_rds.execute_statement.side_effect = _entity_only_side_effects()
    main.handler(_bedrock_invoke_event(), None)
    calls = mock_rds.execute_statement.call_args_list
    finding_calls = [c for c in calls if "INSERT INTO findings" in (c.kwargs.get("sql") or "")]
    unsanctioned = [c for c in finding_calls
                    if any(p.get("value", {}).get("stringValue") == "aws_bedrock_invoke_unsanctioned"
                           for p in c.kwargs.get("parameters", []))]
    assert len(unsanctioned) == 0


def test_first_sighting_emits_model_inventory_finding(mock_rds):
    """model_inventory finding is always emitted (idempotent via ON CONFLICT DO NOTHING)."""
    mock_rds.execute_statement.side_effect = _entity_only_side_effects()
    main.handler(_bedrock_invoke_event(), None)
    calls = mock_rds.execute_statement.call_args_list
    finding_calls = [c for c in calls if "INSERT INTO findings" in (c.kwargs.get("sql") or "")]
    inventory = [c for c in finding_calls
                 if any(p.get("value", {}).get("stringValue") == "aws_bedrock_model_inventory"
                        for p in c.kwargs.get("parameters", []))]
    assert len(inventory) >= 1


# ---------------------------------------------------------------------------
# Task 1.3.4 — daily rollup
# ---------------------------------------------------------------------------

def test_daily_rollup_emits_high_volume_for_over_threshold(mock_rds):
    """Daily rollup with count > 10_000 → aws_bedrock_invoke_high_volume finding.

    Call order in _handle_bedrock_daily_rollup per row:
      1. rollup query (returns tenant rows)
      2. _bedrock_high_volume_threshold (threshold lookup per tenant)
      3. _conn_id_for_tenant (only if count > threshold)
      4. _emit_bedrock_finding (only if count > threshold)
    """
    mock_rds.execute_statement.side_effect = [
        # 1. Rollup query returns one row above threshold
        {"records": [[
            {"stringValue": "t-1"},
            {"stringValue": "arn:aws:iam::111:role/Heavy"},
            {"stringValue": "anthropic.claude-3-opus"},
            {"longValue": 15000},
            {"stringValue": "us-east-1"},
        ]]},
        # 2. Threshold lookup (no override → uses default 10_000)
        {"records": []},
        # 3. conn_id lookup for the tenant
        {"records": [[{"stringValue": "c-1"}]]},
        # 4. Synthetic scan ensure (ON CONFLICT DO NOTHING — no rows)
        {"records": []},
        # 5. Finding upsert
        {"records": [[{"stringValue": "f-1"}]]},
    ]
    resp = main.handler({"detail-type": "shasta.scheduled.bedrock_daily_rollup"}, None)
    assert resp.get("emitted") == 1


def test_daily_rollup_no_finding_when_under_threshold(mock_rds):
    """Daily rollup with count <= threshold → no finding emitted."""
    mock_rds.execute_statement.side_effect = [
        # 1. Rollup query returns one row under threshold
        {"records": [[
            {"stringValue": "t-1"},
            {"stringValue": "arn:aws:iam::111:role/Light"},
            {"stringValue": "anthropic.claude-3-haiku"},
            {"longValue": 500},
            {"stringValue": "us-east-1"},
        ]]},
        # 2. Threshold lookup (no override → default 10_000)
        {"records": []},
        # (count 500 <= 10_000, so conn_id + finding NOT called)
    ]
    resp = main.handler({"detail-type": "shasta.scheduled.bedrock_daily_rollup"}, None)
    assert resp.get("emitted") == 0


def test_daily_rollup_does_not_emit_when_no_rollup_rows(mock_rds):
    """If no bedrock_invocation rows for yesterday → emitted=0."""
    mock_rds.execute_statement.return_value = {"records": []}
    resp = main.handler({"detail-type": "shasta.scheduled.bedrock_daily_rollup"}, None)
    assert resp.get("emitted") == 0
