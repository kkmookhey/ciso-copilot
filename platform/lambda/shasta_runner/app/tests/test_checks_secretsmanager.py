# app/tests/test_checks_secretsmanager.py
"""Secrets Manager posture checks."""
from coverage.checks.secretsmanager import CHECKS
from coverage.model import Resource

_BY_ID = {c.check_id: c for c in CHECKS}


def _secret(raw):
    return Resource(service="secretsmanager", resource_type="secret",
                    arn="arn:aws:secretsmanager:us-east-1:111:secret:s",
                    name="s", region="us-east-1", raw=raw)


def test_rotation_passes_when_enabled():
    out = _BY_ID["secretsmanager-rotation-enabled"].evaluate(
        _secret({"RotationEnabled": True}))
    assert out.status == "pass"


def test_rotation_fails_when_disabled():
    out = _BY_ID["secretsmanager-rotation-enabled"].evaluate(
        _secret({"RotationEnabled": False}))
    assert out.status == "fail"


def test_rotation_fails_when_absent():
    out = _BY_ID["secretsmanager-rotation-enabled"].evaluate(_secret({}))
    assert out.status == "fail"


def test_cmk_passes_with_customer_managed_key():
    out = _BY_ID["secretsmanager-cmk-encryption"].evaluate(
        _secret({"KmsKeyId": "arn:aws:kms:us-east-1:111:key/abc"}))
    assert out.status == "pass"


def test_cmk_partial_on_default_aws_managed_key():
    out = _BY_ID["secretsmanager-cmk-encryption"].evaluate(
        _secret({"KmsKeyId": "alias/aws/secretsmanager"}))
    assert out.status == "partial"


def test_cmk_partial_when_key_absent():
    # No KmsKeyId means the secret uses the default aws/secretsmanager key.
    out = _BY_ID["secretsmanager-cmk-encryption"].evaluate(_secret({}))
    assert out.status == "partial"
