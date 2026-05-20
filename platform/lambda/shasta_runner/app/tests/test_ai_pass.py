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
