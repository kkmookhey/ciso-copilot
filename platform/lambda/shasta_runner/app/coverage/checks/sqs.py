# app/coverage/checks/sqs.py
"""Posture checks for SQS queues.

SQS get_queue_attributes returns every attribute value as a string;
the checks read r.raw accordingly.
"""
from __future__ import annotations

import json

from coverage.model import Check, Outcome, Resource


def _encryption_at_rest(r: Resource) -> Outcome:
    sse_managed = str(r.raw.get("SqsManagedSseEnabled", "")).lower() == "true"
    kms_key = r.raw.get("KmsMasterKeyId")
    if sse_managed or kms_key:
        return Outcome("pass", {"sqs_managed_sse": sse_managed,
                                "kms_master_key_id": kms_key})
    return Outcome(
        "fail", {"sqs_managed_sse": False, "kms_master_key_id": None},
        remediation="Enable SSE-SQS or assign an SSE-KMS key to the queue.",
    )


def _statement_is_public(stmt: dict) -> bool:
    if stmt.get("Effect") != "Allow":
        return False
    principal = stmt.get("Principal")
    return principal == "*" or (
        isinstance(principal, dict) and "*" in _as_list(principal.get("AWS")))


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _queue_not_public(r: Resource) -> Outcome:
    raw_policy = r.raw.get("Policy")
    if not raw_policy:
        return Outcome("pass", {"policy": None})
    try:
        policy = json.loads(raw_policy)
    except (ValueError, TypeError):
        return Outcome("partial", {"policy": "unparseable"},
                       remediation="Queue policy could not be parsed; review it manually.")
    public = [s for s in _as_list(policy.get("Statement")) if _statement_is_public(s)]
    if not public:
        return Outcome("pass", {"public_statements": 0})
    # A wildcard principal guarded by a Condition is a softer finding.
    conditioned = all(s.get("Condition") for s in public)
    status = "partial" if conditioned else "fail"
    return Outcome(
        status,
        {"public_statements": len(public), "all_conditioned": conditioned},
        remediation="Restrict the queue policy to specific principals, "
                    "or scope wildcard access with a Condition.",
    )


def _dlq_configured(r: Resource) -> Outcome:
    if r.raw.get("RedrivePolicy"):
        return Outcome("pass", {"redrive_policy": True})
    return Outcome(
        "fail", {"redrive_policy": False},
        remediation="Attach a redrive policy pointing at a dead-letter queue.",
    )


CHECKS = [
    Check(
        check_id="sqs-encryption-at-rest", service="sqs", resource_type="queue",
        title="SQS queue should be encrypted at rest",
        severity="medium", domain="encryption", min_tier="quick",
        frameworks={"fsbp": ["SQS.1"], "nist_800_53": ["SC-28"]},
        evaluate=_encryption_at_rest,
    ),
    Check(
        check_id="sqs-queue-not-public", service="sqs", resource_type="queue",
        title="SQS queue policy should not grant public access",
        severity="high", domain="networking", min_tier="quick",
        frameworks={"nist_800_53": ["AC-3", "AC-6"]},
        evaluate=_queue_not_public,
    ),
    Check(
        check_id="sqs-dlq-configured", service="sqs", resource_type="queue",
        title="SQS queue should have a dead-letter queue configured",
        severity="low", domain="monitoring", min_tier="medium",
        frameworks={},
        evaluate=_dlq_configured,
    ),
]
