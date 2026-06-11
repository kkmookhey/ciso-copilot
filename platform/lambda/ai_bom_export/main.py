"""GET /v1/ai/bom?format=cyclonedx — CycloneDX-ML 1.6 AI-BOM export."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3
from cyclonedx.model import Property
from cyclonedx.model.bom import Bom
from cyclonedx.model.component import Component, ComponentType
from cyclonedx.model.dependency import Dependency
from cyclonedx.model.vulnerability import (
    BomTarget,
    Vulnerability,
    VulnerabilityRating,
    VulnerabilitySeverity,
    VulnerabilitySource,
)
from cyclonedx.output.json import JsonV1Dot6

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

_SUPPORTED_FORMATS = ["cyclonedx"]

# Map Shasta entity kind → CycloneDX ComponentType
# All AI/ML entity kinds map to MACHINE_LEARNING_MODEL; infrastructure-adjacent
# kinds (github_repo, docker_image) map to CONTAINER / LIBRARY as appropriate.
_SEVERITY_MAP: dict[str, VulnerabilitySeverity] = {
    "critical":      VulnerabilitySeverity.CRITICAL,
    "high":          VulnerabilitySeverity.HIGH,
    "medium":        VulnerabilitySeverity.MEDIUM,
    "low":           VulnerabilitySeverity.LOW,
    "informational": VulnerabilitySeverity.INFO,
    "info":          VulnerabilitySeverity.INFO,
}

_KIND_TO_TYPE: dict[str, ComponentType] = {
    "bedrock_model":       ComponentType.MACHINE_LEARNING_MODEL,
    "vertex_model":        ComponentType.MACHINE_LEARNING_MODEL,
    "openai_model":        ComponentType.MACHINE_LEARNING_MODEL,
    "azure_openai_model":  ComponentType.MACHINE_LEARNING_MODEL,
    "ai_framework":        ComponentType.LIBRARY,
    "github_repo":         ComponentType.LIBRARY,
    "docker_image":        ComponentType.CONTAINER,
}


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp_json(401, {"error": "no_tenant"})

    fmt = (event.get("queryStringParameters") or {}).get("format", "cyclonedx")
    if fmt not in _SUPPORTED_FORMATS:
        return _resp_json(400, {"error": "unknown_format", "supported": _SUPPORTED_FORMATS})

    entities = _select_ai_entities(tenant_id)
    edges    = _select_ai_edges(tenant_id)
    findings = _select_ai_findings(tenant_id)

    bom = _build_bom(entities, edges, findings)
    body = JsonV1Dot6(bom).output_as_string()

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/vnd.cyclonedx+json; version=1.6",
            "Content-Disposition": f'attachment; filename="shasta-ai-bom-{tenant_id}-{date_str}.cdx.json"',
            **_CORS_HEADERS,
        },
        "body": body,
    }


def _resolve_tenant_id(event: dict) -> str | None:
    """Canonical subject extraction: identities[0].userId first, fall back to sub."""
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {}) or {}
    )
    subject = None
    identities_raw = claims.get("identities")
    if identities_raw:
        try:
            identities = (
                json.loads(identities_raw)
                if isinstance(identities_raw, str)
                else identities_raw
            )
            if identities:
                subject = identities[0].get("userId")
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
    if not subject:
        subject = claims.get("sub")
    if not subject:
        return None

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql="SELECT tenant_id::text FROM users WHERE sso_subject = :s LIMIT 1",
        parameters=[{"name": "s", "value": {"stringValue": subject}}],
    )
    records = rs.get("records", [])
    if not records:
        return None
    return records[0][0].get("stringValue")


def _select_ai_entities(tenant_id: str) -> list[dict]:
    """Return AI-related entities for this tenant.

    Columns: id, kind, name, external_id, detector_id, discovered_at
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql="""
            SELECT id::text, kind, name, external_id, detector_id,
                   discovered_at::text
            FROM entities
            WHERE tenant_id = :tid
              AND kind IN (
                'bedrock_model', 'vertex_model', 'openai_model',
                'azure_openai_model', 'ai_framework', 'github_repo',
                'docker_image'
              )
            ORDER BY discovered_at DESC
        """,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    rows = rs.get("records", [])
    return [
        {
            "id":            row[0].get("stringValue", ""),
            "kind":          row[1].get("stringValue", ""),
            "name":          row[2].get("stringValue", ""),
            "external_id":   row[3].get("stringValue", ""),
            "detector_id":   row[4].get("stringValue", ""),
            "discovered_at": row[5].get("stringValue", ""),
        }
        for row in rows
    ]


def _select_ai_edges(tenant_id: str) -> list[dict]:
    """Return edges between AI entities for this tenant.

    Both source and target must be AI-kind entities (guard via JOIN).
    Columns: source_entity_id, target_entity_id, kind
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql="""
            SELECT e.source_entity_id::text,
                   e.target_entity_id::text,
                   e.kind
            FROM edges e
            JOIN entities src ON src.id = e.source_entity_id
                              AND src.tenant_id = :tid
                              AND src.kind IN (
                                    'bedrock_model', 'vertex_model', 'openai_model',
                                    'azure_openai_model', 'ai_framework',
                                    'github_repo', 'docker_image'
                                  )
            JOIN entities tgt ON tgt.id = e.target_entity_id
                              AND tgt.tenant_id = :tid
                              AND tgt.kind IN (
                                    'bedrock_model', 'vertex_model', 'openai_model',
                                    'azure_openai_model', 'ai_framework',
                                    'github_repo', 'docker_image'
                                  )
        """,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    rows = rs.get("records", [])
    return [
        {
            "source_entity_id": row[0].get("stringValue", ""),
            "target_entity_id": row[1].get("stringValue", ""),
            "kind":             row[2].get("stringValue", ""),
        }
        for row in rows
    ]


def _select_ai_findings(tenant_id: str) -> list[dict]:
    """Return AI-related findings for this tenant.

    FINDINGS.md §A.4: ai_supply_chain_matcher emits frameworks=[] (array)
    instead of {} (object). We handle this two ways:
    - The sca_vuln:% branch selects by check_id prefix so it catches rows
      regardless of frameworks shape.
    - The frameworks-object branch uses jsonb_typeof to safely query tags,
      but only for rows where frameworks IS an object.

    Columns: finding_id, check_id, status, conn_id, frameworks_raw
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql="""
            SELECT f.finding_id::text,
                   f.check_id,
                   f.severity,
                   COALESCE(f.subject_entity_id::text, ''),
                   f.frameworks::text
            FROM findings f
            JOIN cloud_connections cc ON cc.conn_id = f.conn_id
                                     AND cc.tenant_id = :tid
            WHERE f.status = 'fail'
              AND (
                    -- sca_vuln:* findings from ai_supply_chain_matcher
                    -- (may have bad-shape frameworks=[])
                    f.check_id LIKE 'sca_vuln:%'
                    OR
                    -- other AI findings that are properly tagged
                    (
                      jsonb_typeof(f.frameworks) = 'object'
                      AND f.frameworks ? 'owasp_llm_top10'
                    )
                  )
            ORDER BY f.finding_id
        """,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    rows = rs.get("records", [])
    return [
        {
            "finding_id":       row[0].get("stringValue", ""),
            "check_id":         row[1].get("stringValue", ""),
            "severity":         row[2].get("stringValue", ""),
            "subject_entity_id": row[3].get("stringValue", ""),
            "frameworks_raw":   row[4].get("stringValue", "{}"),
        }
        for row in rows
    ]


def _safe_frameworks(raw: str) -> dict:
    """Coerce frameworks JSON to a dict, regardless of shape.

    FINDINGS.md §A.4: ai_supply_chain_matcher writes frameworks=[] (array).
    Any non-dict value is normalised to {} so callers can safely call
    .get() / .items() without crashing.
    """
    try:
        parsed = json.loads(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _entity_to_component(e: dict) -> Component:
    """Map one Shasta AI entity dict → CycloneDX Component."""
    kind = e["kind"]
    comp_type = _KIND_TO_TYPE.get(kind, ComponentType.LIBRARY)
    props = [
        Property(name="shasta:kind",        value=kind),
        Property(name="shasta:detector_id", value=e["detector_id"]),
    ]
    if e.get("external_id"):
        props.append(Property(name="shasta:external_id", value=e["external_id"]))
    if e.get("discovered_at"):
        props.append(Property(name="shasta:discovered_at", value=e["discovered_at"]))
    return Component(
        type=comp_type,
        bom_ref=e["id"],
        name=e["name"],
        properties=props,
    )


def _finding_to_vulnerability(f: dict) -> Vulnerability:
    """Map one Shasta AI finding dict → CycloneDX Vulnerability."""
    severity_str = f.get("severity", "medium")
    sev = _SEVERITY_MAP.get(severity_str.lower(), VulnerabilitySeverity.MEDIUM)
    rating = VulnerabilityRating(
        source=VulnerabilitySource(name="shasta"),
        severity=sev,
    )
    affects = []
    entity_id = f.get("subject_entity_id", "")
    if entity_id:
        affects.append(BomTarget(ref=entity_id))

    return Vulnerability(
        bom_ref=f["finding_id"],
        id=f["check_id"],
        ratings=[rating],
        affects=affects if affects else None,
    )


def _build_bom(entities: list, edges: list, findings: list) -> Bom:
    """Assemble a CycloneDX Bom from Shasta AI inventory data."""
    bom = Bom()

    # 1.2.3 — entity → component
    comp_map: dict[str, Component] = {}
    for entity in entities:
        comp = _entity_to_component(entity)
        bom.components.add(comp)
        comp_map[entity["id"]] = comp

    # 1.2.4 — edge → dependency
    # Group edges by source so we can emit one Dependency per source node.
    targets_by_source: dict[str, list[str]] = {}
    for edge in edges:
        src = edge["source_entity_id"]
        tgt = edge["target_entity_id"]
        # Only emit if both nodes are in our component map.
        if src in comp_map and tgt in comp_map:
            targets_by_source.setdefault(src, []).append(tgt)

    for src_id, tgt_ids in targets_by_source.items():
        src_comp = comp_map[src_id]
        dep = Dependency(
            ref=src_comp.bom_ref,
            dependencies=[
                Dependency(ref=comp_map[t].bom_ref)
                for t in tgt_ids
            ],
        )
        bom.dependencies.add(dep)

    # 1.2.5 — finding → vulnerability
    # _safe_frameworks coerces any bad-shape data (FINDINGS.md §A.4) to {}.
    for finding in findings:
        _ = _safe_frameworks(finding.get("frameworks_raw", "{}"))  # validate; not used in Slice 1
        vuln = _finding_to_vulnerability(finding)
        bom.vulnerabilities.add(vuln)

    return bom


_CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
}


def _resp_json(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **_CORS_HEADERS},
        "body": json.dumps(body),
    }
