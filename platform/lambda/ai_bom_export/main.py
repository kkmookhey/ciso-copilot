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
from cyclonedx.output.json import JsonV1Dot6

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

_SUPPORTED_FORMATS = ["cyclonedx"]

# Map Shasta entity kind → CycloneDX ComponentType
# All AI/ML entity kinds map to MACHINE_LEARNING_MODEL; infrastructure-adjacent
# kinds (github_repo, docker_image) map to CONTAINER / LIBRARY as appropriate.
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
    """Task 1.2.5: filled below."""
    return []


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

    return bom


def _resp_json(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
