"""GET /compliance/summary — per-framework compliance score for the caller's tenant.

Aggregates findings by framework + control_id, then rolls up to a per-framework
{passing, failing, total, score_pct} summary. Modelled on Shasta's
compliance/scorer.py logic but operates directly against the DB.

A control "passes" if zero findings tagged to it have status='fail'; otherwise it
fails. Controls with no assessed findings are 'not_assessed' and excluded from
the score.

Response:
  {
    "summary": {
      "soc2":     {"total": int, "passing": int, "failing": int, "score_pct": float},
      "cis_aws":  {...},
      ...
    },
    "by_framework_control": [
      {"framework": "soc2", "control_id": "CC6.1", "fail_count": int, "pass_count": int, "total": int}
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

    # Unnest the frameworks JSONB into one row per (framework, control_id) per finding,
    # then aggregate.
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT fw.key AS framework, ctrl::text AS control_id, "
            "       COUNT(*) FILTER (WHERE f.status = 'fail') AS fail_count, "
            "       COUNT(*) FILTER (WHERE f.status = 'pass') AS pass_count, "
            "       COUNT(*) AS total "
            "FROM findings f, "
            "     jsonb_each(f.frameworks) AS fw(key, value), "
            "     jsonb_array_elements_text(fw.value) AS ctrl "
            "WHERE f.tenant_id = CAST(:tid AS UUID) "
            "GROUP BY fw.key, ctrl "
            "ORDER BY fw.key, ctrl"
        ),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )

    by_control = []
    framework_rollup: dict[str, dict] = {}

    for r in rs.get("records", []):
        framework  = r[0].get("stringValue")
        control_id = r[1].get("stringValue")
        fail_count = int(r[2].get("longValue", 0))
        pass_count = int(r[3].get("longValue", 0))
        total      = int(r[4].get("longValue", 0))

        by_control.append({
            "framework":  framework,
            "control_id": control_id,
            "fail_count": fail_count,
            "pass_count": pass_count,
            "total":      total,
        })

        fw = framework_rollup.setdefault(framework, {"controls": set(), "passing": set(), "failing": set()})
        fw["controls"].add(control_id)
        if fail_count > 0:
            fw["failing"].add(control_id)
        elif pass_count > 0:
            fw["passing"].add(control_id)
        # else: control has only not-fail, not-pass findings (e.g. not_assessed)

    summary = {}
    for framework, agg in framework_rollup.items():
        total   = len(agg["controls"])
        passing = len(agg["passing"])
        failing = len(agg["failing"])
        assessed = passing + failing
        score = (passing / assessed * 100) if assessed > 0 else 0.0
        summary[framework] = {
            "total":     total,
            "passing":   passing,
            "failing":   failing,
            "score_pct": round(score, 1),
        }

    return _resp(200, {
        "summary":               summary,
        "by_framework_control":  by_control,
    })


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
