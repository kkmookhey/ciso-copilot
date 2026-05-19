"""Lambda handler for the AI scan + asset API.

Routes (paths as seen by the Lambda — API Gateway strips ``/v1`` before
forwarding):

  POST  /ai/scans          start a scan; body {connection_id, repo_full_name, default_branch}
  GET   /ai/scans          list scans (filters: ?connection_id=, ?status=)
  GET   /ai/scans/{id}     scan detail
  GET   /ai/assets         list assets (filters: ?repo=, ?type=, ?page=)
  GET   /ai/assets/{id}    asset detail + full evidence packet
"""
from __future__ import annotations

import json
import os
import uuid

import boto3

import helpers

AI_SCAN_QUEUE_URL = os.environ["AI_SCAN_QUEUE_URL"]
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX     = 200

_sqs = boto3.client("sqs")


def handler(event: dict, context) -> dict:
    method = event.get("httpMethod") or ""
    path   = event.get("path") or ""
    try:
        if method == "POST" and path == "/ai/scans":
            return _start_scan(event)
        if method == "GET" and path == "/ai/scans":
            return _list_scans(event)
        if method == "GET" and path.startswith("/ai/scans/"):
            return _get_scan(event)
        if method == "GET" and path == "/ai/assets":
            return _list_assets(event)
        if method == "GET" and path.startswith("/ai/assets/"):
            return _get_asset(event)
        return helpers.resp(404, {"error": "not_found", "path": path, "method": method})
    except Exception as e:  # noqa: BLE001
        return helpers.resp(500, {"error": "internal", "detail": str(e)})


# ----------------------------------------------------------------------------
# POST /ai/scans
# ----------------------------------------------------------------------------

def _start_scan(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return helpers.resp(400, {"error": "bad_json"})

    conn_id        = body.get("connection_id")
    repo_full_name = body.get("repo_full_name")
    default_branch = body.get("default_branch") or "main"
    if not conn_id or not repo_full_name:
        return helpers.resp(400, {"error": "missing_connection_id_or_repo"})

    installation_id = _installation_id_for_connection(tenant_id, conn_id)
    if installation_id is None:
        return helpers.resp(404, {"error": "connection_not_found"})

    repo_asset_id = _upsert_repo_asset(tenant_id, conn_id, repo_full_name)
    scan_id = str(uuid.uuid4())

    helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "INSERT INTO ai_scans "
            "  (id, tenant_id, connection_id, repo_asset_id, status, scanner_version) "
            "VALUES (CAST(:sid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        CAST(:rid AS UUID), 'queued', :ver)"
        ),
        parameters=[
            {"name": "sid", "value": {"stringValue": scan_id}},
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "cid", "value": {"stringValue": conn_id}},
            {"name": "rid", "value": {"stringValue": repo_asset_id}},
            {"name": "ver", "value": {"stringValue": "0.1.0"}},
        ],
    )

    _sqs.send_message(
        QueueUrl=AI_SCAN_QUEUE_URL,
        MessageBody=json.dumps({
            "scan_id":         scan_id,
            "tenant_id":       tenant_id,
            "connection_id":   conn_id,
            "repo_asset_id":   repo_asset_id,
            "repo_full_name":  repo_full_name,
            "default_branch":  default_branch,
            "installation_id": installation_id,
        }),
    )
    return helpers.resp(202, {"scan_id": scan_id})


def _installation_id_for_connection(tenant_id: str, conn_id: str) -> int | None:
    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "SELECT github_installation_id FROM ai_connections "
            "WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tid AS UUID) "
            "  AND provider = 'github' AND status = 'active'"
        ),
        parameters=[
            {"name": "id",  "value": {"stringValue": conn_id}},
            {"name": "tid", "value": {"stringValue": tenant_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return None
    return rows[0][0].get("longValue")


def _upsert_repo_asset(tenant_id: str, conn_id: str, repo_full_name: str) -> str:
    """Return the UUID of the repository ai_asset, creating it if absent."""
    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "SELECT id::text FROM ai_assets "
            "WHERE tenant_id = CAST(:tid AS UUID) AND asset_type = 'repository' "
            "  AND name = :n AND source_repo_id IS NULL LIMIT 1"
        ),
        parameters=[
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "n",   "value": {"stringValue": repo_full_name}},
        ],
    )
    rows = rs.get("records", [])
    if rows:
        return rows[0][0].get("stringValue")

    new_id = str(uuid.uuid4())
    helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "INSERT INTO ai_assets "
            "  (id, tenant_id, connection_id, asset_type, name, attributes, "
            "   evidence_packet, detector_id, detector_version, scan_id) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        'repository', :n, '{}'::jsonb, "
            "        '{\"version\":\"0.1\",\"detector\":{\"id\":\"manual.repo_attach\",\"version\":\"0.1.0\"}}'::jsonb, "
            "        'manual.repo_attach', '0.1.0', "
            "        CAST(:bootstrap AS UUID))"
        ),
        parameters=[
            {"name": "id",        "value": {"stringValue": new_id}},
            {"name": "tid",       "value": {"stringValue": tenant_id}},
            {"name": "cid",       "value": {"stringValue": conn_id}},
            {"name": "n",         "value": {"stringValue": repo_full_name}},
            {"name": "bootstrap", "value": {"stringValue": "00000000-0000-0000-0000-000000000000"}},
        ],
    )
    return new_id


# ----------------------------------------------------------------------------
# GET /ai/scans
# ----------------------------------------------------------------------------

def _list_scans(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})

    q = event.get("queryStringParameters") or {}
    conn_id = q.get("connection_id")
    status  = q.get("status")

    sql = (
        "SELECT s.id::text, COALESCE(r.name, '') AS repo, s.status, "
        "  to_char(s.started_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
        "  COALESCE(to_char(s.completed_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), ''), "
        "  COALESCE(s.error_message, ''), "
        "  s.assets_discovered_count, s.relationships_discovered_count, "
        "  s.findings_generated_count "
        "FROM ai_scans s "
        "LEFT JOIN ai_assets r ON r.id = s.repo_asset_id "
        "WHERE s.tenant_id = CAST(:tid AS UUID)"
    )
    params: list[dict] = [{"name": "tid", "value": {"stringValue": tenant_id}}]
    if conn_id:
        sql += " AND s.connection_id = CAST(:cid AS UUID)"
        params.append({"name": "cid", "value": {"stringValue": conn_id}})
    if status:
        sql += " AND s.status = :st"
        params.append({"name": "st", "value": {"stringValue": status}})
    sql += " ORDER BY s.started_at DESC LIMIT 100"

    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=sql, parameters=params,
    )
    return helpers.resp(200, {"scans": [_row_to_scan(r) for r in rs.get("records", [])]})


def _get_scan(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})
    scan_id = (event.get("pathParameters") or {}).get("id")
    if not scan_id:
        return helpers.resp(400, {"error": "missing_id"})

    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "SELECT s.id::text, COALESCE(r.name, '') AS repo, s.status, "
            "  to_char(s.started_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
            "  COALESCE(to_char(s.completed_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), ''), "
            "  COALESCE(s.error_message, ''), "
            "  s.assets_discovered_count, s.relationships_discovered_count, "
            "  s.findings_generated_count "
            "FROM ai_scans s "
            "LEFT JOIN ai_assets r ON r.id = s.repo_asset_id "
            "WHERE s.tenant_id = CAST(:tid AS UUID) AND s.id = CAST(:sid AS UUID) LIMIT 1"
        ),
        parameters=[
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "sid", "value": {"stringValue": scan_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return helpers.resp(404, {"error": "not_found"})
    return helpers.resp(200, _row_to_scan(rows[0]))


def _row_to_scan(r: list) -> dict:
    return {
        "id":                              r[0].get("stringValue"),
        "repo_full_name":                  r[1].get("stringValue"),
        "status":                          r[2].get("stringValue"),
        "started_at":                      r[3].get("stringValue"),
        "completed_at":                    r[4].get("stringValue") or None,
        "error_message":                   r[5].get("stringValue") or None,
        "assets_discovered_count":         r[6].get("longValue", 0),
        "relationships_discovered_count":  r[7].get("longValue", 0),
        "findings_generated_count":        r[8].get("longValue", 0),
    }


# ----------------------------------------------------------------------------
# GET /ai/assets
# ----------------------------------------------------------------------------

def _list_assets(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})

    q = event.get("queryStringParameters") or {}
    asset_type = q.get("type")
    repo_id    = q.get("repo")
    try:
        page     = max(1, int(q.get("page", "1")))
        per_page = min(int(q.get("per_page", str(PAGE_SIZE_DEFAULT))), PAGE_SIZE_MAX)
    except (TypeError, ValueError):
        return helpers.resp(400, {"error": "bad_pagination"})

    sql = (
        "SELECT a.id::text, a.asset_type, a.name, "
        "  COALESCE(r.id::text, ''), COALESCE(r.name, ''), "
        "  COALESCE(a.source_path, ''), a.detector_id, "
        "  to_char(a.first_seen_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
        "  to_char(a.last_seen_at,  'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
        "FROM ai_assets a "
        "LEFT JOIN ai_assets r ON r.id = a.source_repo_id "
        "WHERE a.tenant_id = CAST(:tid AS UUID) AND a.asset_type != 'repository'"
    )
    params: list[dict] = [{"name": "tid", "value": {"stringValue": tenant_id}}]
    if asset_type:
        sql += " AND a.asset_type = :type"
        params.append({"name": "type", "value": {"stringValue": asset_type}})
    if repo_id:
        sql += " AND a.source_repo_id = CAST(:rid AS UUID)"
        params.append({"name": "rid", "value": {"stringValue": repo_id}})
    sql += " ORDER BY a.last_seen_at DESC LIMIT :lim OFFSET :off"
    params.append({"name": "lim", "value": {"longValue": per_page + 1}})
    params.append({"name": "off", "value": {"longValue": (page - 1) * per_page}})

    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=sql, parameters=params,
    )
    records = rs.get("records", [])
    has_next = len(records) > per_page
    records = records[:per_page]
    return helpers.resp(200, {
        "assets":    [_row_to_asset(r) for r in records],
        "next_page": (page + 1) if has_next else None,
    })


def _row_to_asset(r: list) -> dict:
    repo_id   = r[3].get("stringValue") or None
    repo_name = r[4].get("stringValue")
    return {
        "id":            r[0].get("stringValue"),
        "asset_type":    r[1].get("stringValue"),
        "name":          r[2].get("stringValue"),
        "source_repo":   {"id": repo_id, "full_name": repo_name} if repo_id else None,
        "source_path":   r[5].get("stringValue") or None,
        "detector_id":   r[6].get("stringValue"),
        "first_seen_at": r[7].get("stringValue"),
        "last_seen_at":  r[8].get("stringValue"),
    }


def _get_asset(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})
    asset_id = (event.get("pathParameters") or {}).get("id")
    if not asset_id:
        return helpers.resp(400, {"error": "missing_id"})

    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "SELECT a.id::text, a.asset_type, a.name, "
            "  COALESCE(r.id::text, ''), COALESCE(r.name, ''), "
            "  COALESCE(a.source_path, ''), a.detector_id, "
            "  to_char(a.first_seen_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
            "  to_char(a.last_seen_at,  'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
            "  a.attributes::text, a.evidence_packet::text, "
            "  COALESCE(a.connection_id::text, '') "
            "FROM ai_assets a "
            "LEFT JOIN ai_assets r ON r.id = a.source_repo_id "
            "WHERE a.tenant_id = CAST(:tid AS UUID) AND a.id = CAST(:aid AS UUID) LIMIT 1"
        ),
        parameters=[
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "aid", "value": {"stringValue": asset_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return helpers.resp(404, {"error": "not_found"})
    r = rows[0]
    repo_id   = r[3].get("stringValue") or None
    repo_name = r[4].get("stringValue")
    return helpers.resp(200, {
        "id":              r[0].get("stringValue"),
        "asset_type":      r[1].get("stringValue"),
        "name":            r[2].get("stringValue"),
        "source_repo":     {"id": repo_id, "full_name": repo_name} if repo_id else None,
        "source_path":     r[5].get("stringValue") or None,
        "detector_id":     r[6].get("stringValue"),
        "first_seen_at":   r[7].get("stringValue"),
        "last_seen_at":    r[8].get("stringValue"),
        "attributes":      json.loads(r[9].get("stringValue") or "{}"),
        "evidence_packet": json.loads(r[10].get("stringValue") or "{}"),
        "connection_id":   r[11].get("stringValue") or None,
    })
