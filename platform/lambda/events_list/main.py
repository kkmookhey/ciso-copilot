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
    resource = event.get("resource", "")
    if resource == "/events":
        return _list_handler(event, context)
    if resource == "/events/{event_id}":
        return _detail_handler(event, context)
    if resource == "/events/{event_id}/feedback":
        return _feedback_handler(event, context)
    return _resp(404, {"error": "not_found"})


def _list_handler(event: dict, context) -> dict:
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
        "       resource_arn, actor, fired_at::text, ingested_at::text, "
        "       ai_narrative, ai_anomaly_class, ai_anomaly_score "
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
            "event_id":         r[0].get("stringValue"),
            "kind":             r[1].get("stringValue"),
            "source":           r[2].get("stringValue"),
            "severity":         r[3].get("stringValue"),
            "title":            r[4].get("stringValue"),
            "description":      _str_or_none(r[5]),
            "resource_arn":     _str_or_none(r[6]),
            "actor":            _str_or_none(r[7]),
            "fired_at":         r[8].get("stringValue"),
            "ingested_at":      r[9].get("stringValue"),
            "ai_narrative":     _str_or_none(r[10]),
            "ai_anomaly_class": _str_or_none(r[11]),
            "ai_anomaly_score": _int_or_none(r[12]),
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


def _detail_handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})
    event_id = (event.get("pathParameters") or {}).get("event_id")
    if not event_id:
        return _resp(400, {"error": "missing_event_id"})

    rows = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT e.event_id::text, e.kind, e.source, e.severity, e.title, e.description, "
            "       e.resource_arn, e.actor, e.fired_at::text, e.ingested_at::text, "
            "       e.ai_narrative, e.ai_anomaly_class, e.ai_anomaly_score, "
            "       e.ai_next_steps::text, e.ai_features::text, e.ai_model_version, "
            "       e.mitre_technique, d.action, d.after_state::text, d.before_state::text "
            "FROM events e LEFT JOIN drift_events d USING (event_id) "
            "WHERE e.event_id = CAST(:e AS UUID) AND e.tenant_id = CAST(:t AS UUID)"
        ),
        parameters=[
            {"name": "e", "value": {"stringValue": event_id}},
            {"name": "t", "value": {"stringValue": tenant_id}},
        ],
    ).get("records", [])
    if not rows:
        return _resp(404, {"error": "not_found"})
    r = rows[0]
    evt = {
        "event_id":         r[0]["stringValue"],
        "kind":             r[1]["stringValue"],
        "source":           r[2]["stringValue"],
        "severity":         r[3]["stringValue"],
        "title":            r[4]["stringValue"],
        "description":      _str_or_none(r[5]),
        "resource_arn":     _str_or_none(r[6]),
        "actor":            _str_or_none(r[7]),
        "fired_at":         r[8]["stringValue"],
        "ingested_at":      r[9]["stringValue"],
        "ai_narrative":     _str_or_none(r[10]),
        "ai_anomaly_class": _str_or_none(r[11]),
        "ai_anomaly_score": _int_or_none(r[12]),
        "ai_next_steps":    json.loads(r[13]["stringValue"]) if not r[13].get("isNull") else None,
        "ai_features":      json.loads(r[14]["stringValue"]) if not r[14].get("isNull") else None,
        "ai_model_version": _str_or_none(r[15]),
        "mitre_technique":  _str_or_none(r[16]),
        "action":           _str_or_none(r[17]),
        "after_state":      json.loads(r[18]["stringValue"]) if not r[18].get("isNull") else None,
        "before_state":     json.loads(r[19]["stringValue"]) if not r[19].get("isNull") else None,
    }

    related = []
    if evt["resource_arn"]:
        rs = rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=(
                "SELECT check_id, title, severity FROM findings "
                "WHERE tenant_id = CAST(:t AS UUID) AND resource_arn = :r "
                "  AND status = 'fail' ORDER BY severity LIMIT 10"
            ),
            parameters=[
                {"name": "t", "value": {"stringValue": tenant_id}},
                {"name": "r", "value": {"stringValue": evt["resource_arn"]}},
            ],
        )
        for fr in rs.get("records", []):
            related.append({
                "check_id": fr[0]["stringValue"],
                "title":    fr[1]["stringValue"],
                "severity": fr[2]["stringValue"],
            })

    return _resp(200, {"event": evt, "related_findings": related})


def _resolve_user_id(event: dict) -> str | None:
    """Resolve users.user_id (the FK target) from Cognito claims.

    For federated logins (Microsoft/Google), Cognito's 'sub' is the
    Cognito-user-pool sub, NOT the upstream IdP sub. users.sso_subject
    stores the upstream IdP sub (from identities[0].userId). Falls back
    to 'sub' for non-federated paths. Mirrors voice_session.
    """
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims", {})
    raw = claims.get("identities")
    sso_subject = None
    if raw:
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(ids, dict):
                ids = [ids]
            if ids:
                sso_subject = ids[0].get("userId")
        except (TypeError, ValueError):
            pass
    sso_subject = sso_subject or claims.get("sub")
    if not sso_subject:
        return None
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql="SELECT user_id::text FROM users WHERE sso_subject = :s LIMIT 1",
        parameters=[{"name": "s", "value": {"stringValue": sso_subject}}],
    )
    rows = rs.get("records", [])
    return rows[0][0].get("stringValue") if rows else None


def _feedback_handler(event: dict, context) -> dict:
    if event.get("httpMethod") != "POST":
        return _resp(405, {"error": "method_not_allowed"})
    tenant_id = _resolve_tenant_id(event)
    user_id   = _resolve_user_id(event)
    if not tenant_id or not user_id:
        print(f"feedback 401: tenant_id={'present' if tenant_id else 'MISSING'}, "
              f"user_id={'present' if user_id else 'MISSING'}")
        return _resp(401, {"error": "no_tenant_or_user"})

    event_id = (event.get("pathParameters") or {}).get("event_id")
    body     = json.loads(event.get("body") or "{}")
    sentiment = body.get("sentiment")
    reason    = body.get("reason")

    if sentiment not in ("up", "down"):
        return _resp(400, {"error": "invalid_sentiment"})

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO feedback (feedback_id, tenant_id, user_id, target_kind, target_id, sentiment, reason) "
            "VALUES (gen_random_uuid(), CAST(:t AS UUID), CAST(:u AS UUID), 'event', "
            "        CAST(:id AS UUID), :s, :r)"
        ),
        parameters=[
            {"name": "t",  "value": {"stringValue": tenant_id}},
            {"name": "u",  "value": {"stringValue": user_id}},
            {"name": "id", "value": {"stringValue": event_id}},
            {"name": "s",  "value": {"stringValue": sentiment}},
            {"name": "r",  "value": ({"stringValue": reason} if reason else {"isNull": True})},
        ],
    )
    return _resp(200, {"ok": True})


def _str_or_none(cell: dict) -> str | None:
    return cell.get("stringValue") if not cell.get("isNull") else None


def _int_or_none(cell: dict) -> int | None:
    return cell.get("longValue") if not cell.get("isNull") else None


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
