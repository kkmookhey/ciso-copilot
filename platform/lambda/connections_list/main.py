"""Connection management endpoints:

  GET    /connections              — list cloud connections for the caller's tenant.
  POST   /connections/{id}/rescan  — manually re-trigger a scan for an active connection.
  DELETE /connections/{id}         — revoke (active) or hard-delete (pending/failed).

Response for GET:
  {
    "connections": [
      {
        "conn_id":     "uuid",
        "cloud_type":  "aws",
        "display_name": "...",
        "status":      "active" | "pending" | "error" | "revoked",
        "account_identifier": "<account_id or sub_id>",
        "signals":     {"pull_scan": bool, "alerts": bool, "drift": bool},
        "last_scan_at": "iso8601" | null,
        "created_at":   "iso8601",
        "latest_scan":  {"scan_id": "uuid", "tier": str, "status": str,
                         "phase": str, "started_at": "iso8601" | null} | null
      }, ...
    ]
  }
"""
from __future__ import annotations

import json
import os
import uuid

import boto3

DB_CLUSTER_ARN   = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN    = os.environ["DB_SECRET_ARN"]
DB_NAME          = os.environ["DB_NAME"]
SHASTA_RUNNER_FN = os.environ.get("SHASTA_RUNNER_FN", "")
AZURE_RUNNER_FN  = os.environ.get("AZURE_RUNNER_FN", "")
ENTRA_RUNNER_FN  = os.environ.get("ENTRA_RUNNER_FN", "")
GCP_RUNNER_FN    = os.environ.get("GCP_RUNNER_FN", "")

rds_data      = boto3.client("rds-data")
sm            = boto3.client("secretsmanager")
lambda_client = boto3.client("lambda")


def handler(event: dict, context) -> dict:
    method = (event.get("httpMethod") or "").upper()
    path   = event.get("path") or ""

    if method == "GET" and path.rstrip("/").endswith("/connections"):
        return _list_connections(event)
    if method == "POST" and "/connections/" in path and path.rstrip("/").endswith("/rescan"):
        return _rescan(event)
    if method == "DELETE" and "/connections/" in path and not path.rstrip("/").endswith("/rescan"):
        return _delete(event)

    return _resp(404, {"error": "not_found", "path": path, "method": method})


# ============================================================================
# GET /connections
# ============================================================================

def _list_connections(event: dict) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT c.conn_id::text, c.cloud_type, c.display_name, c.status, "
            "       c.account_identifier, c.signals::text, "
            "       c.last_scan_at::text, c.created_at::text, "
            "       s.scan_id::text, s.tier, s.status, s.phase, s.started_at::text "
            "FROM cloud_connections c "
            "LEFT JOIN LATERAL ("
            "  SELECT scan_id, tier, status, phase, started_at "
            "  FROM scans WHERE scans.conn_id = c.conn_id "
            "  ORDER BY started_at DESC LIMIT 1"
            ") s ON true "
            "WHERE c.tenant_id = CAST(:tid AS UUID) "
            "ORDER BY c.created_at DESC"
        ),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )

    connections = []
    for r in rs.get("records", []):
        latest_scan = None
        if not r[8].get("isNull"):
            latest_scan = {
                "scan_id":    r[8].get("stringValue"),
                "tier":       r[9].get("stringValue"),
                "status":     r[10].get("stringValue"),
                "phase":      r[11].get("stringValue"),
                "started_at": r[12].get("stringValue") if not r[12].get("isNull") else None,
            }
        connections.append({
            "conn_id":            r[0].get("stringValue"),
            "cloud_type":         r[1].get("stringValue"),
            "display_name":       r[2].get("stringValue"),
            "status":             r[3].get("stringValue"),
            "account_identifier": r[4].get("stringValue") if not r[4].get("isNull") else None,
            "signals":            json.loads(r[5].get("stringValue") or "{}"),
            "last_scan_at":       r[6].get("stringValue") if not r[6].get("isNull") else None,
            "created_at":         r[7].get("stringValue"),
            "latest_scan":        latest_scan,
        })

    return _resp(200, {"connections": connections})


# ============================================================================
# POST /connections/{id}/rescan
# ============================================================================

def _rescan(event: dict) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    conn_id = _extract_conn_id(event)
    if not conn_id:
        return _resp(400, {"error": "missing_conn_id"})

    conn = _get_connection_full(conn_id, tenant_id)
    if not conn:
        return _resp(404, {"error": "connection_not_found"})
    if conn["status"] != "active":
        return _resp(409, {"error": "connection_not_active", "current_status": conn["status"]})

    cloud = conn["cloud_type"]
    try:
        if cloud == "aws":
            scan_id = _rescan_aws(conn, tenant_id)
        elif cloud == "azure":
            scan_id = _rescan_azure(conn, tenant_id)
        elif cloud == "entra":
            scan_id = _rescan_entra(conn, tenant_id)
        elif cloud == "gcp":
            scan_id = _rescan_gcp(conn, tenant_id)
        else:
            return _resp(422, {"error": "unsupported_cloud_type", "cloud_type": cloud})
    except _IncompleteConnection as e:
        return _resp(422, {"error": "incomplete_connection", "detail": str(e)})

    # Touch last_scan_at + updated_at so the UI shows recent activity.
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE cloud_connections SET last_scan_at = now(), updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        ),
        parameters=[{"name": "cid", "value": {"stringValue": conn_id}}],
    )

    return _resp(200, {"scan_id": scan_id, "status": "queued"})


def _rescan_aws(conn: dict, tenant_id: str) -> str:
    if not SHASTA_RUNNER_FN:
        raise _IncompleteConnection("SHASTA_RUNNER_FN not configured")
    secret_arn = conn.get("credentials_secret_arn")
    account_id = conn.get("account_identifier")
    external_id = conn.get("external_id")
    if not secret_arn or not account_id:
        raise _IncompleteConnection("missing credentials_secret_arn or account_identifier")

    secret = _get_secret_json(secret_arn)
    role_arn = secret.get("role_arn")
    if not role_arn:
        raise _IncompleteConnection("missing role_arn in secret")
    # Prefer the external_id stored on the row; fall back to the secret copy.
    ext_id = external_id or secret.get("external_id")

    scope = conn.get("scope") or {}
    regions = scope.get("regions") or ["us-east-1"]

    scan_id = str(uuid.uuid4())
    _insert_scan(scan_id, tenant_id, conn["conn_id"], scope or {"regions": regions})
    _invoke_async(SHASTA_RUNNER_FN, {
        "scan_id":     scan_id,
        "tenant_id":   tenant_id,
        "conn_id":     conn["conn_id"],
        "role_arn":    role_arn,
        "external_id": ext_id,
        "account_id":  account_id,
        "regions":     regions,
    })
    return scan_id


def _rescan_azure(conn: dict, tenant_id: str) -> str:
    if not AZURE_RUNNER_FN:
        raise _IncompleteConnection("AZURE_RUNNER_FN not configured")
    secret_arn = conn.get("credentials_secret_arn")
    if not secret_arn:
        raise _IncompleteConnection("missing credentials_secret_arn")

    secret = _get_secret_json(secret_arn)
    azure_tenant_id = secret.get("azure_tenant_id")
    client_id       = secret.get("client_id")
    if not azure_tenant_id or not client_id:
        raise _IncompleteConnection("missing azure_tenant_id or client_id in secret")

    scope = conn.get("scope") or {}
    subscriptions = scope.get("subscriptions") or []
    if not subscriptions:
        raise _IncompleteConnection("missing subscriptions in scope")

    # Fire one scan per subscription (mirrors onboarding_azure_complete). Return
    # the first scan_id — the UI shows "queued" and the user will see all rows
    # land in the scans table.
    first_scan_id = None
    for sub_id in subscriptions:
        scan_id = str(uuid.uuid4())
        _insert_scan(scan_id, tenant_id, conn["conn_id"], {"subscription_id": sub_id})
        _invoke_async(AZURE_RUNNER_FN, {
            "scan_id":         scan_id,
            "tenant_id":       tenant_id,
            "conn_id":         conn["conn_id"],
            "azure_tenant_id": azure_tenant_id,
            "client_id":       client_id,
            "secret_arn":      secret_arn,
            "subscription_id": sub_id,
        })
        first_scan_id = first_scan_id or scan_id
    return first_scan_id or ""


def _rescan_entra(conn: dict, tenant_id: str) -> str:
    if not ENTRA_RUNNER_FN:
        raise _IncompleteConnection("ENTRA_RUNNER_FN not configured")
    entra_tenant_id = conn.get("account_identifier")
    if not entra_tenant_id:
        raise _IncompleteConnection("missing entra tenant id (account_identifier)")

    scan_id = str(uuid.uuid4())
    _insert_scan(scan_id, tenant_id, conn["conn_id"], {"entra_tenant_id": entra_tenant_id})
    _invoke_async(ENTRA_RUNNER_FN, {
        "scan_id":         scan_id,
        "tenant_id":       tenant_id,
        "conn_id":         conn["conn_id"],
        "entra_tenant_id": entra_tenant_id,
    })
    return scan_id


def _rescan_gcp(conn: dict, tenant_id: str) -> str:
    if not GCP_RUNNER_FN:
        raise _IncompleteConnection("GCP_RUNNER_FN not configured")
    scope = conn.get("scope") or {}
    required = ("project_id", "project_number", "sa_email", "wif_pool", "wif_provider")
    missing = [k for k in required if not scope.get(k)]
    if missing:
        raise _IncompleteConnection(f"missing scope fields: {','.join(missing)}")

    scan_id = str(uuid.uuid4())
    _insert_scan(scan_id, tenant_id, conn["conn_id"], scope)
    _invoke_async(GCP_RUNNER_FN, {
        "scan_id":   scan_id,
        "tenant_id": tenant_id,
        "conn_id":   conn["conn_id"],
        **scope,
    })
    return scan_id


# ============================================================================
# DELETE /connections/{id}
# ============================================================================

def _delete(event: dict) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    conn_id = _extract_conn_id(event)
    if not conn_id:
        return _resp(400, {"error": "missing_conn_id"})

    conn = _get_connection_full(conn_id, tenant_id)
    if not conn:
        return _resp(404, {"error": "connection_not_found"})

    status = conn["status"]

    if status == "active":
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=(
                "UPDATE cloud_connections SET status = 'revoked', updated_at = now() "
                "WHERE conn_id = CAST(:cid AS UUID)"
            ),
            parameters=[{"name": "cid", "value": {"stringValue": conn_id}}],
        )
        return _resp(200, {"status": "revoked"})

    if status == "revoked":
        return _resp(200, {"status": "already_revoked"})

    # pending / error / failed — hard delete the row + best-effort secret cleanup.
    secret_arn = conn.get("credentials_secret_arn")
    if secret_arn:
        try:
            sm.delete_secret(SecretId=secret_arn, ForceDeleteWithoutRecovery=True)
        except Exception as e:
            print(f"WARN: secret delete failed for {conn_id}: {e}")

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql="DELETE FROM cloud_connections WHERE conn_id = CAST(:cid AS UUID)",
        parameters=[{"name": "cid", "value": {"stringValue": conn_id}}],
    )
    return _resp(200, {"status": "deleted"})


# ============================================================================
# Helpers
# ============================================================================

class _IncompleteConnection(Exception):
    """Raised when a connection lacks fields needed to scan."""


def _extract_conn_id(event: dict) -> str | None:
    params = event.get("pathParameters") or {}
    cid = params.get("id") or params.get("conn_id")
    if cid:
        return cid
    # Fallback: parse the path. /connections/{id} or /connections/{id}/rescan
    path = (event.get("path") or "").rstrip("/")
    parts = [p for p in path.split("/") if p]
    if "connections" in parts:
        i = parts.index("connections")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def _get_connection_full(conn_id: str, tenant_id: str) -> dict | None:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT conn_id::text, cloud_type, status, credentials_secret_arn, "
            "       account_identifier, external_id, scope::text "
            "FROM cloud_connections "
            "WHERE conn_id = CAST(:cid AS UUID) AND tenant_id = CAST(:tid AS UUID) "
            "LIMIT 1"
        ),
        parameters=[
            {"name": "cid", "value": {"stringValue": conn_id}},
            {"name": "tid", "value": {"stringValue": tenant_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return None
    r = rows[0]
    scope_txt = r[6].get("stringValue") if not r[6].get("isNull") else None
    return {
        "conn_id":                r[0].get("stringValue"),
        "cloud_type":             r[1].get("stringValue"),
        "status":                 r[2].get("stringValue"),
        "credentials_secret_arn": r[3].get("stringValue") if not r[3].get("isNull") else None,
        "account_identifier":     r[4].get("stringValue") if not r[4].get("isNull") else None,
        "external_id":            r[5].get("stringValue") if not r[5].get("isNull") else None,
        "scope":                  json.loads(scope_txt) if scope_txt else {},
    }


def _get_secret_json(secret_arn: str) -> dict:
    resp = sm.get_secret_value(SecretId=secret_arn)
    try:
        return json.loads(resp.get("SecretString") or "{}")
    except json.JSONDecodeError:
        return {}


def _insert_scan(scan_id: str, tenant_id: str, conn_id: str, scope: dict) -> None:
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO scans (scan_id, tenant_id, conn_id, trigger, status, scope) "
            "VALUES (CAST(:sid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        'manual', 'queued', CAST(:scope AS JSONB))"
        ),
        parameters=[
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "tid",   "value": {"stringValue": tenant_id}},
            {"name": "cid",   "value": {"stringValue": conn_id}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )


def _invoke_async(fn_name: str, payload: dict) -> None:
    """Async-invoke a scanner Lambda. Same fail-open posture as onboarding —
    log a WARN on error and let the row stay 'queued'; the user will see it
    stuck and can retry.
    """
    try:
        lambda_client.invoke(
            FunctionName   = fn_name,
            InvocationType = "Event",
            Payload        = json.dumps(payload).encode(),
        )
    except Exception as e:
        print(f"WARN: invoke {fn_name} failed: {e}")


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
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
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
