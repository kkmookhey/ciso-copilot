"""GET /connections — list cloud connections for the caller's tenant.

Response:
  {
    "connections": [
      {
        "conn_id":     "uuid",
        "cloud_type":  "aws",
        "display_name": "...",
        "status":      "active" | "pending" | "error" | "revoked",
        "account_identifier": "<account_id or sub_id>",
        "signals":     {"pull_scan": bool, "alerts": bool, "drift": bool},
        "last_scan_at": "iso8601" | null,
        "created_at":   "iso8601"
      }, ...
    ]
  }
"""
from __future__ import annotations

import json
import os

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT conn_id::text, cloud_type, display_name, status, "
            "       account_identifier, signals::text, last_scan_at::text, created_at::text "
            "FROM cloud_connections "
            "WHERE tenant_id = CAST(:tid AS UUID) "
            "ORDER BY created_at DESC"
        ),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )

    connections = []
    for r in rs.get("records", []):
        connections.append({
            "conn_id":            r[0].get("stringValue"),
            "cloud_type":         r[1].get("stringValue"),
            "display_name":       r[2].get("stringValue"),
            "status":             r[3].get("stringValue"),
            "account_identifier": r[4].get("stringValue") if not r[4].get("isNull") else None,
            "signals":            json.loads(r[5].get("stringValue") or "{}"),
            "last_scan_at":       r[6].get("stringValue") if not r[6].get("isNull") else None,
            "created_at":         r[7].get("stringValue"),
        })

    return _resp(200, {"connections": connections})


def _resolve_tenant_id(event: dict) -> str | None:
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    raw = claims.get("identities")
    sso_subject = None
    if raw:
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(ids, dict):
                ids = [ids]
            if ids:
                sso_subject = ids[0].get("userId") or claims.get("sub")
        except (TypeError, ValueError):
            pass
    sso_subject = sso_subject or claims.get("sub")
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


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
