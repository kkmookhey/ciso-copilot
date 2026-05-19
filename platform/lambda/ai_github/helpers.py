"""Shared response + tenant-resolution helpers for the ai_github Lambda."""
from __future__ import annotations

import json
import os
from typing import Any

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN = os.environ["DB_SECRET_ARN"]
DB_NAME = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")


def resp(status: int, body: dict[str, Any]) -> dict[str, Any]:
    """API Gateway proxy response with the standard CORS headers."""
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
        },
        "body": json.dumps(body),
    }


def resolve_tenant_id(event: dict) -> str | None:
    """Look up tenant_id from the Cognito JWT in the API Gateway event."""
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    sso_subject = _subject_from_claims(claims)
    if not sso_subject:
        return None
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql="SELECT tenant_id::text FROM users WHERE sso_subject = :s LIMIT 1",
        parameters=[{"name": "s", "value": {"stringValue": sso_subject}}],
    )
    rows = rs.get("records", [])
    return rows[0][0].get("stringValue") if rows else None


def _subject_from_claims(claims: dict) -> str | None:
    raw = claims.get("identities")
    if raw:
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(ids, dict):
                ids = [ids]
            if ids:
                return ids[0].get("userId") or claims.get("sub")
        except (TypeError, ValueError):
            pass
    return claims.get("sub")
