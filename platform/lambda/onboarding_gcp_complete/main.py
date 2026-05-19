"""POST /onboarding/gcp/complete

NOT JWT-authed — called by the gcloud onboarding script. Authenticates via
the external_id matching the pending cloud_connection row.

Body:
  {
    "external_id":     "<one-time>",
    "project_id":      "<gcp project>",
    "project_number":  "<gcp project number>",
    "sa_email":        "ciso-copilot-reader@<project>.iam.gserviceaccount.com",
    "wif_pool":        "ciso-copilot-pool",
    "wif_provider":    "ciso-copilot-aws-provider"
  }
"""
from __future__ import annotations

import json
import os
import uuid

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
GCP_RUNNER_FN  = os.environ.get("GCP_RUNNER_FN", "")

rds_data       = boto3.client("rds-data")
lambda_client  = boto3.client("lambda")


def handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    external_id    = body.get("external_id")
    project_id     = body.get("project_id")
    project_number = body.get("project_number")
    sa_email       = body.get("sa_email")
    wif_pool       = body.get("wif_pool")
    wif_provider   = body.get("wif_provider")

    if not all([external_id, project_id, project_number, sa_email, wif_pool, wif_provider]):
        return _resp(400, {"error": "missing_fields"})

    conn = _get_connection_by_external_id(external_id)
    if not conn:
        return _resp(404, {"error": "external_id_unknown"})
    if conn["status"] != "pending":
        return _resp(409, {"error": "already_completed", "current_status": conn["status"]})

    # GCP WIF: no shared secret to store. Configuration (project, pool, provider,
    # SA email) is stored in `scope` JSON. The Lambda re-constructs the WIF
    # external_account credentials at invoke time from these values.
    scope = {
        "project_id":     project_id,
        "project_number": project_number,
        "sa_email":       sa_email,
        "wif_pool":       wif_pool,
        "wif_provider":   wif_provider,
    }

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE cloud_connections "
            "SET status = 'active', "
            "    account_identifier = :pid, "
            "    scope = CAST(:scope AS JSONB), "
            "    signals = jsonb_build_object('pull_scan', true, 'alerts', false, 'drift', false), "
            "    updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        ),
        parameters=[
            {"name": "cid",   "value": {"stringValue": conn["conn_id"]}},
            {"name": "pid",   "value": {"stringValue": project_id}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )

    print(f"gcp connection {conn['conn_id']} active — project {project_id}")

    initial_scan_id = _enqueue_initial_scan(
        tenant_id      = conn["tenant_id"],
        conn_id        = conn["conn_id"],
        scope          = scope,
    )

    return _resp(200, {
        "status":          "active",
        "connection_id":   conn["conn_id"],
        "initial_scan_id": initial_scan_id,
    })


def _get_connection_by_external_id(external_id: str) -> dict | None:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT conn_id::text, tenant_id::text, status "
            "FROM cloud_connections "
            "WHERE external_id = :eid AND cloud_type = 'gcp'"
        ),
        parameters=[{"name": "eid", "value": {"stringValue": external_id}}],
    )
    rows = rs.get("records", [])
    if not rows:
        return None
    r = rows[0]
    return {
        "conn_id":   r[0].get("stringValue"),
        "tenant_id": r[1].get("stringValue"),
        "status":    r[2].get("stringValue"),
    }


def _enqueue_initial_scan(*, tenant_id: str, conn_id: str, scope: dict) -> str | None:
    if not GCP_RUNNER_FN:
        print("WARN: GCP_RUNNER_FN not configured; skipping initial scan")
        return None

    scan_id = str(uuid.uuid4())
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO scans (scan_id, tenant_id, conn_id, trigger, status, scope) "
            "VALUES (CAST(:sid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        'onboarding', 'queued', CAST(:scope AS JSONB))"
        ),
        parameters=[
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "tid",   "value": {"stringValue": tenant_id}},
            {"name": "cid",   "value": {"stringValue": conn_id}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )

    try:
        lambda_client.invoke(
            FunctionName   = GCP_RUNNER_FN,
            InvocationType = "Event",
            Payload=json.dumps({
                "scan_id":   scan_id,
                "tenant_id": tenant_id,
                "conn_id":   conn_id,
                **scope,
            }).encode(),
        )
        print(f"gcp scan {scan_id} enqueued")
    except Exception as e:
        print(f"WARN: gcp scan invoke failed: {e}")

    return scan_id


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
