# app/coverage/checks/ecr.py
"""Posture checks for ECR private repositories."""
from __future__ import annotations

from coverage.model import Check, Outcome, Resource


def _scan_on_push(r: Resource) -> Outcome:
    scan_on_push = bool(
        (r.raw.get("imageScanningConfiguration") or {}).get("scanOnPush"))
    if scan_on_push:
        return Outcome("pass", {"scan_on_push": True})
    return Outcome(
        "fail", {"scan_on_push": False},
        remediation="Enable scan-on-push so images are scanned for "
                    "vulnerabilities when pushed.",
    )


def _tag_immutability(r: Resource) -> Outcome:
    immutable = r.raw.get("imageTagMutability") == "IMMUTABLE"
    if immutable:
        return Outcome("pass", {"image_tag_mutability": "IMMUTABLE"})
    return Outcome(
        "fail", {"image_tag_mutability": r.raw.get("imageTagMutability", "MUTABLE")},
        remediation="Set the repository's tag mutability to IMMUTABLE so "
                    "image tags cannot be overwritten.",
    )


CHECKS = [
    Check(
        check_id="ecr-scan-on-push", service="ecr", resource_type="repository",
        title="ECR repository should scan images on push",
        severity="medium", domain="compute", min_tier="medium",
        frameworks={"fsbp": ["ECR.1"], "nist_800_53": ["RA-5"]},
        evaluate=_scan_on_push,
    ),
    Check(
        check_id="ecr-tag-immutability", service="ecr", resource_type="repository",
        title="ECR repository should have tag immutability enabled",
        severity="low", domain="compute", min_tier="medium",
        frameworks={"fsbp": ["ECR.2"], "nist_800_53": ["CM-2"]},
        evaluate=_tag_immutability,
    ),
]
