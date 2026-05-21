# app/coverage/engine.py
"""The coverage engine — runs tier-filtered posture checks over collected
AWS resources and emits entities, edges, and findings.

run_coverage is wrapped by main.py like the other scan passes; one
failing collector (e.g. a permission-denied service) is caught and
skipped so it cannot kill the scan. See spec §6.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable

from detectors.base import EdgeEmission, EntityEmission, FindingEmission

from coverage.registry import COLLECTORS, checks_for_tier

_DETECTOR_ID      = "shasta_runner.coverage"
_DETECTOR_VERSION = "0.1.0"


def run_coverage(
    make_session: Callable[[str], Any], *,
    account_id: str, tenant_id: str,
    regions: list[str], scan_tier: str,
) -> dict[str, list]:
    """Run the coverage engine.

    `make_session(region)` returns a boto3 Session bound to that region.
    Returns {'entities': [...], 'edges': [...], 'findings': [...]}.
    """
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission]   = []
    findings: list[FindingEmission] = []

    checks = checks_for_tier(scan_tier)
    checks_by_service: dict[str, list] = {}
    for c in checks:
        checks_by_service.setdefault(c.service, []).append(c)

    for region in regions:
        session = make_session(region)
        for service, service_checks in checks_by_service.items():
            try:
                client = session.client(service)
                resources = COLLECTORS[service](
                    client, account_id=account_id, region=region)
            except Exception as e:
                print(f"coverage/{service}@{region} collect FAILED: {e}\n"
                      f"{traceback.format_exc()}")
                continue

            for r in resources:
                kind = f"aws_{r.service}_{r.resource_type}"
                entities.append(EntityEmission(
                    tenant_id=tenant_id, kind=kind, natural_key=r.arn,
                    display_name=r.name, domain="cloud",
                    attributes={"service": r.service, "account": account_id,
                                "region": r.region,
                                "resource_type": r.resource_type},
                    evidence_packet=None,
                    detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
                ))
                edges.append(EdgeEmission(
                    tenant_id=tenant_id,
                    source_kind="aws_account", source_natural_key=account_id,
                    target_kind=kind, target_natural_key=r.arn,
                    kind="contains", attributes={},
                    evidence_packet={"version": "0.1", "via": "coverage_engine"},
                    detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
                ))
                for check in service_checks:
                    if check.resource_type != r.resource_type:
                        continue
                    outcome = check.evaluate(r)
                    findings.append(_to_finding(check, r, outcome, kind, tenant_id))

    return {"entities": entities, "edges": edges, "findings": findings}


def _to_finding(check, r, outcome, kind: str, tenant_id: str) -> FindingEmission:
    return FindingEmission(
        tenant_id=tenant_id,
        finding_type=check.check_id,
        severity=check.severity,
        title=check.title[:500],
        description=(outcome.remediation or "")[:2000],
        subject_entity_kind=kind,
        subject_entity_natural_key=r.arn,
        subject_type=r.resource_type,
        subject_ref=r.arn[:500],
        evidence_packet={
            "version": "0.1",
            "coverage_engine": {
                "check_id":  check.check_id,
                "status":    outcome.status,
                "evidence":  outcome.evidence,
                "remediation": (outcome.remediation or "")[:2000],
                "frameworks": check.frameworks,
            },
        },
        confidence="high",
        frameworks=check.frameworks,
        domain=check.domain,
        status=outcome.status,
        region=r.region,
    )
