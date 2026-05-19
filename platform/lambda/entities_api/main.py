"""Lambda handler for the unified entities + scan API.

Routes (paths as seen by the Lambda — API Gateway strips ``/v1`` before
forwarding):

  POST  /ai/scans                        start a scan; body {connection_id, repo_full_name, default_branch}
  GET   /ai/scans                        list scans (filters: ?connection_id=, ?status=)
  GET   /ai/scans/{id}                   scan detail
  GET   /entities                        list entities (filters: ?domain=, ?kind=, ?repo=, ?page=, ?per_page=)
  GET   /entities/{id}                   entity detail + evidence packet
  GET   /entities/{id}/graph             recursive-CTE graph in cytoscape shape
  GET   /entities/{id}/relationships     flat edges with other-entity joined

Replaces ai_scan_api. The repo asset is no longer a row in ai_assets — it's
an entity in the unified `entities` table with kind='github_repo'.
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

GRAPH_DEPTH_DEFAULT     = 4
GRAPH_DEPTH_MAX         = 8
GRAPH_MAX_NODES_DEFAULT = 500
GRAPH_MAX_NODES_LIMIT   = 1000

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
        if method == "GET" and path == "/entities":
            return _list_entities(event)
        if method == "GET" and path.startswith("/entities/"):
            # /entities/{id}, /entities/{id}/graph, /entities/{id}/relationships
            if path.endswith("/graph"):
                return _entity_graph(event)
            if path.endswith("/relationships"):
                return _entity_relationships(event)
            return _get_entity(event)
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

    repo_entity_id = _upsert_repo_entity(tenant_id, conn_id, repo_full_name)
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
            {"name": "rid", "value": {"stringValue": repo_entity_id}},
            {"name": "ver", "value": {"stringValue": "0.1.0"}},
        ],
    )

    _sqs.send_message(
        QueueUrl=AI_SCAN_QUEUE_URL,
        MessageBody=json.dumps({
            "scan_id":         scan_id,
            "tenant_id":       tenant_id,
            "connection_id":   conn_id,
            "repo_asset_id":   repo_entity_id,
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


def _upsert_repo_entity(tenant_id: str, conn_id: str, repo_full_name: str) -> str:
    """Upsert a github_repo entity. Returns the PERSISTED id (existing row
    on conflict, new row on insert). Mirrors unified_writer's pattern."""
    natural_key = f"github.com/{repo_full_name}"
    new_id = str(uuid.uuid4())
    attrs = {"_stub": False}
    evidence = {
        "version": "0.1",
        "detector": {"id": "manual.repo_attach", "version": "0.1.0"},
    }
    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "INSERT INTO entities "
            "  (id, tenant_id, kind, natural_key, display_name, domain, "
            "   attributes, evidence_packet, detector_id, detector_version, "
            "   connection_id) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), 'github_repo', "
            "        :nk, :name, 'repo', "
            "        CAST(:attrs AS JSONB), CAST(:ev AS JSONB), "
            "        'manual.repo_attach', '0.1.0', CAST(:cid AS UUID)) "
            "ON CONFLICT (tenant_id, kind, natural_key) "
            "  DO UPDATE SET last_seen_at=NOW(), "
            "                attributes=COALESCE(EXCLUDED.attributes - '_stub', entities.attributes), "
            "                display_name=EXCLUDED.display_name "
            "RETURNING id::text"
        ),
        parameters=[
            {"name": "id",    "value": {"stringValue": new_id}},
            {"name": "tid",   "value": {"stringValue": tenant_id}},
            {"name": "nk",    "value": {"stringValue": natural_key}},
            {"name": "name",  "value": {"stringValue": repo_full_name}},
            {"name": "attrs", "value": {"stringValue": json.dumps(attrs)}},
            {"name": "ev",    "value": {"stringValue": json.dumps(evidence)}},
            {"name": "cid",   "value": {"stringValue": conn_id}},
        ],
    )
    rows = rs.get("records", [])
    if rows and rows[0] and "stringValue" in rows[0][0]:
        return rows[0][0]["stringValue"]
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
        "SELECT s.id::text, COALESCE(r.display_name, '') AS repo, s.status, "
        "  to_char(s.started_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
        "  COALESCE(to_char(s.completed_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), ''), "
        "  COALESCE(s.error_message, ''), "
        "  s.assets_discovered_count, s.relationships_discovered_count, "
        "  s.findings_generated_count "
        "FROM ai_scans s "
        "LEFT JOIN entities r ON r.id = s.repo_asset_id AND r.kind = 'github_repo' "
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
            "SELECT s.id::text, COALESCE(r.display_name, '') AS repo, s.status, "
            "  to_char(s.started_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
            "  COALESCE(to_char(s.completed_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), ''), "
            "  COALESCE(s.error_message, ''), "
            "  s.assets_discovered_count, s.relationships_discovered_count, "
            "  s.findings_generated_count "
            "FROM ai_scans s "
            "LEFT JOIN entities r ON r.id = s.repo_asset_id AND r.kind = 'github_repo' "
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
# GET /entities
# ----------------------------------------------------------------------------

def _list_entities(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})
    q = event.get("queryStringParameters") or {}
    domain  = q.get("domain")
    kind    = q.get("kind")
    repo_id = q.get("repo")
    try:
        page     = max(1, int(q.get("page", "1")))
        per_page = min(int(q.get("per_page", str(PAGE_SIZE_DEFAULT))), PAGE_SIZE_MAX)
    except (TypeError, ValueError):
        return helpers.resp(400, {"error": "bad_pagination"})

    sql = (
        "SELECT e.id::text, e.kind, e.natural_key, e.display_name, e.domain, "
        "       e.detector_id, "
        "       to_char(e.first_seen_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
        "       to_char(e.last_seen_at,  'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
        "       e.attributes::text "
        "FROM entities e "
        "WHERE e.tenant_id = CAST(:tid AS UUID)"
    )
    params: list[dict] = [{"name": "tid", "value": {"stringValue": tenant_id}}]
    if domain:
        sql += " AND e.domain = :dom"
        params.append({"name": "dom", "value": {"stringValue": domain}})
    if kind:
        sql += " AND e.kind = :kind"
        params.append({"name": "kind", "value": {"stringValue": kind}})
    if repo_id:
        # Filter by edges where this repo is the source.
        sql += (" AND e.id IN ("
                "  SELECT target_entity_id FROM edges "
                "  WHERE source_entity_id = CAST(:rid AS UUID) "
                "    AND tenant_id = CAST(:tid AS UUID))")
        params.append({"name": "rid", "value": {"stringValue": repo_id}})
    sql += " ORDER BY e.last_seen_at DESC LIMIT :lim OFFSET :off"
    params.append({"name": "lim", "value": {"longValue": per_page + 1}})
    params.append({"name": "off", "value": {"longValue": (page - 1) * per_page}})

    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME, sql=sql, parameters=params,
    )
    records = rs.get("records", [])
    has_next = len(records) > per_page
    return helpers.resp(200, {
        "entities":  [_row_to_entity(r) for r in records[:per_page]],
        "next_page": (page + 1) if has_next else None,
    })


def _row_to_entity(r: list) -> dict:
    attrs = json.loads(r[8].get("stringValue") or "{}")
    return {
        "id":            r[0].get("stringValue"),
        "kind":          r[1].get("stringValue"),
        "natural_key":   r[2].get("stringValue"),
        "display_name":  r[3].get("stringValue"),
        "domain":        r[4].get("stringValue"),
        "detector_id":   r[5].get("stringValue"),
        "first_seen_at": r[6].get("stringValue"),
        "last_seen_at":  r[7].get("stringValue"),
        "attributes":    attrs,
        "source_path":   attrs.get("source_path"),
    }


# ----------------------------------------------------------------------------
# GET /entities/{id}
# ----------------------------------------------------------------------------

def _get_entity(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})
    entity_id = (event.get("pathParameters") or {}).get("id")
    if not entity_id:
        return helpers.resp(400, {"error": "missing_id"})

    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "SELECT id::text, kind, natural_key, display_name, domain, "
            "       detector_id, "
            "       to_char(first_seen_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
            "       to_char(last_seen_at,  'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
            "       attributes::text, COALESCE(evidence_packet::text, 'null'), "
            "       COALESCE(connection_id::text, '') "
            "FROM entities WHERE tenant_id = CAST(:tid AS UUID) "
            "  AND id = CAST(:eid AS UUID) LIMIT 1"
        ),
        parameters=[
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "eid", "value": {"stringValue": entity_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return helpers.resp(404, {"error": "not_found"})
    r = rows[0]
    return helpers.resp(200, {
        "id":              r[0].get("stringValue"),
        "kind":            r[1].get("stringValue"),
        "natural_key":     r[2].get("stringValue"),
        "display_name":    r[3].get("stringValue"),
        "domain":          r[4].get("stringValue"),
        "detector_id":     r[5].get("stringValue"),
        "first_seen_at":   r[6].get("stringValue"),
        "last_seen_at":    r[7].get("stringValue"),
        "attributes":      json.loads(r[8].get("stringValue") or "{}"),
        "evidence_packet": json.loads(r[9].get("stringValue") or "null"),
        "connection_id":   r[10].get("stringValue") or None,
    })


# ----------------------------------------------------------------------------
# GET /entities/{id}/graph
# ----------------------------------------------------------------------------

def _entity_graph(event: dict) -> dict:
    """Recursive CTE walking outward from the root entity, capped at depth +
    node count. Returns cytoscape-shaped JSON."""
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})
    entity_id = (event.get("pathParameters") or {}).get("id")
    if not entity_id:
        return helpers.resp(400, {"error": "missing_id"})
    q = event.get("queryStringParameters") or {}
    try:
        depth     = min(int(q.get("depth", str(GRAPH_DEPTH_DEFAULT))),     GRAPH_DEPTH_MAX)
        max_nodes = min(int(q.get("max_nodes", str(GRAPH_MAX_NODES_DEFAULT))), GRAPH_MAX_NODES_LIMIT)
    except (TypeError, ValueError):
        return helpers.resp(400, {"error": "bad_query"})

    # Recursive CTE walks edges in both directions from the root.
    nodes_sql = (
        "WITH RECURSIVE walked(id, depth) AS ( "
        "  SELECT id, 0 FROM entities "
        "  WHERE id = CAST(:root AS UUID) AND tenant_id = CAST(:tid AS UUID) "
        "  UNION "
        "  SELECT next_id, walked.depth + 1 FROM walked "
        "  CROSS JOIN LATERAL ( "
        "    SELECT CASE WHEN source_entity_id = walked.id "
        "                THEN target_entity_id ELSE source_entity_id END AS next_id "
        "    FROM edges "
        "    WHERE (source_entity_id = walked.id OR target_entity_id = walked.id) "
        "      AND tenant_id = CAST(:tid AS UUID) "
        "  ) ex "
        "  WHERE walked.depth < :max_depth "
        ") "
        "SELECT e.id::text, e.kind, e.display_name, e.attributes::text "
        "FROM (SELECT DISTINCT id FROM walked) w "
        "JOIN entities e ON e.id = w.id "
        "LIMIT :max_nodes"
    )
    nrs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME, sql=nodes_sql,
        parameters=[
            {"name": "root",      "value": {"stringValue": entity_id}},
            {"name": "tid",       "value": {"stringValue": tenant_id}},
            {"name": "max_depth", "value": {"longValue":   depth}},
            {"name": "max_nodes", "value": {"longValue":   max_nodes + 1}},
        ],
    )
    node_rows = nrs.get("records", [])
    truncated = len(node_rows) > max_nodes
    node_rows = node_rows[:max_nodes]
    node_ids = [r[0].get("stringValue") for r in node_rows]
    if not node_ids:
        return helpers.resp(404, {"error": "not_found"})

    # Edges among the walked nodes only (no dangling references in the UI).
    ers = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=("SELECT id::text, source_entity_id::text, target_entity_id::text, kind "
             "FROM edges WHERE tenant_id = CAST(:tid AS UUID) "
             "  AND source_entity_id::text = ANY(string_to_array(:ids, ',')) "
             "  AND target_entity_id::text = ANY(string_to_array(:ids, ','))"),
        parameters=[
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "ids", "value": {"stringValue": ",".join(node_ids)}},
        ],
    )
    return helpers.resp(200, {
        "nodes": [{"data": {
            "id":         r[0].get("stringValue"),
            "label":      r[2].get("stringValue"),
            "type":       r[1].get("stringValue"),
            "attributes": json.loads(r[3].get("stringValue") or "{}"),
        }} for r in node_rows],
        "edges": [{"data": {
            "id":     r[0].get("stringValue"),
            "source": r[1].get("stringValue"),
            "target": r[2].get("stringValue"),
            "label":  r[3].get("stringValue"),
        }} for r in ers.get("records", [])],
        "meta": {
            "root_id":    entity_id,
            "node_count": len(node_rows),
            "truncated":  truncated,
        },
    })


# ----------------------------------------------------------------------------
# GET /entities/{id}/relationships
# ----------------------------------------------------------------------------

def _entity_relationships(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})
    entity_id = (event.get("pathParameters") or {}).get("id")
    if not entity_id:
        return helpers.resp(400, {"error": "missing_id"})
    direction = (event.get("queryStringParameters") or {}).get("direction", "both")
    if direction not in ("both", "outgoing", "incoming"):
        return helpers.resp(400, {"error": "bad_direction"})

    where_clauses = []
    if direction in ("both", "outgoing"):
        where_clauses.append("e.source_entity_id = CAST(:eid AS UUID)")
    if direction in ("both", "incoming"):
        where_clauses.append("e.target_entity_id = CAST(:eid AS UUID)")
    where = " OR ".join(where_clauses)

    sql = (
        "SELECT e.id::text, e.kind, "
        "  CASE WHEN e.source_entity_id = CAST(:eid AS UUID) "
        "       THEN 'outgoing' ELSE 'incoming' END AS direction, "
        "  other.id::text, other.kind, other.natural_key, other.display_name "
        "FROM edges e "
        "JOIN entities other ON other.id = CASE "
        "  WHEN e.source_entity_id = CAST(:eid AS UUID) THEN e.target_entity_id "
        "                                                ELSE e.source_entity_id END "
        f"WHERE e.tenant_id = CAST(:tid AS UUID) AND ({where}) "
        "ORDER BY e.last_seen_at DESC LIMIT 500"
    )
    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME, sql=sql,
        parameters=[
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "eid", "value": {"stringValue": entity_id}},
        ],
    )
    return helpers.resp(200, {
        "relationships": [{
            "id":        r[0].get("stringValue"),
            "kind":      r[1].get("stringValue"),
            "direction": r[2].get("stringValue"),
            "other_entity": {
                "id":           r[3].get("stringValue"),
                "kind":         r[4].get("stringValue"),
                "natural_key":  r[5].get("stringValue"),
                "display_name": r[6].get("stringValue"),
            },
        } for r in rs.get("records", [])],
    })
