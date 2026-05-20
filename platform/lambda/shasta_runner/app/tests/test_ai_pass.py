"""Unit tests for the cloud-AI pass (ai_pass.py)."""


def test_discovery_to_entities_maps_sagemaker_and_comprehend():
    from ai_pass import discovery_to_entities

    discovery = {
        "sagemaker": {
            "available": True,
            "endpoints":     [{"name": "fraud-ep", "status": "InService",
                               "creation_time": "2026-01-02"}],
            "models":        [{"name": "fraud-model", "creation_time": "2026-01-01"}],
            "training_jobs": [{"name": "fraud-train", "status": "Completed",
                               "creation_time": "2025-12-30"}],
            "total_resources": 3,
        },
        "comprehend": {
            "available": True,
            "endpoints": [{"arn": "arn:aws:comprehend:us-east-1:111122223333:"
                                  "document-classifier-endpoint/pii",
                           "status": "IN_SERVICE", "model_arn": "arn:aws:comprehend:..."}],
            "total_resources": 1,
        },
        "bedrock":   {"available": True, "models": [], "total_resources": 4},
        "lambda_ai": {"available": False, "functions": [], "total_resources": 0},
    }

    entities, edges = discovery_to_entities(
        discovery, account_id="111122223333", tenant_id="tnt-1",
    )

    by_kind = {e.kind: e for e in entities}
    assert set(by_kind) == {
        "sagemaker_endpoint", "sagemaker_model",
        "sagemaker_training_job", "comprehend_endpoint",
    }
    assert by_kind["sagemaker_endpoint"].natural_key == "sagemaker:endpoint/fraud-ep"
    assert by_kind["sagemaker_endpoint"].domain == "cloud"
    assert by_kind["sagemaker_endpoint"].display_name == "fraud-ep"
    assert by_kind["sagemaker_endpoint"].attributes["status"] == "InService"
    assert by_kind["comprehend_endpoint"].natural_key == (
        "arn:aws:comprehend:us-east-1:111122223333:"
        "document-classifier-endpoint/pii"
    )
    # one contains-edge per entity, all rooted at the account
    assert len(edges) == 4
    assert all(e.kind == "contains" for e in edges)
    assert all(e.source_kind == "aws_account"
               and e.source_natural_key == "111122223333" for e in edges)


def test_ai_findings_to_emissions_pulls_frameworks_from_details():
    import types
    from ai_pass import ai_findings_to_emissions

    finding = types.SimpleNamespace(
        check_id="bedrock-guardrails-configured",
        title="Bedrock guardrails not configured",
        description="No guardrails on the account.",
        severity="MEDIUM",
        status="fail",
        domain="ai_governance",
        region="us-east-1",
        resource_type="bedrock-guardrails",
        resource_id="arn:aws:bedrock:us-east-1:111122223333:guardrails",
        remediation="Configure a guardrail.",
        soc2_controls=[],
        cis_aws_controls=[],
        iso27001_controls=[],
        hipaa_controls=[],
        mcsb_controls=[],
        details={
            "nist_ai_rmf":       ["MANAGE-2"],
            "iso42001_controls": ["AI-8.3"],
            "owasp_llm_top10":   ["LLM01"],
        },
    )

    emissions = ai_findings_to_emissions([finding], tenant_id="tnt-1")

    assert len(emissions) == 1
    e = emissions[0]
    assert e.finding_type == "bedrock-guardrails-configured"
    assert e.severity == "medium"
    assert e.tenant_id == "tnt-1"
    assert e.frameworks == {
        "nist_ai_rmf":     ["MANAGE-2"],
        "iso_42001":       ["AI-8.3"],
        "owasp_llm_top10": ["LLM01"],
    }
    assert e.evidence_packet["shasta"]["check_id"] == "bedrock-guardrails-configured"


def test_ai_findings_to_emissions_handles_missing_details():
    import types
    from ai_pass import ai_findings_to_emissions

    finding = types.SimpleNamespace(
        check_id="sagemaker-endpoint-encryption",
        title="SageMaker endpoint not encrypted",
        description="",
        severity="high",
        status="fail",
        domain="ai_governance",
        region="us-east-1",
        resource_type="sagemaker-endpoint",
        resource_id="fraud-ep",
        remediation="",
        soc2_controls=[],
        cis_aws_controls=[],
        iso27001_controls=[],
        hipaa_controls=[],
        mcsb_controls=[],
        details={},
    )

    emissions = ai_findings_to_emissions([finding], tenant_id="tnt-1")
    assert emissions[0].frameworks == {}
