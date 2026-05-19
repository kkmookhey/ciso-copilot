"""GET /findings — paginated findings for the caller's tenant.

Query params:
  severity  — comma-separated subset of {critical, high, medium, low, info}
  status    — comma-separated subset of {fail, pass, not_assessed, not_applicable}; default 'fail'
  cloud     — 'aws' | 'azure' | 'entra' | 'gcp' (matches via conn_id join)
  limit     — default 50, max 200
  offset    — default 0

Response:
  {
    "findings": [...],
    "total": int,
    "limit": int,
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
ALLOWED_STATUSES   = {"fail", "pass", "not_assessed", "not_applicable"}
ALLOWED_CLOUDS     = {"aws", "azure", "entra", "gcp"}


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    qp = event.get("queryStringParameters") or {}

    severities = _parse_set(qp.get("severity"), ALLOWED_SEVERITIES) or list(ALLOWED_SEVERITIES)
    statuses   = _parse_set(qp.get("status"), ALLOWED_STATUSES) or ["fail"]
    cloud      = (qp.get("cloud") or "").lower() if qp.get("cloud") else None
    if cloud and cloud not in ALLOWED_CLOUDS:
        return _resp(400, {"error": "invalid_cloud"})
    check_id   = (qp.get("check_id") or "").strip() or None
    limit  = min(int(qp.get("limit",  "50") or 50), 200)
    offset = max(int(qp.get("offset", "0")  or 0),  0)

    sql = (
        "SELECT f.finding_id::text, f.check_id, f.title, f.description, f.severity, "
        "       f.status, f.resource_arn, f.resource_type, f.region, f.domain, "
        "       f.frameworks::text, f.remediation, f.first_seen::text, f.last_seen::text "
        "FROM findings f "
        + ("JOIN cloud_connections c ON c.conn_id = f.conn_id AND c.cloud_type = :cloud "
           if cloud else "")
        + "WHERE f.tenant_id = CAST(:tid AS UUID) "
        + f"  AND f.severity IN ({_in_clause('sev', severities)}) "
        + f"  AND f.status   IN ({_in_clause('st',  statuses)}) "
        + ("  AND f.check_id = :chk " if check_id else "")
        + "ORDER BY "
          "  CASE f.severity "
          "    WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 "
          "    WHEN 'low' THEN 4 ELSE 5 END, "
          "  f.last_seen DESC "
          "LIMIT :limit OFFSET :offset"
    )

    params = [{"name": "tid", "value": {"stringValue": tenant_id}}]
    if cloud:
        params.append({"name": "cloud", "value": {"stringValue": cloud}})
    for i, s in enumerate(severities):
        params.append({"name": f"sev{i}", "value": {"stringValue": s}})
    for i, s in enumerate(statuses):
        params.append({"name": f"st{i}", "value": {"stringValue": s}})
    if check_id:
        params.append({"name": "chk", "value": {"stringValue": check_id}})
    params.append({"name": "limit",  "value": {"longValue": limit}})
    params.append({"name": "offset", "value": {"longValue": offset}})

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params,
    )

    # Total count under the same filters (ignoring limit/offset) so the UI can
    # show "N open findings" without paging through all rows.
    count_sql = (
        "SELECT COUNT(*) FROM findings f "
        + ("JOIN cloud_connections c ON c.conn_id = f.conn_id AND c.cloud_type = :cloud "
           if cloud else "")
        + "WHERE f.tenant_id = CAST(:tid AS UUID) "
        + f"  AND f.severity IN ({_in_clause('sev', severities)}) "
        + f"  AND f.status   IN ({_in_clause('st',  statuses)})"
        + ("  AND f.check_id = :chk" if check_id else "")
    )
    count_params = [p for p in params if p["name"] not in ("limit", "offset")]
    count_rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=count_sql, parameters=count_params,
    )
    total = int(count_rs["records"][0][0].get("longValue", 0))

    findings = []
    for r in rs.get("records", []):
        findings.append({
            "finding_id":    r[0].get("stringValue"),
            "check_id":      r[1].get("stringValue"),
            "title":         r[2].get("stringValue"),
            "description":   r[3].get("stringValue") if not r[3].get("isNull") else None,
            "severity":      r[4].get("stringValue"),
            "status":        r[5].get("stringValue"),
            "resource_arn":  r[6].get("stringValue") if not r[6].get("isNull") else None,
            "resource_type": r[7].get("stringValue") if not r[7].get("isNull") else None,
            "region":        r[8].get("stringValue") if not r[8].get("isNull") else None,
            "domain":        r[9].get("stringValue"),
            "frameworks":    json.loads(r[10].get("stringValue") or "{}"),
            "remediation":   r[11].get("stringValue") if not r[11].get("isNull") else None,
            "first_seen":    r[12].get("stringValue"),
            "last_seen":     r[13].get("stringValue"),
        })

    return _resp(200, {
        "findings": findings,
        "limit":    limit,
        "offset":   offset,
        "count":    len(findings),  # this page
        "total":    total,           # all matches under the filters
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
