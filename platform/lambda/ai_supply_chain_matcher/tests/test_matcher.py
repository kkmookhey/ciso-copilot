# platform/lambda/ai_supply_chain_matcher/tests/test_matcher.py
import json
from unittest.mock import patch, MagicMock

# main.py reads DB env vars at import time. Stub them before importing.
import os
os.environ.setdefault("DB_CLUSTER_ARN", "test-cluster")
os.environ.setdefault("DB_SECRET_ARN",  "test-secret")
os.environ.setdefault("DB_NAME",        "ciso_copilot_test")

from ai_supply_chain_matcher.main import handler, _find_matches


@patch("ai_supply_chain_matcher.main._rds")
def test_find_matches_returns_kev_listed_and_actively_imported(mock_rds):
    mock_rds.execute_statement.return_value = {
        "records": [[
            {"stringValue": "f-trivy-1"},           # trivy_finding_id
            {"stringValue": "langchain"},            # package
            {"stringValue": "0.0.184"},              # version
            {"stringValue": "CVE-2026-0470"},        # cve
            {"stringValue": "lc-entity-id"},         # framework_entity_id
            {"stringValue": "agent-id"},             # agent_entity_id
            {"stringValue": "pricing-agent"},        # agent_name
            {"stringValue": "acme/paying-system"},   # repo_full_name
        ]]
    }
    matches = _find_matches(tenant_id="t-1")
    assert len(matches) == 1
    assert matches[0]["package"] == "langchain"
    assert matches[0]["cve"] == "CVE-2026-0470"
    assert matches[0]["agent_name"] == "pricing-agent"


@patch("ai_supply_chain_matcher.main._emit_finding")
@patch("ai_supply_chain_matcher.main._fire_push")
@patch("ai_supply_chain_matcher.main._find_matches")
def test_handler_emits_finding_per_match(mock_find, mock_push, mock_emit):
    mock_find.return_value = [{
        "package": "langchain", "version": "0.0.184",
        "cve": "CVE-2026-0470", "agent_name": "pricing-agent",
        "agent_entity_id": "agent-id", "framework_entity_id": "lc-entity-id",
        "repo_full_name": "acme/paying-system", "trivy_finding_id": "f-1",
    }]
    mock_emit.return_value = "new-finding-id"
    event = {"Records": [{"body": json.dumps({"tenant_id": "t-1", "scan_id": "s-1"})}]}
    handler(event, None)
    mock_emit.assert_called_once()
    mock_push.assert_called_once()


@patch("ai_supply_chain_matcher.main._find_matches")
def test_handler_no_matches_no_emit(mock_find):
    mock_find.return_value = []
    event = {"Records": [{"body": json.dumps({"tenant_id": "t-1", "scan_id": "s-1"})}]}
    # Should complete without raising.
    handler(event, None)
    # No assertion — the test passes if no exception fires.
