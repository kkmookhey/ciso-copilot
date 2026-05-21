"""GET /v1/scans/{scan_id} — scan progress for the web/iOS app.

Returns the scan's tier, status, phase, the coverage map (scope), and
finding counts so the app can render an in-progress scan and label
results by scan type. Cognito-authed; tenant-scoped.

Tenant resolution: mirrors the rest of the API — looks up tenant_id via
a DB join on the Cognito sub (or SSO userId from the identities claim).
There is no custom:tenant_id attribute in this Cognito pool.
"""
from __future__ import annotations

import json
import os

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")


def _resolve_tenant_id(event: dict) -> str | None:
    """Resolve the caller's tenant_id from the Cognito claims.

    Mirrors connections_list._resolve_tenant_id: the Cognito pool does not
    carry a custom:tenant_id attribute, so we derive it from the sub (or the
    SSO userId from the identities claim) via a users-table lookup.
    """
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


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    scan_id = (event.get("pathParameters") or {}).get("scan_id")
    if not tenant_id or not scan_id:
        return _resp(400, {"error": "missing_tenant_or_scan_id"})

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT tier, status, phase, scope, started_at, finished_at "
             "FROM scans WHERE scan_id = CAST(:sid AS UUID) "
             "AND tenant_id = CAST(:tid AS UUID)"),
        parameters=[
            {"name": "sid", "value": {"stringValue": scan_id}},
            {"name": "tid", "value": {"stringValue": tenant_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return _resp(404, {"error": "scan_not_found"})
    r = rows[0]

    fc = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT count(*) FROM findings "
             "WHERE scan_id = CAST(:sid AS UUID)"),
        parameters=[{"name": "sid", "value": {"stringValue": scan_id}}],
    )
    finding_count = fc["records"][0][0].get("longValue", 0)

    scope_raw = r[3].get("stringValue") if not r[3].get("isNull") else None
    return _resp(200, {
        "scan_id":       scan_id,
        "tier":          r[0].get("stringValue"),
        "status":        r[1].get("stringValue"),
        "phase":         r[2].get("stringValue"),
        "coverage_map":  json.loads(scope_raw) if scope_raw else None,
        "started_at":    r[4].get("stringValue"),
        "finished_at":   r[5].get("stringValue") if not r[5].get("isNull") else None,
        "finding_count": finding_count,
    })


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json",
                    "access-control-allow-origin": "*"},
        "body": json.dumps(body),
    }
