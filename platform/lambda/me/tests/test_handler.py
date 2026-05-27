"""Tests for /me handler — focused on the new is_admin field."""
from __future__ import annotations

import json
import importlib
import sys
import os

# Make the lambda directory importable (matches sibling-test convention).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def env_setup(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:999999999999:cluster:test")
    monkeypatch.setenv("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:999999999999:secret:test")
    monkeypatch.setenv("DB_NAME",        "ciso_copilot_test")
    monkeypatch.setenv("ADMIN_EMAILS",   "admin@example.com,kk@example.com")


def _make_event(sub: str = "test-sub") -> dict:
    return {
        "requestContext": {
            "authorizer": {
                "claims": {"sub": sub, "email": "user@example.com"},
            },
        },
    }


def _make_db_response(email: str, role: str = "admin") -> dict:
    return {
        "records": [[
            {"stringValue": email},
            {"stringValue": role},
            {"stringValue": "tenant-uuid"},
            {"stringValue": "Test Tenant"},
            {"stringValue": "approved"},
        ]],
    }


def test_is_admin_true_when_email_in_allowlist(env_setup):
    import main as me_main
    importlib.reload(me_main)

    mock_rds = MagicMock()
    mock_rds.execute_statement.return_value = _make_db_response("admin@example.com")

    with patch.object(me_main, "rds_data", mock_rds):
        result = me_main.handler(_make_event(), None)

    body = json.loads(result["body"])
    assert body["user"]["is_admin"] is True


def test_is_admin_false_when_email_not_in_allowlist(env_setup):
    import main as me_main
    importlib.reload(me_main)

    mock_rds = MagicMock()
    mock_rds.execute_statement.return_value = _make_db_response("randomuser@example.com")

    with patch.object(me_main, "rds_data", mock_rds):
        result = me_main.handler(_make_event(), None)

    body = json.loads(result["body"])
    assert body["user"]["is_admin"] is False


def test_is_admin_case_insensitive(env_setup):
    import main as me_main
    importlib.reload(me_main)

    mock_rds = MagicMock()
    mock_rds.execute_statement.return_value = _make_db_response("Admin@Example.com")

    with patch.object(me_main, "rds_data", mock_rds):
        result = me_main.handler(_make_event(), None)

    body = json.loads(result["body"])
    assert body["user"]["is_admin"] is True


def test_is_admin_false_when_admin_emails_empty(env_setup, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "")

    import main as me_main
    importlib.reload(me_main)

    mock_rds = MagicMock()
    mock_rds.execute_statement.return_value = _make_db_response("admin@example.com")

    with patch.object(me_main, "rds_data", mock_rds):
        result = me_main.handler(_make_event(), None)

    body = json.loads(result["body"])
    assert body["user"]["is_admin"] is False
