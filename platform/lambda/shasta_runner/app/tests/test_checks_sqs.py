# app/tests/test_checks_sqs.py
"""SQS posture checks evaluate a queue Resource into an Outcome."""
import json

from coverage.checks.sqs import CHECKS
from coverage.model import Resource

_BY_ID = {c.check_id: c for c in CHECKS}


def _queue(raw):
    return Resource(service="sqs", resource_type="queue",
                    arn="arn:aws:sqs:us-east-1:111:q1", name="q1",
                    region="us-east-1", raw=raw)


def test_encryption_check_passes_with_sse():
    out = _BY_ID["sqs-encryption-at-rest"].evaluate(
        _queue({"SqsManagedSseEnabled": "true"}))
    assert out.status == "pass"


def test_encryption_check_passes_with_kms_key():
    out = _BY_ID["sqs-encryption-at-rest"].evaluate(
        _queue({"KmsMasterKeyId": "alias/aws/sqs"}))
    assert out.status == "pass"


def test_encryption_check_fails_when_unencrypted():
    out = _BY_ID["sqs-encryption-at-rest"].evaluate(_queue({}))
    assert out.status == "fail"
    assert out.remediation


def test_public_policy_check_fails_on_wildcard_principal():
    policy = json.dumps({"Statement": [
        {"Effect": "Allow", "Principal": "*", "Action": "sqs:SendMessage"}]})
    out = _BY_ID["sqs-queue-not-public"].evaluate(_queue({"Policy": policy}))
    assert out.status == "fail"


def test_public_policy_check_partial_when_wildcard_has_condition():
    policy = json.dumps({"Statement": [
        {"Effect": "Allow", "Principal": {"AWS": "*"},
         "Action": "sqs:SendMessage",
         "Condition": {"StringEquals": {"aws:SourceAccount": "111"}}}]})
    out = _BY_ID["sqs-queue-not-public"].evaluate(_queue({"Policy": policy}))
    assert out.status == "partial"


def test_public_policy_check_passes_without_policy():
    out = _BY_ID["sqs-queue-not-public"].evaluate(_queue({}))
    assert out.status == "pass"


def test_dlq_check_fails_without_redrive_policy():
    out = _BY_ID["sqs-dlq-configured"].evaluate(_queue({}))
    assert out.status == "fail"


def test_dlq_check_passes_with_redrive_policy():
    out = _BY_ID["sqs-dlq-configured"].evaluate(
        _queue({"RedrivePolicy": json.dumps({"deadLetterTargetArn": "arn:..."})}))
    assert out.status == "pass"


def test_every_check_is_well_formed():
    for c in CHECKS:
        assert c.service == "sqs" and c.resource_type == "queue"
        assert c.min_tier in ("quick", "medium", "deep")
        assert c.severity in ("low", "medium", "high", "critical")
