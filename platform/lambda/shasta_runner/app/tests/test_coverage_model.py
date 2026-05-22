# app/tests/test_coverage_model.py
"""The coverage model types — Resource, Outcome, Check — carry the data
the engine passes between collectors, checks, and emission."""
from coverage.model import Check, Outcome, Resource


def _resource(**over):
    base = dict(service="sqs", resource_type="queue",
                arn="arn:aws:sqs:us-east-1:111:q1", name="q1",
                region="us-east-1", raw={"x": 1})
    base.update(over)
    return Resource(**base)


def test_resource_carries_normalized_fields():
    r = _resource()
    assert r.service == "sqs"
    assert r.arn.endswith("q1")
    assert r.raw == {"x": 1}


def test_outcome_defaults_remediation_empty():
    o = Outcome("pass", {"k": "v"})
    assert o.status == "pass"
    assert o.remediation == ""


def test_check_holds_metadata_and_callable_evaluate():
    chk = Check(
        check_id="x-1", service="sqs", resource_type="queue",
        title="t", severity="medium", domain="encryption",
        min_tier="quick", frameworks={"fsbp": ["SQS.1"]},
        evaluate=lambda r: Outcome("pass", {}),
    )
    out = chk.evaluate(_resource())
    assert out.status == "pass"
    assert chk.frameworks["fsbp"] == ["SQS.1"]
