"""Convert Shasta GCP Finding objects into the platform's unified
emission types — the GCP analog of azure_findings.convert_azure_findings.

Pure: no Shasta or Google-SDK import. Operates on duck-typed Finding
objects (anything with the Shasta Finding fields), so it is
unit-testable without the scanner runtime.
"""
from __future__ import annotations

from typing import Any

from detectors.base import EdgeEmission, EntityEmission, FindingEmission
from framework_map import merge_framework_map
from gcp_id_to_entity import parse_gcp_id

_DETECTOR_ID_BASE = "shasta_runner_gcp"


def project_entity(project_id: str, tenant_id: str) -> EntityEmission:
    """The top-level entity for a GCP project."""
    return EntityEmission(
        tenant_id=tenant_id,
        kind="gcp_project",
        natural_key=project_id,
        display_name=project_id,
        domain="cloud",
        attributes={"service": "gcp", "project": project_id},
        evidence_packet=None,
        detector_id=f"{_DETECTOR_ID_BASE}.project",
        detector_version="0.1.0",
    )


def convert_gcp_findings(shasta_findings: list[Any], tenant_id: str,
                         project_id: str) -> dict:
    """Convert Shasta Finding objects to {entities, edges, findings}.
    Pure, no shared state — safe to call concurrently per project. A
    resource ID that parses to a known kind also emits a subject entity
    + a `contains` edge from the project."""
    out_findings: list[FindingEmission] = []
    out_entities: list[EntityEmission] = []
    out_edges:    list[EdgeEmission]   = []
    seen: set[tuple[str, str]] = set()

    for f in shasta_findings:
        if f.status.value.lower() in ("not_assessed", "not_applicable"):
            continue
        rid = (getattr(f, "resource_id", "") or "").strip()
        subj_kind = subj_nk = None
        parsed = parse_gcp_id(rid) if rid else None
        if parsed:
            subj_kind, subj_nk = parsed["kind"], parsed["natural_key"]
            if (subj_kind, subj_nk) not in seen:
                seen.add((subj_kind, subj_nk))
                out_entities.append(EntityEmission(
                    tenant_id=tenant_id, kind=subj_kind, natural_key=subj_nk,
                    display_name=parsed["display_name"], domain="cloud",
                    attributes=parsed["attributes"], evidence_packet=None,
                    detector_id=f"{_DETECTOR_ID_BASE}.finding_resource",
                    detector_version="0.1.0"))
                out_edges.append(EdgeEmission(
                    tenant_id=tenant_id, source_kind="gcp_project",
                    source_natural_key=project_id, target_kind=subj_kind,
                    target_natural_key=subj_nk, kind="contains", attributes={},
                    evidence_packet={"version": "0.1", "via": "finding.resource_id"},
                    detector_id=f"{_DETECTOR_ID_BASE}.finding_resource",
                    detector_version="0.1.0"))
        out_findings.append(_to_emission(f, tenant_id, subj_kind, subj_nk))

    return {"entities": out_entities, "edges": out_edges, "findings": out_findings}


def _to_emission(f, tenant_id: str, subj_kind: str | None,
                 subj_nk: str | None) -> FindingEmission:
    frameworks = {
        "soc2":      f.soc2_controls,
        "cis_aws":   f.cis_aws_controls,
        "cis_azure": f.cis_azure_controls,
        "cis_gcp":   f.cis_gcp_controls,
        "mcsb":      f.mcsb_controls,
        "iso27001":  f.iso27001_controls,
        "hipaa":     f.hipaa_controls,
    }
    frameworks = {k: v for k, v in frameworks.items() if v}
    frameworks = merge_framework_map(f.check_id, frameworks)

    status = f.status.value.lower()
    domain = f.domain.value.lower()
    if domain == "ai_governance":
        domain = "ai"
    region = f.region or None

    evidence = {
        "version": "0.1",
        "shasta": {
            "check_id":      f.check_id,
            "status":        status,
            "domain":        domain,
            "region":        f.region,
            "resource_type": f.resource_type,
            "resource_id":   f.resource_id,
            "remediation":   (f.remediation or "")[:2000],
            "frameworks":    frameworks,
        },
    }
    return FindingEmission(
        tenant_id=tenant_id,
        finding_type=f.check_id,
        severity=f.severity.value.lower(),
        title=f.title[:500],
        description=(f.description or "")[:2000],
        subject_entity_kind=subj_kind,
        subject_entity_natural_key=subj_nk,
        subject_type=f.resource_type[:200] if f.resource_type else None,
        subject_ref=(f.resource_id or "")[:500] if f.resource_id else None,
        evidence_packet=evidence,
        confidence="high",
        frameworks=frameworks,
        domain=domain,
        status=status,
        region=region,
    )
