"""Unit tests for _fire_personal_tier_pushes in main.py.

Mocks rds_data + push_mod so no real AWS calls are made.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock


# main.py reads DB_* env vars at import time; stub them before the import.
for _k, _v in {
    "DB_CLUSTER_ARN":            "arn:aws:rds:us-east-1:123:cluster:test",
    "DB_SECRET_ARN":             "arn:aws:secretsmanager:us-east-1:123:secret:test",
    "DB_NAME":                   "ciso_copilot",
    "ENTRA_SCANNER_SECRET_NAME": "ciso-copilot/entra-scanner-creds",
}.items():
    os.environ.setdefault(_k, _v)

# Force a fresh import of main each test run (avoids cross-test state).
if "main" in sys.modules:
    del sys.modules["main"]

import main  # noqa: E402 — must be after env setup


def _make_rds_row(finding_id: str, title: str, upn: str) -> list[dict]:
    return [
        {"stringValue": finding_id},
        {"stringValue": title},
        {"stringValue": upn},
    ]


def test_fire_personal_tier_pushes_fires_once_per_row(monkeypatch):
    """One push per finding row, using the right payload shape."""

    # Patch the APNS ARN so the early-exit guard doesn't fire.
    monkeypatch.setattr(main, "_APNS_PLATFORM_APP_ARN", "arn:aws:sns:us-east-1:123:app/APNS/test")

    # Fake rds_data.execute_statement returning two finding rows.
    rows = [
        _make_rds_row("fid-aaa", "alice@corp.com signed into ChatGPT", "alice@corp.com"),
        _make_rds_row("fid-bbb", "bob@corp.com signed into ChatGPT",   "bob@corp.com"),
    ]
    fake_rds = MagicMock()
    fake_rds.execute_statement.return_value = {"records": rows}
    monkeypatch.setattr(main, "rds_data", fake_rds)

    # Patch push_mod so no SNS calls happen.
    fake_push = MagicMock()
    fake_push.tokens_for_tenant.return_value = ["device-token-1"]
    monkeypatch.setattr(main, "push_mod", fake_push)

    main._fire_personal_tier_pushes(tenant_id="tid-001", scan_id="sid-001")

    # Two pushes, one per row.
    assert fake_push.send_push_with_payload.call_count == 2

    # Validate payload shape for the first call.
    first_call_kwargs = fake_push.send_push_with_payload.call_args_list[0].kwargs
    assert first_call_kwargs["payload"]["kind_label"] == "Shadow AI"
    assert first_call_kwargs["payload"]["finding_id"] == "fid-aaa"
    assert "alice@corp.com" in first_call_kwargs["payload"]["speakable_summary"]


def test_fire_personal_tier_pushes_no_op_when_no_tokens(monkeypatch):
    """No push_with_payload calls when no device tokens registered."""
    monkeypatch.setattr(main, "_APNS_PLATFORM_APP_ARN", "arn:aws:sns:us-east-1:123:app/APNS/test")

    fake_rds = MagicMock()
    fake_rds.execute_statement.return_value = {
        "records": [_make_rds_row("fid-aaa", "title", "user@corp.com")]
    }
    monkeypatch.setattr(main, "rds_data", fake_rds)

    fake_push = MagicMock()
    fake_push.tokens_for_tenant.return_value = []
    monkeypatch.setattr(main, "push_mod", fake_push)

    main._fire_personal_tier_pushes(tenant_id="tid-001", scan_id="sid-001")

    fake_push.send_push_with_payload.assert_not_called()


def test_fire_personal_tier_pushes_no_op_when_arn_missing(monkeypatch):
    """Returns immediately when APNS_PLATFORM_APP_ARN is empty."""
    monkeypatch.setattr(main, "_APNS_PLATFORM_APP_ARN", "")

    fake_rds = MagicMock()
    monkeypatch.setattr(main, "rds_data", fake_rds)

    fake_push = MagicMock()
    monkeypatch.setattr(main, "push_mod", fake_push)

    main._fire_personal_tier_pushes(tenant_id="tid-001", scan_id="sid-001")

    # rds_data should not even be called.
    fake_rds.execute_statement.assert_not_called()
    fake_push.send_push_with_payload.assert_not_called()
