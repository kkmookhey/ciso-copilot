"""Trust center API.

  AUTHED (Cognito-gated):
    GET  /trust              — get my tenant's trust page settings
    PUT  /trust              — create/update settings (slug, flags, notes)

  UNAUTHED (public):
    GET  /public/trust/{slug} — read-only redacted posture summary

Redaction rules:
  - Never expose: specific resource ARNs, account IDs, individual finding
    titles/descriptions, IAM users/roles, IPs.
  - Always exposed: aggregate counts (total open, by severity), framework
    score percentages, last-scan timestamp, count of connected clouds.
"""
from __future__ import annotations

import json
import os
import re
import uuid

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}[a-z0-9]$")


def handler(event: dict, context) -> dict:
    method = event.get("httpMethod", "GET")
    path = event.get("path") or ""
    path_params = event.get("pathParameters") or {}

    # UNAUTHED public page
    if method == "GET" and "/public/trust/" in path:
        slug = path_params.get("slug")
        if not slug:
            return _resp(400, {"error": "missing_slug"})
        return _public_page(slug)

    # AUTHED admin
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    if method == "GET":
        return _get_settings(tenant_id)
    if method == "PUT":
        return _put_settings(tenant_id, _body(event))
    return _resp(400, {"error": "unsupported"})


# ============================================================================
# Public page
# ============================================================================

def _public_page(slug: str) -> dict:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT tenant_id::text, public_name, notes, show_compliance, "
             "       show_finding_counts, show_clouds, show_last_scan, updated_at::text "
             "FROM trust_pages WHERE slug = :s AND is_published = true"),
        parameters=[{"name": "s", "value": {"stringValue": slug}}],
    )
    rows = rs.get("records", [])
    if not rows:
        return _resp(404, {"error": "not_found"})
    r = rows[0]
    tenant_id = r[0].get("stringValue")
    page = {
        "name":        r[1].get("stringValue"),
        "notes":       r[2].get("stringValue") if not r[2].get("isNull") else None,
        "updated_at":  r[7].get("stringValue"),
    }

    if r[3].get("booleanValue"):
        page["compliance"] = _aggregate_compliance(tenant_id)
    if r[4].get("booleanValue"):
        page["findings"] = _aggregate_findings(tenant_id)
    if r[5].get("booleanValue"):
        page["clouds"] = _aggregate_clouds(tenant_id)
    if r[6].get("booleanValue"):
        page["last_scan"] = _last_scan(tenant_id)

    return _resp(200, page)


def _aggregate_compliance(tenant_id: str) -> dict:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT fw.key AS framework, ctrl::text AS control_id, "
             "       COUNT(*) FILTER (WHERE f.status = 'fail') AS fail_count, "
             "       COUNT(*) FILTER (WHERE f.status = 'pass') AS pass_count "
             "FROM findings f, "
             "     jsonb_each(f.frameworks) AS fw(key, value), "
             "     jsonb_array_elements_text(fw.value) AS ctrl "
             "WHERE f.tenant_id = CAST(:tid AS UUID) "
             "GROUP BY fw.key, ctrl"),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    framework_rollup: dict[str, dict] = {}
    for r in rs.get("records", []):
        fw = r[0].get("stringValue")
        ctrl = r[1].get("stringValue")
        fail = int(r[2].get("longValue", 0))
        pas  = int(r[3].get("longValue", 0))
        agg = framework_rollup.setdefault(fw, {"controls": set(), "passing": set(), "failing": set()})
        agg["controls"].add(ctrl)
        if fail > 0:
            agg["failing"].add(ctrl)
        elif pas > 0:
            agg["passing"].add(ctrl)

    summary = {}
    for fw, agg in framework_rollup.items():
        total = len(agg["controls"])
        passing = len(agg["passing"])
        failing = len(agg["failing"])
        assessed = passing + failing
        score = round(passing / assessed * 100, 1) if assessed > 0 else 0.0
        summary[fw] = {"score_pct": score, "passing": passing, "failing": failing, "total": total}
    return summary


def _aggregate_findings(tenant_id: str) -> dict:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT severity, COUNT(*) FROM findings "
             "WHERE tenant_id = CAST(:tid AS UUID) AND status = 'fail' "
             "GROUP BY severity"),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for r in rs.get("records", []):
        sev = r[0].get("stringValue")
        if sev in counts:
            counts[sev] = int(r[1].get("longValue", 0))
    return {"by_severity": counts, "total": sum(counts.values())}


def _aggregate_clouds(tenant_id: str) -> dict:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT cloud_type, COUNT(*) FROM cloud_connections "
             "WHERE tenant_id = CAST(:tid AS UUID) AND status = 'active' "
             "GROUP BY cloud_type"),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    clouds: dict[str, int] = {}
    total = 0
    for r in rs.get("records", []):
        cloud = r[0].get("stringValue")
        n = int(r[1].get("longValue", 0))
        clouds[cloud] = n
        total += n
    return {"by_cloud": clouds, "total": total}


def _last_scan(tenant_id: str) -> str | None:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT MAX(finished_at)::text FROM scans s "
             "JOIN cloud_connections c ON c.conn_id = s.conn_id "
             "WHERE c.tenant_id = CAST(:tid AS UUID) AND s.status = 'completed'"),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    rows = rs.get("records", [])
    if not rows:
        return None
    val = rows[0][0]
    return val.get("stringValue") if not val.get("isNull") else None


# ============================================================================
# Admin (authed)
# ============================================================================

def _get_settings(tenant_id: str) -> dict:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT page_id::text, slug, public_name, notes, is_published, "
             "       show_compliance, show_finding_counts, show_clouds, show_last_scan, "
             "       created_at::text, updated_at::text "
             "FROM trust_pages WHERE tenant_id = CAST(:t AS UUID)"),
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    rows = rs.get("records", [])
    if not rows:
        return _resp(200, {"page": None})
    r = rows[0]
    return _resp(200, {"page": {
        "page_id":              r[0].get("stringValue"),
        "slug":                 r[1].get("stringValue"),
        "public_name":          r[2].get("stringValue"),
        "notes":                r[3].get("stringValue") if not r[3].get("isNull") else None,
        "is_published":         r[4].get("booleanValue"),
        "show_compliance":      r[5].get("booleanValue"),
        "show_finding_counts":  r[6].get("booleanValue"),
        "show_clouds":          r[7].get("booleanValue"),
        "show_last_scan":       r[8].get("booleanValue"),
        "created_at":           r[9].get("stringValue"),
        "updated_at":           r[10].get("stringValue"),
    }})


def _put_settings(tenant_id: str, body: dict) -> dict:
    slug = (body.get("slug") or "").strip().lower()
    if not SLUG_RE.match(slug):
        return _resp(400, {"error": "invalid_slug", "rule": "3-62 chars: a-z, 0-9, hyphen; must start/end alphanumeric"})
    public_name = (body.get("public_name") or "").strip()
    if not public_name:
        return _resp(400, {"error": "public_name_required"})

    # Check slug collision with another tenant.
    coll = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql="SELECT 1 FROM trust_pages WHERE slug = :s AND tenant_id <> CAST(:t AS UUID)",
        parameters=[
            {"name": "s", "value": {"stringValue": slug}},
            {"name": "t", "value": {"stringValue": tenant_id}},
        ],
    )
    if coll.get("records"):
        return _resp(409, {"error": "slug_taken"})

    page_id = str(uuid.uuid4())
    params = [
        {"name": "t",    "value": {"stringValue": tenant_id}},
        {"name": "p",    "value": {"stringValue": page_id}},
        {"name": "s",    "value": {"stringValue": slug}},
        {"name": "name", "value": {"stringValue": public_name[:200]}},
        {"name": "notes","value": ({"stringValue": body["notes"][:5000]} if body.get("notes") else {"isNull": True})},
        {"name": "pub",  "value": {"booleanValue": bool(body.get("is_published", False))}},
        {"name": "sc",   "value": {"booleanValue": bool(body.get("show_compliance", True))}},
        {"name": "sf",   "value": {"booleanValue": bool(body.get("show_finding_counts", True))}},
        {"name": "sx",   "value": {"booleanValue": bool(body.get("show_clouds", True))}},
        {"name": "sl",   "value": {"booleanValue": bool(body.get("show_last_scan", True))}},
    ]

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("INSERT INTO trust_pages (page_id, tenant_id, slug, public_name, notes, "
             "                          is_published, show_compliance, show_finding_counts, "
             "                          show_clouds, show_last_scan) "
             "VALUES (CAST(:p AS UUID), CAST(:t AS UUID), :s, :name, :notes, "
             "        :pub, :sc, :sf, :sx, :sl) "
             "ON CONFLICT (tenant_id) DO UPDATE SET "
             "  slug = excluded.slug, "
             "  public_name = excluded.public_name, "
             "  notes = excluded.notes, "
             "  is_published = excluded.is_published, "
             "  show_compliance = excluded.show_compliance, "
             "  show_finding_counts = excluded.show_finding_counts, "
             "  show_clouds = excluded.show_clouds, "
             "  show_last_scan = excluded.show_last_scan, "
             "  updated_at = now()"),
        parameters=params,
    )
    return _resp(200, {"saved": True, "slug": slug, "is_published": bool(body.get("is_published", False))})


# ============================================================================
# Helpers
# ============================================================================

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
