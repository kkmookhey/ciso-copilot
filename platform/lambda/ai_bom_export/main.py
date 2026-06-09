"""GET /v1/ai/bom?format=cyclonedx — CycloneDX-ML 1.6 AI-BOM export."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3
from cyclonedx.model.bom import Bom
from cyclonedx.output.json import JsonV1Dot6

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

_SUPPORTED_FORMATS = ["cyclonedx"]


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
    """Task 1.2.3: filled below."""
    return []


def _select_ai_edges(tenant_id: str) -> list[dict]:
    """Task 1.2.4: filled below."""
    return []


def _select_ai_findings(tenant_id: str) -> list[dict]:
    """Task 1.2.5: filled below."""
    return []


def _build_bom(entities: list, edges: list, findings: list) -> Bom:
    """Tasks 1.2.3–1.2.5 enrich this."""
    return Bom()


def _resp_json(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
