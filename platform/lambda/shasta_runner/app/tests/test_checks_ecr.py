# app/tests/test_checks_ecr.py
"""ECR posture checks."""
from coverage.checks.ecr import CHECKS
from coverage.model import Resource

_BY_ID = {c.check_id: c for c in CHECKS}


def _repo(raw):
    return Resource(service="ecr", resource_type="repository",
                    arn="arn:aws:ecr:us-east-1:111:repository/app",
                    name="app", region="us-east-1", raw=raw)


def test_scan_on_push_passes_when_enabled():
    out = _BY_ID["ecr-scan-on-push"].evaluate(
        _repo({"imageScanningConfiguration": {"scanOnPush": True}}))
    assert out.status == "pass"


def test_scan_on_push_fails_when_disabled():
    out = _BY_ID["ecr-scan-on-push"].evaluate(
        _repo({"imageScanningConfiguration": {"scanOnPush": False}}))
    assert out.status == "fail"


def test_scan_on_push_fails_when_config_absent():
    out = _BY_ID["ecr-scan-on-push"].evaluate(_repo({}))
    assert out.status == "fail"


def test_tag_immutability_passes_when_immutable():
    out = _BY_ID["ecr-tag-immutability"].evaluate(
        _repo({"imageTagMutability": "IMMUTABLE"}))
    assert out.status == "pass"


def test_tag_immutability_fails_when_mutable():
    out = _BY_ID["ecr-tag-immutability"].evaluate(
        _repo({"imageTagMutability": "MUTABLE"}))
    assert out.status == "fail"
