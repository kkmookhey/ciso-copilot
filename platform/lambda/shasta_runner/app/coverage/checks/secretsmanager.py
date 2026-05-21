# app/coverage/checks/secretsmanager.py
"""Posture checks for Secrets Manager secrets."""
from __future__ import annotations

from coverage.model import Check, Outcome, Resource

# A secret with no KmsKeyId, or one pointing at the account default key,
# is encrypted with the AWS-managed aws/secretsmanager key.
_DEFAULT_KEY_MARKERS = ("alias/aws/secretsmanager",)


def _rotation_enabled(r: Resource) -> Outcome:
    if r.raw.get("RotationEnabled") is True:
        return Outcome("pass", {"rotation_enabled": True})
    return Outcome(
        "fail", {"rotation_enabled": False},
        remediation="Enable automatic rotation on the secret.",
    )


def _cmk_encryption(r: Resource) -> Outcome:
    key = r.raw.get("KmsKeyId")
    if key and not any(m in key for m in _DEFAULT_KEY_MARKERS):
        return Outcome("pass", {"kms_key_id": key})
    return Outcome(
        "partial", {"kms_key_id": key or None,
                    "note": "uses the default aws/secretsmanager key"},
        remediation="Encrypt the secret with a customer-managed KMS key "
                    "for independent key control and audit.",
    )


CHECKS = [
    Check(
        check_id="secretsmanager-rotation-enabled",
        service="secretsmanager", resource_type="secret",
        title="Secrets Manager secret should have automatic rotation enabled",
        severity="medium", domain="iam", min_tier="medium",
        frameworks={"fsbp": ["SecretsManager.1"], "nist_800_53": ["IA-5"]},
        evaluate=_rotation_enabled,
    ),
    Check(
        check_id="secretsmanager-cmk-encryption",
        service="secretsmanager", resource_type="secret",
        title="Secrets Manager secret should use a customer-managed KMS key",
        severity="low", domain="encryption", min_tier="medium",
        frameworks={"nist_800_53": ["SC-28"]},
        evaluate=_cmk_encryption,
    ),
]
