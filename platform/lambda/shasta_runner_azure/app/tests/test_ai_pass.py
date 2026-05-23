"""Unit tests for the Azure ai_pass module.

Pure helpers (discovery_to_entities, ai_findings_to_emissions) are unit-tested
against fixture dicts/objects. run_ai_pass is not tested here — it's exercised
in the runner-level integration test.
"""
from __future__ import annotations

import pytest
from ai_pass import discovery_to_entities, ai_findings_to_emissions


def test_discovery_to_entities_emits_azure_openai():
    discovery = {
        "azure_openai": {
            "accounts": [
                {
                    "name": "openai-prod",
                    "id": "/subscriptions/SUB/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/openai-prod",
                    "location": "eastus",
                    "sku": "S0",
                }
            ],
        },
    }
    entities, edges = discovery_to_entities(
        discovery, subscription_id="SUB", tenant_id="TEN"
    )
    assert len(entities) == 1
    e = entities[0]
    assert e.kind == "azure_openai_deployment"
    assert e.natural_key.endswith("/openai-prod")
    assert e.domain == "cloud"
    assert len(edges) == 1
    assert edges[0].source_kind == "azure_subscription"
    assert edges[0].source_natural_key == "SUB"
    assert edges[0].target_kind == "azure_openai_deployment"
    assert edges[0].kind == "contains"


def test_discovery_to_entities_emits_azure_ml_workspace():
    discovery = {
        "azure_ml": {
            "workspaces": [
                {"name": "ml-prod",
                 "id": "/subscriptions/SUB/.../workspaces/ml-prod",
                 "location": "eastus"}
            ],
        },
    }
    entities, edges = discovery_to_entities(
        discovery, subscription_id="SUB", tenant_id="TEN"
    )
    assert len(entities) == 1
    assert entities[0].kind == "azure_ml_workspace"


def test_discovery_to_entities_emits_cognitive_service_and_skips_openai_kind():
    discovery = {
        "cognitive_services": {
            "accounts": [
                {"name": "vision-1",
                 "id": "/subscriptions/SUB/.../accounts/vision-1",
                 "kind": "ComputerVision", "location": "eastus", "sku": "S1"},
                # This one should be skipped — already emitted via azure_openai.
                {"name": "openai-prod",
                 "id": "/subscriptions/SUB/.../accounts/openai-prod",
                 "kind": "OpenAI", "location": "eastus", "sku": "S0"},
            ],
        },
    }
    entities, edges = discovery_to_entities(
        discovery, subscription_id="SUB", tenant_id="TEN"
    )
    kinds = sorted(e.kind for e in entities)
    assert kinds == ["cognitive_service"]
    assert entities[0].display_name == "vision-1"


def test_discovery_to_entities_drops_entries_missing_id_or_name():
    discovery = {
        "azure_openai": {
            "accounts": [
                {"name": "", "id": "/subscriptions/SUB/.../accounts/x"},   # no name
                {"name": "y", "id": ""},                                    # no id
                {"name": "good", "id": "/subscriptions/SUB/.../accounts/good"},
            ],
        },
    }
    entities, _ = discovery_to_entities(
        discovery, subscription_id="SUB", tenant_id="TEN"
    )
    assert [e.display_name for e in entities] == ["good"]


def test_ai_findings_to_emissions_drops_not_assessed():
    class F:
        check_id = "azure_openai_content_filter"
        title = "Azure OpenAI content filter enabled"
        description = "..."
        severity = "high"
        status = "not_assessed"
        details = {}
        soc2_controls = []
        cis_aws_controls = []
        iso27001_controls = []
        hipaa_controls = []
        mcsb_controls = []
        region = "eastus"
        resource_type = "openai_account"
        resource_id = "/subscriptions/SUB/.../accounts/x"
        remediation = ""
        domain = "ai_governance"
    out = ai_findings_to_emissions([F()], tenant_id="TEN")
    assert out == []


def test_ai_findings_to_emissions_carries_ai_frameworks_from_details():
    class F:
        check_id = "azure_openai_content_filter"
        title = "Azure OpenAI content filter enabled"
        description = "OpenAI content filtering disabled"
        severity = "high"
        status = "fail"
        details = {
            "iso42001_controls": ["A.9.2.1"],
            "nist_ai_rmf":       ["GOVERN-1.1"],
            "eu_ai_act":         ["Article 15"],
        }
        soc2_controls = []
        cis_aws_controls = []
        iso27001_controls = []
        hipaa_controls = []
        mcsb_controls = []
        region = "eastus"
        resource_type = "openai_account"
        resource_id = "/subscriptions/SUB/.../accounts/x"
        remediation = "Turn the filter on."
        domain = "ai_governance"
    out = ai_findings_to_emissions([F()], tenant_id="TEN")
    assert len(out) == 1
    fe = out[0]
    assert fe.frameworks.get("iso_42001") == ["A.9.2.1"]
    assert fe.frameworks.get("nist_ai_rmf") == ["GOVERN-1.1"]
    assert fe.frameworks.get("eu_ai_act") == ["Article 15"]
    assert fe.domain == "ai"
    assert fe.status == "fail"
    assert fe.severity == "high"
