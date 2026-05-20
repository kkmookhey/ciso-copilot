"""Cloud-AI pass — wraps Shasta's AWS-AI discovery + checks into the
unified entity/edge/finding model.

Pure helpers (discovery_to_entities, ai_findings_to_emissions) take
already-fetched data and are unit-tested directly. run_ai_pass is the
orchestrator; it imports Shasta lazily so this module imports cleanly
in a test environment without Shasta installed.
"""
from __future__ import annotations

from typing import Any

from detectors.base import EdgeEmission, EntityEmission, FindingEmission

_DETECTOR_ID      = "shasta_runner.ai_pass"
_DETECTOR_VERSION = "0.1.0"


def discovery_to_entities(
    discovery: dict[str, Any], *, account_id: str, tenant_id: str,
) -> tuple[list[EntityEmission], list[EdgeEmission]]:
    """Map a Shasta discover_aws_ai_services() result to entities + edges.

    Each AI service becomes a domain='cloud' entity plus an
    aws_account --contains--> entity edge. Bedrock + lambda_ai lists are
    empty from Shasta today (key-name mismatch) and produce nothing.
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

    return entities, edges
