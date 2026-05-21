"""Unit tests for the cloud-AI pass (ai_pass.py)."""


def test_match_ai_env_vars_detects_known_providers():
    from ai_pass import _match_ai_env_vars

    matched = _match_ai_env_vars({
        "OPENAI_API_KEY": "x",
        "anthropic_api_key": "y",     # case-insensitive
        "AWS_REGION": "us-east-1",    # not an AI key
        "MY_GROQ_API_KEY": "z",       # substring match
    })
    assert sorted(matched) == ["MY_GROQ_API_KEY", "OPENAI_API_KEY", "anthropic_api_key"]


def test_match_ai_env_vars_empty_when_no_ai_keys():
    from ai_pass import _match_ai_env_vars

    assert _match_ai_env_vars({"AWS_REGION": "us-east-1", "PORT": "8080"}) == []


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


def test_discover_bedrock_and_ai_lambdas_collects_across_regions():
    from unittest.mock import MagicMock
    from ai_pass import discover_bedrock_and_ai_lambdas

    def make_regional(region):
        bedrock = MagicMock()
        bedrock.list_guardrails.return_value = {
            "guardrails": [
                {"id": f"gr-{region}",
                 "arn": f"arn:aws:bedrock:{region}:1:guardrail/gr-{region}",
                 "name": "pii", "status": "READY"},
            ],
        }
        lam = MagicMock()
        lam.get_paginator.return_value.paginate.return_value = [
            {"Functions": [
                {"FunctionName": "ai-fn",
                 "FunctionArn": f"arn:aws:lambda:{region}:1:function:ai-fn",
                 "Runtime": "python3.12",
                 "Environment": {"Variables": {"OPENAI_API_KEY": "x"}}},
                {"FunctionName": "plain-fn",
                 "FunctionArn": f"arn:aws:lambda:{region}:1:function:plain-fn",
                 "Runtime": "python3.12",
                 "Environment": {"Variables": {"PORT": "8080"}}},
            ]},
        ]
        rc = MagicMock()
        rc.client.side_effect = lambda svc: bedrock if svc == "bedrock" else lam
        return rc

    client = MagicMock()
    client.get_enabled_regions.return_value = ["us-east-1", "eu-west-1"]
    client.for_region.side_effect = make_regional

    result = discover_bedrock_and_ai_lambdas(client)

    guardrails = result["bedrock"]["guardrails"]
    assert {g["region"] for g in guardrails} == {"us-east-1", "eu-west-1"}
    assert result["bedrock"]["available"] is True

    fns = result["lambda_ai"]["functions_with_ai_vars"]
    assert len(fns) == 2                                  # one AI fn per region
    assert all(f["function_name"] == "ai-fn" for f in fns)  # plain-fn excluded
    assert fns[0]["ai_env_vars"] == ["OPENAI_API_KEY"]
    assert {f["region"] for f in fns} == {"us-east-1", "eu-west-1"}


def test_discover_bedrock_and_ai_lambdas_tolerates_regional_failure():
    """A region where Bedrock/Lambda is unavailable must not abort discovery."""
    from unittest.mock import MagicMock
    from ai_pass import discover_bedrock_and_ai_lambdas

    def make_regional(region):
        rc = MagicMock()
        if region == "us-east-1":
            bedrock = MagicMock()
            bedrock.list_guardrails.return_value = {
                "guardrails": [{"id": "gr-1", "arn": "arn:gr-1",
                                "name": "g", "status": "READY"}],
            }
            lam = MagicMock()
            lam.get_paginator.return_value.paginate.return_value = []
            rc.client.side_effect = lambda svc: bedrock if svc == "bedrock" else lam
        else:
            rc.client.side_effect = RuntimeError("not available in region")
        return rc

    client = MagicMock()
    client.get_enabled_regions.return_value = ["us-east-1", "ap-south-1"]
    client.for_region.side_effect = make_regional

    result = discover_bedrock_and_ai_lambdas(client)
    assert len(result["bedrock"]["guardrails"]) == 1


def test_discovery_to_entities_maps_bedrock_guardrails_and_ai_lambdas():
    from ai_pass import discovery_to_entities

    discovery = {
        "bedrock": {
            "available": True,
            "guardrails": [
                {"id": "gr-1",
                 "arn": "arn:aws:bedrock:us-east-1:111122223333:guardrail/gr-1",
                 "name": "pii-filter", "status": "READY", "region": "us-east-1"},
            ],
        },
        "lambda_ai": {
            "available": True,
            "functions_with_ai_vars": [
                {"function_name": "summarise",
                 "function_arn": "arn:aws:lambda:us-east-1:111122223333:function:summarise",
                 "runtime": "python3.12", "region": "us-east-1",
                 "ai_env_vars": ["OPENAI_API_KEY"]},
            ],
        },
    }

    entities, edges = discovery_to_entities(
        discovery, account_id="111122223333", tenant_id="tnt-1",
    )
    by_kind = {e.kind: e for e in entities}
    assert set(by_kind) == {"bedrock_guardrail", "lambda_ai_function"}

    gr = by_kind["bedrock_guardrail"]
    assert gr.natural_key == "arn:aws:bedrock:us-east-1:111122223333:guardrail/gr-1"
    assert gr.display_name == "pii-filter"
    assert gr.domain == "cloud"
    assert gr.attributes["status"] == "READY"
    assert gr.attributes["region"] == "us-east-1"

    fn = by_kind["lambda_ai_function"]
    assert fn.natural_key == (
        "arn:aws:lambda:us-east-1:111122223333:function:summarise"
    )
    assert fn.display_name == "summarise"
    assert fn.attributes["runtime"] == "python3.12"
    assert fn.attributes["ai_env_vars"] == ["OPENAI_API_KEY"]

    assert len(edges) == 2
    assert all(e.kind == "contains"
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
    assert e.domain == "ai"      # ai_governance maps to 'ai'
    assert e.status == "fail"    # real status carried, not hardcoded


def test_ai_findings_to_emissions_drops_not_assessed_and_not_applicable():
    """not_assessed ('Unable to check …') and not_applicable are noise and
    must not be ingested as findings; pass/fail/partial are kept."""
    import types
    from ai_pass import ai_findings_to_emissions

    def mk(status, check):
        return types.SimpleNamespace(
            check_id=check, title="t", description="d", severity="medium",
            status=status, domain="ai_governance", region="us-east-1",
            resource_type="x", resource_id="r", remediation="",
            soc2_controls=[], cis_aws_controls=[], iso27001_controls=[],
            hipaa_controls=[], mcsb_controls=[], details={},
        )

    findings = [
        mk("not_assessed",   "unable-1"),
        mk("not_applicable", "na-1"),
        mk("fail",           "real-fail"),
        mk("partial",        "real-partial"),
        mk("pass",           "real-pass"),
    ]
    out = ai_findings_to_emissions(findings, tenant_id="t1")
    assert sorted(e.finding_type for e in out) == [
        "real-fail", "real-partial", "real-pass",
    ]


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
