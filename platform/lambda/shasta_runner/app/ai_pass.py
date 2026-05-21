"""Cloud-AI pass — wraps Shasta's AWS-AI discovery + checks into the
unified entity/edge/finding model.

Pure helpers (discovery_to_entities, ai_findings_to_emissions) take
already-fetched data and are unit-tested directly. run_ai_pass is the
orchestrator; it imports Shasta lazily so this module imports cleanly
in a test environment without Shasta installed.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from detectors.base import EdgeEmission, EntityEmission, FindingEmission

logger = logging.getLogger(__name__)

_DETECTOR_ID      = "shasta_runner.ai_pass"
_DETECTOR_VERSION = "0.1.0"

# Environment-variable name fragments that indicate AI provider credentials.
_AI_ENV_VAR_RE = re.compile(
    r"OPENAI|ANTHROPIC|CLAUDE|COHERE|HUGGING.?FACE|HF_TOKEN|REPLICATE|"
    r"GOOGLE_AI|GEMINI|PALM_API|VERTEX_AI|STABILITY|AI21|MISTRAL|TOGETHER|"
    r"GROQ|PERPLEXITY|DEEPSEEK|XAI_|GROK|BEDROCK|LANGCHAIN|LANGSMITH",
    re.IGNORECASE,
)

# Standard (non-AI) framework attributes on a Shasta Finding.
_STD_FRAMEWORK_ATTRS = {
    "soc2_controls":     "soc2",
    "cis_aws_controls":  "cis_aws",
    "iso27001_controls": "iso27001",
    "hipaa_controls":    "hipaa",
    "mcsb_controls":     "mcsb",
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


def _match_ai_env_vars(env_vars: dict[str, str]) -> list[str]:
    """Names of environment variables that look like AI provider credentials."""
    return [name for name in env_vars if _AI_ENV_VAR_RE.search(name)]


def discovery_to_entities(
    discovery: dict[str, Any], *, account_id: str, tenant_id: str,
) -> tuple[list[EntityEmission], list[EdgeEmission]]:
    """Map an AWS-AI discovery result to entities + edges.

    Each AI service becomes a domain='cloud' entity plus an
    aws_account --contains--> entity edge. Bedrock guardrails and
    AI-using Lambda functions come from discover_bedrock_and_ai_lambdas()
    (Shasta's discover_aws_ai_services drops them); the Bedrock
    foundation-model catalog is intentionally not emitted — it is the
    same AWS-wide catalog for every account, not customer inventory.
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
            source_kind="aws_account", source_natural_key=account_id,
            target_kind=kind, target_natural_key=natural_key,
            kind="contains", attributes={},
            evidence_packet={"version": "0.1", "via": "ai_discovery"},
            detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
        ))

    sm = discovery.get("sagemaker", {})
    for ep in sm.get("endpoints", []):
        name = ep.get("name", "")
        if name:
            _emit("sagemaker_endpoint", f"sagemaker:endpoint/{name}", name,
                  {"status": ep.get("status", ""),
                   "creation_time": ep.get("creation_time", "")})
    for m in sm.get("models", []):
        name = m.get("name", "")
        if name:
            _emit("sagemaker_model", f"sagemaker:model/{name}", name,
                  {"creation_time": m.get("creation_time", "")})
    for tj in sm.get("training_jobs", []):
        name = tj.get("name", "")
        if name:
            _emit("sagemaker_training_job", f"sagemaker:training-job/{name}", name,
                  {"status": tj.get("status", ""),
                   "creation_time": tj.get("creation_time", "")})

    for ce in discovery.get("comprehend", {}).get("endpoints", []):
        arn = ce.get("arn", "")
        if arn:
            _emit("comprehend_endpoint", arn, arn.rsplit("/", 1)[-1] or arn,
                  {"status": ce.get("status", ""),
                   "model_arn": ce.get("model_arn", "")})

    for g in discovery.get("bedrock", {}).get("guardrails", []):
        arn = g.get("arn", "")
        if arn:
            _emit("bedrock_guardrail", arn,
                  g.get("name", "") or arn.rsplit("/", 1)[-1] or arn,
                  {"status": g.get("status", ""),
                   "region": g.get("region", ""),
                   "guardrail_id": g.get("id", "")})

    for fn in discovery.get("lambda_ai", {}).get("functions_with_ai_vars", []):
        arn = fn.get("function_arn", "")
        if arn:
            _emit("lambda_ai_function", arn,
                  fn.get("function_name", "") or arn,
                  {"runtime": fn.get("runtime", ""),
                   "region": fn.get("region", ""),
                   "ai_env_vars": fn.get("ai_env_vars", [])})

    return entities, edges


def ai_findings_to_emissions(
    findings: list[Any], *, tenant_id: str,
) -> list[FindingEmission]:
    """Map Shasta AI-check Findings (already enriched via
    enrich_findings_with_ai_controls) to FindingEmission rows, pulling
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


def discover_bedrock_and_ai_lambdas(client: Any) -> dict[str, Any]:
    """Discover Bedrock guardrails and AI-using Lambda functions across all
    enabled regions, via boto3 directly.

    Done here rather than through Shasta's discover_aws_ai_services, whose
    aggregator drops both lists on a key-name mismatch. The Bedrock
    foundation-model catalog is deliberately not collected — it is AWS's
    account-wide catalog, identical for every tenant, not their inventory.
    """
    guardrails: list[dict[str, Any]] = []
    functions:  list[dict[str, Any]] = []

    try:
        regions = list(client.get_enabled_regions())
    except Exception as e:
        default = getattr(getattr(client, "account_info", None), "region", None)
        logger.warning("ai_pass: get_enabled_regions failed (%s); using %s",
                        e, default or "us-east-1")
        regions = [default or "us-east-1"]

    for region in regions:
        rc = client.for_region(region)

        try:
            resp = rc.client("bedrock").list_guardrails(maxResults=100)
            for g in resp.get("guardrails", []):
                guardrails.append({
                    "id":     g.get("id", ""),
                    "arn":    g.get("arn", ""),
                    "name":   g.get("name", ""),
                    "status": g.get("status", ""),
                    "region": region,
                })
        except Exception as e:
            logger.debug("ai_pass: bedrock list_guardrails failed in %s: %s",
                         region, e)

        try:
            lam = rc.client("lambda")
            for page in lam.get_paginator("list_functions").paginate(MaxItems=500):
                for fn in page.get("Functions", []):
                    env = (fn.get("Environment", {}) or {}).get("Variables", {}) or {}
                    matched = _match_ai_env_vars(env)
                    if matched:
                        functions.append({
                            "function_name": fn.get("FunctionName", ""),
                            "function_arn":  fn.get("FunctionArn", ""),
                            "runtime":       fn.get("Runtime", ""),
                            "region":        region,
                            "ai_env_vars":   matched,
                        })
        except Exception as e:
            logger.debug("ai_pass: lambda discovery failed in %s: %s", region, e)

    return {
        "bedrock":   {"available": bool(guardrails), "guardrails": guardrails},
        "lambda_ai": {"available": bool(functions),
                      "functions_with_ai_vars": functions},
    }


def run_ai_pass(client: Any, *, account_id: str, tenant_id: str) -> dict[str, list]:
    """Run Shasta's AWS-AI discovery + checks against an assumed-role client
    and return unified emissions. Shasta is imported lazily so this module
    stays importable in test environments without Shasta installed."""
    from shasta.aws.ai_discovery import discover_aws_ai_services
    from shasta.aws.ai_checks import run_full_aws_ai_scan
    from shasta.compliance.ai.mapper import enrich_findings_with_ai_controls

    discovery = discover_aws_ai_services(client)
    # Shasta's aggregator drops Bedrock guardrails + AI-Lambda functions;
    # discover them directly and merge into the discovery dict.
    extra = discover_bedrock_and_ai_lambdas(client)
    discovery.setdefault("bedrock", {}).update(extra["bedrock"])
    discovery.setdefault("lambda_ai", {}).update(extra["lambda_ai"])
    entities, edges = discovery_to_entities(
        discovery, account_id=account_id, tenant_id=tenant_id,
    )

    findings = run_full_aws_ai_scan(client)
    enrich_findings_with_ai_controls(findings)
    finding_emissions = ai_findings_to_emissions(findings, tenant_id=tenant_id)

    return {"entities": entities, "edges": edges, "findings": finding_emissions}
