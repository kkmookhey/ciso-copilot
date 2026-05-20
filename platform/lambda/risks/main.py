"""Risks API (lifted from Shasta's risk register pattern).

  GET    /risks               — list (filters: status, severity)
  POST   /risks               — create. Body: {title, description?, severity, owner?, due_date?, finding_id?, notes?}
  PATCH  /risks/{id}          — update. Body: {status?, owner?, due_date?, notes?}

Single Lambda routes by httpMethod + pathParameters.
"""
from __future__ import annotations

import json
import os
import uuid

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "info"}
ALLOWED_STATUSES   = {"open", "mitigated", "accepted", "transferred", "closed"}


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    method = event.get("httpMethod", "GET")
    risk_id = (event.get("pathParameters") or {}).get("id")

    if method == "GET":
        return _list(tenant_id, event)
    if method == "POST":
        return _create(tenant_id, _body(event))
    if method == "PATCH" and risk_id:
        return _update(tenant_id, risk_id, _body(event))
    return _resp(400, {"error": "unsupported"})


def _list(tenant_id: str, event: dict) -> dict:
    qp = event.get("queryStringParameters") or {}
    status   = qp.get("status")
    severity = qp.get("severity")

    sql = ("SELECT risk_id::text, title, description, severity, status, owner, "
           "       due_date::text, finding_id::text, notes, created_at::text, updated_at::text "
           "FROM risks WHERE tenant_id = CAST(:tid AS UUID)")
    params = [{"name": "tid", "value": {"stringValue": tenant_id}}]
    if status and status in ALLOWED_STATUSES:
        sql += " AND status = :s"
        params.append({"name": "s", "value": {"stringValue": status}})
    if severity and severity in ALLOWED_SEVERITIES:
        sql += " AND severity = :sev"
        params.append({"name": "sev", "value": {"stringValue": severity}})
    sql += (" ORDER BY CASE severity "
            "  WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 "
            "  WHEN 'low' THEN 4 ELSE 5 END, "
            "  COALESCE(due_date, '9999-12-31'::date), created_at DESC")

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params,
    )

    risks = [_row_to_dict(r) for r in rs.get("records", [])]
    return _resp(200, {"risks": risks, "count": len(risks)})


def _create(tenant_id: str, body: dict) -> dict:
    title    = (body.get("title") or "").strip()
    severity = (body.get("severity") or "medium").lower()
    if not title:
        return _resp(400, {"error": "title_required"})
    if severity not in ALLOWED_SEVERITIES:
        return _resp(400, {"error": "invalid_severity", "allowed": list(ALLOWED_SEVERITIES)})

    source_approval_id = body.get("source_approval_id")

    risk_id = str(uuid.uuid4())

    params = [
        {"name": "rid",   "value": {"stringValue": risk_id}},
        {"name": "tid",   "value": {"stringValue": tenant_id}},
        {"name": "title", "value": {"stringValue": title[:500]}},
        {"name": "sev",   "value": {"stringValue": severity}},
    ]
    optional_cols: list[str] = []
    optional_vals: list[str] = []
    if body.get("description"):
        optional_cols.append("description"); optional_vals.append(":desc")
        params.append({"name": "desc", "value": {"stringValue": body["description"][:5000]}})
    if body.get("owner"):
        optional_cols.append("owner"); optional_vals.append(":owner")
        params.append({"name": "owner", "value": {"stringValue": body["owner"][:200]}})
    if body.get("due_date"):
        optional_cols.append("due_date"); optional_vals.append("CAST(:due AS DATE)")
        params.append({"name": "due", "value": {"stringValue": body["due_date"]}})
    if body.get("finding_id"):
        optional_cols.append("finding_id"); optional_vals.append("CAST(:fid AS UUID)")
        params.append({"name": "fid", "value": {"stringValue": body["finding_id"]}})
    if body.get("notes"):
        optional_cols.append("notes"); optional_vals.append(":notes")
        params.append({"name": "notes", "value": {"stringValue": body["notes"][:5000]}})

    cols_sql = ", ".join(["risk_id", "tenant_id", "title", "severity"] + optional_cols)
    vals_sql = ", ".join(["CAST(:rid AS UUID)", "CAST(:tid AS UUID)", ":title", ":sev"] + optional_vals)

    if source_approval_id:
        # Atomic idempotency via ON CONFLICT: the INSERT either creates the row
        # or no-ops if (tenant_id, source_approval_id) already exists. If it
        # no-ops (RETURNING is empty), fetch the winner row. This eliminates
        # the TOCTOU window that a SELECT-then-INSERT pattern has.
        cols_sql += ", source_approval_id"
        vals_sql += ", CAST(:said AS UUID)"
        params.append({"name": "said", "value": {"stringValue": source_approval_id}})

        rs = rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=(f"INSERT INTO risks ({cols_sql}) VALUES ({vals_sql}) "
                 "ON CONFLICT (tenant_id, source_approval_id) DO NOTHING "
                 "RETURNING risk_id::text, status"),
            parameters=params,
        )
        rows = rs.get("records", [])
        if rows:
            # Fresh insert succeeded — return the new row.
            new_id = rows[0][0].get("stringValue")
            new_st = rows[0][1].get("stringValue")
            return _resp(200, {"risk_id": new_id, "status": new_st})

        # Conflict: another concurrent request already inserted this row.
        # Fetch and return the winner.
        rs2 = rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=("SELECT risk_id::text, status FROM risks "
                 "WHERE tenant_id = CAST(:tid AS UUID) "
                 "AND source_approval_id = CAST(:said AS UUID) LIMIT 1"),
            parameters=[
                {"name": "tid",  "value": {"stringValue": tenant_id}},
                {"name": "said", "value": {"stringValue": source_approval_id}},
            ],
        )
        existing = rs2.get("records", [])
        if existing:
            existing_id = existing[0][0].get("stringValue")
            existing_st = existing[0][1].get("stringValue")
            print(f"INFO: idempotent risk create — returning existing risk_id={existing_id}")
            return _resp(200, {"risk_id": existing_id, "status": existing_st})
        # Should never reach here, but fall through to plain insert as safety net.

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=f"INSERT INTO risks ({cols_sql}) VALUES ({vals_sql})",
        parameters=params,
    )
    return _resp(200, {"risk_id": risk_id, "status": "open"})


def _update(tenant_id: str, risk_id: str, body: dict) -> dict:
    sets: list[str] = []
    params = [
        {"name": "rid", "value": {"stringValue": risk_id}},
        {"name": "tid", "value": {"stringValue": tenant_id}},
    ]
    if "status" in body:
        status = (body["status"] or "").lower()
        if status not in ALLOWED_STATUSES:
            return _resp(400, {"error": "invalid_status"})
        sets.append("status = :st")
        params.append({"name": "st", "value": {"stringValue": status}})
    if "owner" in body:
        sets.append("owner = :ow")
        params.append({"name": "ow", "value": {"stringValue": (body["owner"] or "")[:200]}})
    if "due_date" in body:
        if body["due_date"] is None:
            sets.append("due_date = NULL")
        else:
            sets.append("due_date = CAST(:due AS DATE)")
            params.append({"name": "due", "value": {"stringValue": body["due_date"]}})
    if "notes" in body:
        sets.append("notes = :notes")
        params.append({"name": "notes", "value": {"stringValue": (body["notes"] or "")[:5000]}})
    if not sets:
        return _resp(400, {"error": "no_fields_to_update"})

    sets.append("updated_at = now()")
    sql = (f"UPDATE risks SET {', '.join(sets)} "
           f"WHERE risk_id = CAST(:rid AS UUID) AND tenant_id = CAST(:tid AS UUID)")
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params,
    )
    if rs.get("numberOfRecordsUpdated", 0) == 0:
        return _resp(404, {"error": "risk_not_found"})
    return _resp(200, {"updated": True})


def _row_to_dict(r: list) -> dict:
    return {
        "risk_id":     r[0].get("stringValue"),
        "title":       r[1].get("stringValue"),
        "description": r[2].get("stringValue") if not r[2].get("isNull") else None,
        "severity":    r[3].get("stringValue"),
        "status":      r[4].get("stringValue"),
        "owner":       r[5].get("stringValue") if not r[5].get("isNull") else None,
        "due_date":    r[6].get("stringValue") if not r[6].get("isNull") else None,
        "finding_id":  r[7].get("stringValue") if not r[7].get("isNull") else None,
        "notes":       r[8].get("stringValue") if not r[8].get("isNull") else None,
        "created_at":  r[9].get("stringValue"),
        "updated_at":  r[10].get("stringValue"),
    }


def _body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


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
