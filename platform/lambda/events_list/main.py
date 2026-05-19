"""GET /events — paginated real-time events (alerts + drift) for the caller's tenant.

Query params:
  kind      — 'alert' | 'drift' (default both)
  severity  — comma-separated subset of {critical, high, medium, low, info}
  source    — comma-separated source filters (e.g. aws.guardduty,aws.cloudtrail)
  limit     — default 50, max 200
  offset    — default 0

Response:
  {
    "events": [...],
    "total":  int,
    "limit":  int,
    "offset": int
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

ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "info"}
ALLOWED_KINDS      = {"alert", "drift"}


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    qp = event.get("queryStringParameters") or {}

    severities = _parse_set(qp.get("severity"), ALLOWED_SEVERITIES) or list(ALLOWED_SEVERITIES)
    kinds      = _parse_set(qp.get("kind"),     ALLOWED_KINDS)      or list(ALLOWED_KINDS)
    sources    = [s.strip() for s in (qp.get("source") or "").split(",") if s.strip()]
    limit      = min(int(qp.get("limit",  "50") or 50), 200)
    offset     = max(int(qp.get("offset", "0")  or 0),  0)

    sql = (
        "SELECT event_id::text, kind, source, severity, title, description, "
        "       resource_arn, actor, fired_at::text, ingested_at::text "
        "FROM events "
        "WHERE tenant_id = CAST(:tid AS UUID) "
        f"  AND severity IN ({_in_clause('sev', severities)}) "
        f"  AND kind     IN ({_in_clause('k',   kinds)}) "
        + (f"  AND source IN ({_in_clause('src', sources)}) " if sources else "")
        + "ORDER BY fired_at DESC LIMIT :limit OFFSET :offset"
    )

    params = [{"name": "tid", "value": {"stringValue": tenant_id}}]
    for i, s in enumerate(severities):
        params.append({"name": f"sev{i}", "value": {"stringValue": s}})
    for i, k in enumerate(kinds):
        params.append({"name": f"k{i}", "value": {"stringValue": k}})
    for i, s in enumerate(sources):
        params.append({"name": f"src{i}", "value": {"stringValue": s}})
    params.append({"name": "limit",  "value": {"longValue": limit}})
    params.append({"name": "offset", "value": {"longValue": offset}})

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params,
    )

    events_out = []
    for r in rs.get("records", []):
        events_out.append({
            "event_id":     r[0].get("stringValue"),
            "kind":         r[1].get("stringValue"),
            "source":       r[2].get("stringValue"),
            "severity":     r[3].get("stringValue"),
            "title":        r[4].get("stringValue"),
            "description":  r[5].get("stringValue") if not r[5].get("isNull") else None,
            "resource_arn": r[6].get("stringValue") if not r[6].get("isNull") else None,
            "actor":        r[7].get("stringValue") if not r[7].get("isNull") else None,
            "fired_at":     r[8].get("stringValue"),
            "ingested_at":  r[9].get("stringValue"),
        })

    # Total under same filters (without limit/offset) for the home-screen Alerts stat.
    count_sql = (
        "SELECT COUNT(*) FROM events "
        "WHERE tenant_id = CAST(:tid AS UUID) "
        f"  AND severity IN ({_in_clause('sev', severities)}) "
        f"  AND kind     IN ({_in_clause('k',   kinds)}) "
        + (f"  AND source IN ({_in_clause('src', sources)})" if sources else "")
    )
    count_params = [p for p in params if p["name"] not in ("limit", "offset")]
    count_rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=count_sql, parameters=count_params,
    )
    total = int(count_rs["records"][0][0].get("longValue", 0))

    return _resp(200, {
        "events": events_out,
        "total":  total,
        "limit":  limit,
        "offset": offset,
    })


def _parse_set(raw: str | None, allowed: set[str]) -> list[str]:
    if not raw:
        return []
    return [s.strip().lower() for s in raw.split(",") if s.strip().lower() in allowed]


def _in_clause(prefix: str, values: list[str]) -> str:
    return ", ".join(f":{prefix}{i}" for i in range(len(values)))


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
