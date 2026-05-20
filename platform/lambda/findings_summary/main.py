"""GET /findings/summary — aggregate counts for dashboards.

Returns counts grouped by status, severity, and cloud.

  - `by_status` counts every finding (fail / partial / pass) — drives the
    dashboard status tiles.
  - `by_severity` / `by_cloud` count the *actionable* set (fail + partial)
    — drive the risk-distribution donut and the by-cloud bar.

Response:
  {
    "by_status":   { "fail": int, "partial": int, "pass": int },
    "by_severity": { "critical": int, "high": int, "medium": int, "low": int, "info": int },
    "by_cloud":    { "aws": int, "azure": int, "gcp": int, "entra": int },
    "total":       int   # actionable total (fail + partial)
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

# Findings that represent work to do. pass is reported but not "actionable".
ACTIONABLE = ("fail", "partial")


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    by_status   = _agg(tenant_id, "f.status")
    by_severity = _agg(tenant_id, "f.severity", statuses=ACTIONABLE)
    by_cloud    = _agg(tenant_id, "c.cloud_type", join_cloud=True, statuses=ACTIONABLE)
    total = sum(by_severity.values())

    return _resp(200, {
        "by_status": {
            "fail":    by_status.get("fail", 0),
            "partial": by_status.get("partial", 0),
            "pass":    by_status.get("pass", 0),
        },
        "by_severity": {
            "critical": by_severity.get("critical", 0),
            "high":     by_severity.get("high", 0),
            "medium":   by_severity.get("medium", 0),
            "low":      by_severity.get("low", 0),
            "info":     by_severity.get("info", 0),
        },
        "by_cloud": {
            "aws":   by_cloud.get("aws", 0),
            "azure": by_cloud.get("azure", 0),
            "gcp":   by_cloud.get("gcp", 0),
            "entra": by_cloud.get("entra", 0),
        },
        "total": total,
    })


def _agg(tenant_id: str, group_col: str, *,
         join_cloud: bool = False,
         statuses: tuple[str, ...] | None = None) -> dict[str, int]:
    # `statuses` values are code-controlled literals (never user input).
    status_clause = ""
    if statuses:
        in_list = ", ".join(f"'{s}'" for s in statuses)
        status_clause = f"AND f.status IN ({in_list}) "
    sql = (
        f"SELECT {group_col} AS k, COUNT(*) AS n "
        "FROM findings f "
        + ("JOIN cloud_connections c ON c.conn_id = f.conn_id " if join_cloud else "")
        + "WHERE f.tenant_id = CAST(:tid AS UUID) "
        + status_clause
        + f"GROUP BY {group_col}"
    )
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    return {
        r[0].get("stringValue"): int(r[1].get("longValue", 0))
        for r in rs.get("records", [])
    }


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
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
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
