"""Cloud-AI pass for Azure — wraps Shasta's Azure-AI discovery + checks
into the unified entity/edge/finding model.

Mirrors shasta_runner/app/ai_pass.py (AWS). Pure helpers
(discovery_to_entities, ai_findings_to_emissions) take already-fetched
data and are unit-tested directly. run_ai_pass is the orchestrator; it
imports Shasta lazily so this module imports cleanly in a test
environment without Shasta installed.
"""
from __future__ import annotations

import logging
from typing import Any

from detectors.base import EdgeEmission, EntityEmission, FindingEmission
from framework_map import merge_framework_map

logger = logging.getLogger(__name__)

_DETECTOR_ID      = "shasta_runner_azure.ai_pass"
_DETECTOR_VERSION = "0.1.0"

# Standard (non-AI) framework attributes on a Shasta Finding.
_STD_FRAMEWORK_ATTRS = {
    "soc2_controls":      "soc2",
    "cis_azure_controls": "cis_azure",
    "iso27001_controls":  "iso27001",
    "hipaa_controls":     "hipaa",
    "mcsb_controls":      "mcsb",
}

# AI-framework control lists, written into Finding.details by Shasta's
# enrich_findings_with_ai_controls(). Maps detail key -> framework key.
_AI_FRAMEWORK_DETAIL_KEYS = {
    "nist_ai_rmf":       "nist_ai_rmf",
    "iso42001_controls": "iso_42001",
    "eu_ai_act":         "eu_ai_act",
    "owasp_llm_top10":   "owasp_llm_top10",
    "owasp_agentic":     "owasp_agentic",
    "nist_ai_600_1":     "nist_ai_600_1",
    "mitre_atlas":       "mitre_atlas",
}


def _estr(value: Any) -> str:
    """Stringify an enum-or-string (Shasta enums expose .value)."""
    return value.value if hasattr(value, "value") else str(value)


def discovery_to_entities(
    discovery: dict[str, Any], *, subscription_id: str, tenant_id: str,
) -> tuple[list[EntityEmission], list[EdgeEmission]]:
    """Map an Azure-AI discovery result to entities + edges.

    Top-level Shasta keys (confirmed against shasta/azure/ai_discovery.py):
      azure_openai.accounts       -> kind=azure_openai_deployment
      azure_ml.workspaces         -> kind=azure_ml_workspace
      cognitive_services.accounts -> kind=cognitive_service
    """
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission]   = []

    def _emit(kind: str, natural_key: str, display_name: str,
              attributes: dict[str, Any]) -> None:
        entities.append(EntityEmission(
            tenant_id=tenant_id, kind=kind, natural_key=natural_key,
            display_name=display_name, domain="cloud", attributes=attributes,
            evidence_packet=None,
            detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
        ))
        edges.append(EdgeEmission(
            tenant_id=tenant_id,
            source_kind="azure_subscription",
            source_natural_key=subscription_id,
            target_kind=kind, target_natural_key=natural_key,
            kind="contains", attributes={},
            evidence_packet={"version": "0.1", "via": "ai_discovery"},
            detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
        ))

    for acct in discovery.get("azure_openai", {}).get("accounts", []):
        rid = acct.get("id") or ""
        name = acct.get("name") or ""
        if rid and name:
            _emit("azure_openai_deployment", rid, name, {
                "location": acct.get("location", ""),
                "sku":      acct.get("sku", ""),
            })

    for ws in discovery.get("azure_ml", {}).get("workspaces", []):
        rid = ws.get("id") or ""
        name = ws.get("name") or ""
        if rid and name:
            _emit("azure_ml_workspace", rid, name, {
                "location": ws.get("location", ""),
            })

    for cs in discovery.get("cognitive_services", {}).get("accounts", []):
        rid = cs.get("id") or ""
        name = cs.get("name") or ""
        kind = (cs.get("kind") or "").lower()
        # Skip cognitive services we already emitted as azure_openai.
        if kind == "openai":
            continue
        if rid and name:
            _emit("cognitive_service", rid, name, {
                "location": cs.get("location", ""),
                "kind":     cs.get("kind", ""),
                "sku":      cs.get("sku", ""),
            })

    return entities, edges


def ai_findings_to_emissions(
    findings: list[Any], *, tenant_id: str,
) -> list[FindingEmission]:
    """Map Shasta Azure-AI Findings to FindingEmission rows; pulls
    AI-framework control IDs from finding.details into .frameworks.

    not_assessed / not_applicable results are dropped — they are noise
    ("Unable to check …"), not findings."""
    out: list[FindingEmission] = []
    for f in findings:
        status = _estr(f.status).lower()
        if status in ("not_assessed", "not_applicable"):
            continue

        details = getattr(f, "details", None) or {}

        frameworks: dict[str, list[str]] = {}
        for attr, fw_key in _STD_FRAMEWORK_ATTRS.items():
            vals = getattr(f, attr, None)
            if vals:
                frameworks[fw_key] = list(vals)
        for detail_key, fw_key in _AI_FRAMEWORK_DETAIL_KEYS.items():
            vals = details.get(detail_key)
            if vals:
                frameworks[fw_key] = list(vals)
        frameworks = merge_framework_map(f.check_id, frameworks)

        domain = _estr(getattr(f, "domain", "")).lower()
        if domain in ("ai_governance", ""):
            domain = "ai"
        region = getattr(f, "region", "") or None

        evidence = {
            "version": "0.1",
            "shasta": {
                "check_id":      f.check_id,
                "status":        status,
                "domain":        domain,
                "region":        getattr(f, "region", ""),
                "resource_type": getattr(f, "resource_type", ""),
                "resource_id":   getattr(f, "resource_id", ""),
                "remediation":   (getattr(f, "remediation", "") or "")[:2000],
            },
        }
        out.append(FindingEmission(
            tenant_id=tenant_id,
            finding_type=f.check_id,
            severity=_estr(f.severity).lower(),
            title=(f.title or "")[:500],
            description=(getattr(f, "description", "") or "")[:2000],
            subject_entity_kind=None,
            subject_entity_natural_key=None,
            subject_type=(getattr(f, "resource_type", "") or None),
            subject_ref=((getattr(f, "resource_id", "") or "")[:500] or None),
            evidence_packet=evidence,
            confidence="high",
            frameworks=frameworks,
            domain=domain,
            status=status,
            region=region,
        ))
    return out


def run_ai_pass(client: Any, *, subscription_id: str,
                tenant_id: str) -> dict[str, list]:
    """Run Shasta's Azure-AI discovery + checks against a per-subscription
    AzureClient and return unified emissions. Shasta is imported lazily so
    this module stays importable in test environments without Shasta
    installed."""
    from shasta.azure.ai_discovery import discover_azure_ai_services
    from shasta.azure.ai_checks    import run_full_azure_ai_scan
    from shasta.compliance.ai.mapper import enrich_findings_with_ai_controls

    discovery = discover_azure_ai_services(client)
    entities, edges = discovery_to_entities(
        discovery, subscription_id=subscription_id, tenant_id=tenant_id,
    )

    findings = run_full_azure_ai_scan(client)
    enrich_findings_with_ai_controls(findings)
    finding_emissions = ai_findings_to_emissions(findings, tenant_id=tenant_id)

    return {"entities": entities, "edges": edges, "findings": finding_emissions}
