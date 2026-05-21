"""GET /findings/rollup — group failing findings by (domain, check_id).

For the redesigned Risks page. The flat /findings endpoint returns ~500 rows;
this rollup collapses them to ~30 distinct issues, each with a count of
affected resources.

Query params:
  severity, cloud, status — same semantics as /findings
  q                       — optional free-text search across check_id / title
  limit_per_group         — default 5, max 25. Sample resources per group.

Response:
  {
    "groups": [
      {
        "domain": "iam",
        "check_id": "iam-user-mfa",
        "title": "...",
        "severity": "critical",
        "count": 47,
        "frameworks": {"soc2": ["CC6.1"], "cis_aws": ["1.2"]},
        "sample_resources": [{"resource_arn": "arn:...", "region": "us-east-1"}, ...],
      }
    ],
    "total_findings": int,
    "total_groups":   int
  }
"""
from __future__ import annotations

import json
import os

import boto3

from check_titles import resolve_check_title

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "info"}
ALLOWED_STATUSES   = {"fail", "partial", "pass", "not_assessed", "not_applicable"}
ALLOWED_CLOUDS     = {"aws", "azure", "entra", "gcp"}

SEVERITY_RANK = {"critical": 1, "high": 2, "medium": 3, "low": 4, "info": 5}


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    qp = event.get("queryStringParameters") or {}
    severities = _parse_set(qp.get("severity"), ALLOWED_SEVERITIES) or list(ALLOWED_SEVERITIES)
    statuses   = _parse_set(qp.get("status"),   ALLOWED_STATUSES)   or ["fail"]
    cloud      = (qp.get("cloud") or "").lower() or None
    if cloud and cloud not in ALLOWED_CLOUDS:
        return _resp(400, {"error": "invalid_cloud"})
    q = (qp.get("q") or "").strip().lower()
    sample_limit = min(int(qp.get("limit_per_group", "5") or 5), 25)

    # One query fetches the rows we need to roll up; the aggregation happens
    # in Python because we also collect frameworks + sample resources.
    sql = (
        "SELECT f.check_id, f.title, f.severity, f.domain, f.resource_arn, "
        "       f.region, f.frameworks::text "
        "FROM findings f "
        + ("JOIN cloud_connections c ON c.conn_id = f.conn_id AND c.cloud_type = :cloud "
           if cloud else "")
        + "WHERE f.tenant_id = CAST(:tid AS UUID) "
        + f"  AND f.severity IN ({_in_clause('sev', severities)}) "
        + f"  AND f.status   IN ({_in_clause('st',  statuses)}) "
        + ("  AND (LOWER(f.title) LIKE :qpat OR LOWER(f.check_id) LIKE :qpat OR LOWER(f.description) LIKE :qpat) "
           if q else "")
    )

    params = [{"name": "tid", "value": {"stringValue": tenant_id}}]
    if cloud:
        params.append({"name": "cloud", "value": {"stringValue": cloud}})
    for i, s in enumerate(severities):
        params.append({"name": f"sev{i}", "value": {"stringValue": s}})
    for i, s in enumerate(statuses):
        params.append({"name": f"st{i}", "value": {"stringValue": s}})
    if q:
        params.append({"name": "qpat", "value": {"stringValue": f"%{q}%"}})

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params,
    )

    groups: dict[tuple[str, str], dict] = {}
    total_findings = 0
    for r in rs.get("records", []):
        check_id = r[0].get("stringValue")
        title    = r[1].get("stringValue")
        severity = r[2].get("stringValue")
        domain   = r[3].get("stringValue") or "other"
        arn      = r[4].get("stringValue") if not r[4].get("isNull") else None
        region   = r[5].get("stringValue") if not r[5].get("isNull") else None
        try:
            fw = json.loads(r[6].get("stringValue") or "{}")
        except json.JSONDecodeError:
            fw = {}

        key = (domain, check_id)
        g = groups.get(key)
        if g is None:
            g = {
                "domain":           domain,
                "check_id":         check_id,
                "title":            title,
                "check_title":      resolve_check_title(check_id, title),
                "severity":         severity,
                "count":            0,
                "frameworks":       fw,
                "sample_resources": [],
            }
            groups[key] = g

        # Worst-case severity wins
        if SEVERITY_RANK.get(severity, 99) < SEVERITY_RANK.get(g["severity"], 99):
            g["severity"] = severity
        g["count"] += 1
        total_findings += 1
        if len(g["sample_resources"]) < sample_limit and arn:
            g["sample_resources"].append({"resource_arn": arn, "region": region})

    out = list(groups.values())
    # Sort by impact: severity rank ascending, then count descending.
    out.sort(key=lambda g: (SEVERITY_RANK.get(g["severity"], 99), -g["count"]))

    return _resp(200, {
        "groups":         out,
        "total_findings": total_findings,
        "total_groups":   len(out),
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
