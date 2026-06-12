"""ai_bom_export — CycloneDX-ML 1.6 AI-BOM endpoint.

Auth: existing JWT path (tenant from claims.identities[0].userId or claims.sub
→ users.tenant_id). Format: ?format=cyclonedx (only Slice 1 value).
Unknown format → 400.
"""
from unittest.mock import MagicMock
import json
import pytest


@pytest.fixture(autouse=True)
def reset_main(monkeypatch):
    """Re-import main fresh for each test to avoid mock bleed-through."""
    import importlib
    import sys
    sys.modules.pop("main", None)
    yield


@pytest.fixture
def mock_rds(monkeypatch):
    fake = MagicMock()
    import main
    monkeypatch.setattr(main, "rds_data", fake)
    return fake


def _event_with_tenant(sub="subject-1", fmt="cyclonedx"):
    return {
        "queryStringParameters": {"format": fmt},
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": sub,
                    "identities": json.dumps([{"userId": sub}]),
                }
            }
        },
    }


# ── Task 1.2.2 tests ─────────────────────────────────────────────────────────

def test_returns_401_when_no_tenant(mock_rds):
    """users lookup returns empty → no tenant."""
    mock_rds.execute_statement.return_value = {"records": []}
    import main
    resp = main.handler(_event_with_tenant(), None)
    assert resp["statusCode"] == 401
    assert json.loads(resp["body"])["error"] == "no_tenant"


def test_returns_400_for_unknown_format(mock_rds):
    mock_rds.execute_statement.return_value = {
        "records": [[{"stringValue": "t-1"}]]
    }
    import main
    resp = main.handler(_event_with_tenant(fmt="spdx-ai"), None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"] == "unknown_format"
    assert body["supported"] == ["cyclonedx"]


def test_returns_200_with_cyclonedx_payload_for_empty_inventory(mock_rds):
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},  # tenant_id
        {"records": []},  # entities
        {"records": []},  # edges
        {"records": []},  # findings
    ]
    import main
    resp = main.handler(_event_with_tenant(), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["bomFormat"] == "CycloneDX"
    assert body["specVersion"] == "1.6"
    assert body.get("components", []) == []
    assert body.get("dependencies", []) == []
    assert body.get("vulnerabilities", []) == []
    assert resp["headers"]["Content-Type"].startswith("application/vnd.cyclonedx+json")


# ── Task 1.2.3 test ──────────────────────────────────────────────────────────

def test_entity_emits_machine_learning_model_component(mock_rds):
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},  # tenant
        # one entity: bedrock_model
        {"records": [[
            {"stringValue": "e-1"},
            {"stringValue": "bedrock_model"},
            {"stringValue": "claude-3-opus"},
            {"stringValue": "arn:aws:bedrock:us-east-1:111:model/x"},
            {"stringValue": "shasta-runner-aws"},
            {"stringValue": "2026-05-01T10:00:00Z"},
        ]]},
        {"records": []},  # edges
        {"records": []},  # findings
    ]
    import main
    resp = main.handler(_event_with_tenant(), None)
    body = json.loads(resp["body"])
    assert len(body["components"]) == 1
    comp = body["components"][0]
    assert comp["type"] == "machine-learning-model"
    assert comp["bom-ref"] == "e-1"
    assert comp["name"] == "claude-3-opus"
    props = {p["name"]: p["value"] for p in comp["properties"]}
    assert props["shasta:kind"] == "bedrock_model"
    assert props["shasta:detector_id"] == "shasta-runner-aws"


# ── Task 1.2.4 test ──────────────────────────────────────────────────────────

def test_edges_emit_dependencies(mock_rds):
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},
        {"records": [
            [{"stringValue": "repo-1"}, {"stringValue": "ai_framework"},
             {"stringValue": "langchain"}, {"stringValue": ""},
             {"stringValue": "ai-scanner"}, {"stringValue": "2026-05-01"}],
            [{"stringValue": "fw-1"}, {"stringValue": "ai_framework"},
             {"stringValue": "openai"}, {"stringValue": ""},
             {"stringValue": "ai-scanner"}, {"stringValue": "2026-05-01"}],
        ]},
        {"records": [[{"stringValue": "repo-1"}, {"stringValue": "fw-1"}, {"stringValue": "uses"}]]},
        {"records": []},
    ]
    import main
    resp = main.handler(_event_with_tenant(), None)
    body = json.loads(resp["body"])
    assert len(body.get("dependencies", [])) >= 1
    found = [d for d in body["dependencies"] if d["ref"] == "repo-1"]
    assert len(found) == 1
    assert "fw-1" in found[0]["dependsOn"]


# ── Task 1.2.5 tests ─────────────────────────────────────────────────────────

def test_findings_emit_vulnerabilities(mock_rds):
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},
        {"records": [[{"stringValue": "fw-1"}, {"stringValue": "ai_framework"},
                      {"stringValue": "langchain"}, {"stringValue": ""},
                      {"stringValue": "ai-scanner"}, {"stringValue": "2026-05-01"}]]},
        {"records": []},
        {"records": [[
            {"stringValue": "f-1"},
            {"stringValue": "sca_vuln:CVE-2026-45134"},
            {"stringValue": "critical"},
            {"stringValue": "fw-1"},
            {"stringValue": '{"owasp_llm_top10": ["LLM03:2025"]}'},
        ]]},
    ]
    import main
    resp = main.handler(_event_with_tenant(), None)
    body = json.loads(resp["body"])
    assert len(body["vulnerabilities"]) == 1
    vuln = body["vulnerabilities"][0]
    assert vuln["bom-ref"] == "f-1"
    assert vuln["id"] == "sca_vuln:CVE-2026-45134"
    assert vuln["ratings"][0]["severity"] == "critical"
    assert vuln["affects"][0]["ref"] == "fw-1"


def test_findings_with_legacy_array_frameworks_do_not_crash(mock_rds):
    """FINDINGS.md §A.4 — ai_supply_chain_matcher emits frameworks=[] not {}.
    The export must coerce non-object JSON to {} and still return the
    finding via the sca_vuln:* check_id branch."""
    mock_rds.execute_statement.side_effect = [
        {"records": [[{"stringValue": "t-1"}]]},
        {"records": [[{"stringValue": "e-1"}, {"stringValue": "ai_framework"},
                      {"stringValue": "x"}, {"stringValue": ""},
                      {"stringValue": "x"}, {"stringValue": "2026-01-01"}]]},
        {"records": []},
        # frameworks is a JSON array (the bug shape)
        {"records": [[
            {"stringValue": "f-2"},
            {"stringValue": "sca_vuln:CVE-2026-99999"},
            {"stringValue": "high"},
            {"stringValue": "e-1"},
            {"stringValue": "[]"},  # ← the bug shape
        ]]},
    ]
    import main
    resp = main.handler(_event_with_tenant(), None)
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200  # no crash
    assert len(body["vulnerabilities"]) == 1  # finding still picked up via sca_vuln:* branch
