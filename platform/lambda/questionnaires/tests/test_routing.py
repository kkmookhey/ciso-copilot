"""Routing tests for the questionnaires handler.

Guards the dispatch order: a bare ``POST /questionnaires`` must NOT shadow the
more specific ``POST /questionnaires/from-excel`` and
``POST /questionnaires/{id}/items/{iid}`` routes (regression — both were
registered + called by the web app but unreachable because the catch-all POST
branch ran first).
"""
from __future__ import annotations

import importlib
import os
import sys

# Make the lambda directory importable (matches sibling-test convention).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def env_setup(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:999999999999:cluster:test")
    monkeypatch.setenv("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:999999999999:secret:test")
    monkeypatch.setenv("DB_NAME",        "ciso_copilot_test")


def _post_event(path: str, path_params: dict | None = None) -> dict:
    return {
        "httpMethod": "POST",
        "path": path,
        "pathParameters": path_params or {},
        "body": "{}",
        "requestContext": {"authorizer": {"claims": {"sub": "test-sub"}}},
    }


def _load_main():
    import main as q_main
    importlib.reload(q_main)
    return q_main


def _patch_tenant(q_main):
    """Make _resolve_tenant_id return a tenant without touching a real DB."""
    mock_rds = MagicMock()
    mock_rds.execute_statement.return_value = {"records": [[{"stringValue": "tenant-uuid"}]]}
    return patch.object(q_main, "rds_data", mock_rds)


def test_post_root_routes_to_create(env_setup):
    q = _load_main()
    with _patch_tenant(q), \
         patch.object(q, "_create", return_value={"statusCode": 200, "body": "{}"}) as create, \
         patch.object(q, "_from_excel") as from_excel, \
         patch.object(q, "_suggest_item") as suggest:
        q.handler(_post_event("/questionnaires"), None)
    create.assert_called_once()
    from_excel.assert_not_called()
    suggest.assert_not_called()


def test_post_from_excel_routes_to_from_excel(env_setup):
    q = _load_main()
    with _patch_tenant(q), \
         patch.object(q, "_create") as create, \
         patch.object(q, "_from_excel", return_value={"statusCode": 200, "body": "{}"}) as from_excel, \
         patch.object(q, "_suggest_item") as suggest:
        q.handler(_post_event("/questionnaires/from-excel"), None)
    from_excel.assert_called_once()
    create.assert_not_called()
    suggest.assert_not_called()


def test_post_item_routes_to_suggest_item(env_setup):
    q = _load_main()
    with _patch_tenant(q), \
         patch.object(q, "_create") as create, \
         patch.object(q, "_from_excel") as from_excel, \
         patch.object(q, "_suggest_item", return_value={"statusCode": 200, "body": "{}"}) as suggest:
        q.handler(
            _post_event("/questionnaires/q-123/items/i-456", {"id": "q-123", "iid": "i-456"}),
            None,
        )
    suggest.assert_called_once()
    create.assert_not_called()
    from_excel.assert_not_called()
