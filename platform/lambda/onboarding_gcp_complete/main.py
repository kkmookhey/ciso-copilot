"""POST /onboarding/gcp/complete

NOT JWT-authed — called by the gcloud onboarding script. Authenticates via
the external_id matching the pending cloud_connection row.

Body (project mode — historical default):
  {
    "external_id":     "<one-time>",
    "project_id":      "<gcp project>",
    "project_number":  "<gcp project number>",
    "sa_email":        "ciso-copilot-reader@<project>.iam.gserviceaccount.com",
    "wif_pool":        "ciso-copilot-pool",
    "wif_provider":    "ciso-copilot-aws-provider"
  }

Body (org mode — when onboard.sh was run with --org <ORG_ID>):
  {
    "external_id":         "<one-time>",
    "mode":                "org",
    "org_id":              "<gcp org id>",
    "host_project_id":     "<host gcp project>",
    "host_project_number": "<host gcp project number>",
    "sa_email":            "ciso-copilot-reader@<host>.iam.gserviceaccount.com",
    "wif_pool":            "ciso-copilot-pool",
    "wif_provider":        "ciso-copilot-aws-provider"
  }

Org mode does NOT auto-scan — projects are discovered lazily on the first
scan (the scanner's Stage 1 enumerates and writes back to scope.projects).
Project mode keeps the historical auto-scan-on-onboard behaviour.
"""
from __future__ import annotations

import json
import os
import uuid

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
GCP_SCAN_TASK_DEF      = os.environ.get("GCP_SCAN_TASK_DEF", "")
SCAN_CLUSTER_ARN       = os.environ.get("SCAN_CLUSTER_ARN", "")
SCAN_SUBNET_IDS        = os.environ.get("SCAN_SUBNET_IDS", "")
SCAN_SECURITY_GROUP_ID = os.environ.get("SCAN_SECURITY_GROUP_ID", "")

rds_data = boto3.client("rds-data")
ecs      = boto3.client("ecs")


def handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    external_id  = body.get("external_id")
    mode         = (body.get("mode") or "project").lower()
    sa_email     = body.get("sa_email")
    wif_pool     = body.get("wif_pool")
    wif_provider = body.get("wif_provider")
    if not all([external_id, sa_email, wif_pool, wif_provider]):
        return _resp(400, {"error": "missing_fields"})

    if mode == "org":
        org_id              = body.get("org_id")
        host_project_id     = body.get("host_project_id")
        host_project_number = body.get("host_project_number")
        if not all([org_id, host_project_id, host_project_number]):
            return _resp(400, {"error": "missing_fields"})
        scope = {
            "mode":                "org",
            "org_id":              org_id,
            "host_project_id":     host_project_id,
            "host_project_number": host_project_number,
            "sa_email":            sa_email,
            "wif_pool":            wif_pool,
            "wif_provider":        wif_provider,
            "projects":            {},
            "selected":            [],
        }
        account_identifier = org_id
    elif mode == "project":
        project_id     = body.get("project_id")
        project_number = body.get("project_number")
        if not all([project_id, project_number]):
            return _resp(400, {"error": "missing_fields"})
        scope = {
            "project_id":     project_id,
            "project_number": project_number,
            "sa_email":       sa_email,
            "wif_pool":       wif_pool,
            "wif_provider":   wif_provider,
        }
        account_identifier = project_id
    else:
        return _resp(400, {"error": "invalid_mode", "mode": mode})

    conn = _get_connection_by_external_id(external_id)
    if not conn:
        return _resp(404, {"error": "external_id_unknown"})
    if conn["status"] != "pending":
        return _resp(409, {"error": "already_completed", "current_status": conn["status"]})

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
            {"name": "pid",   "value": {"stringValue": account_identifier}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )

    print(f"gcp connection {conn['conn_id']} active — {mode}={account_identifier}")

    if mode == "org":
        # Org mode does not auto-scan — the project list is empty until the
        # scanner enumerates on first scan. The user starts the scan manually
        # (Connect-page rescan today; the /scan screen after Slice 2b).
        initial_scan_id = None
    else:
        initial_scan_id = _run_initial_scan(
            tenant_id = conn["tenant_id"],
            conn_id   = conn["conn_id"],
            scope     = scope,
        )

    return _resp(200, {
        "status":          "active",
        "connection_id":   conn["conn_id"],
        "mode":            mode,
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


def _run_initial_scan(*, tenant_id: str, conn_id: str, scope: dict) -> str | None:
    """Insert one `scans` row and start one v2 GCP Fargate task that
    scans the connection's project. Mirrors onboarding_azure_complete."""
    if not (GCP_SCAN_TASK_DEF and SCAN_CLUSTER_ARN and SCAN_SUBNET_IDS):
        print("WARN: gcp scan task not configured; skipping initial scan")
        return None

    scan_id = str(uuid.uuid4())
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO scans (scan_id, tenant_id, conn_id, trigger, "
            "                   status, tier, phase) "
            "VALUES (CAST(:sid AS UUID), CAST(:tid AS UUID), "
            "        CAST(:cid AS UUID), 'onboarding', 'queued', "
            "        'quick', 'region_discovery')"
        ),
        parameters=[
            {"name": "sid", "value": {"stringValue": scan_id}},
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "cid", "value": {"stringValue": conn_id}},
        ],
    )

    try:
        ecs.run_task(
            cluster=SCAN_CLUSTER_ARN,
            taskDefinition=GCP_SCAN_TASK_DEF,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets":        [s for s in SCAN_SUBNET_IDS.split(",") if s],
                    "securityGroups": [SCAN_SECURITY_GROUP_ID] if SCAN_SECURITY_GROUP_ID else [],
                    "assignPublicIp": "DISABLED",
                },
            },
            overrides={
                "containerOverrides": [{
                    "name": "scanner",
                    "environment": [
                        {"name": "SCAN_ID",            "value": scan_id},
                        {"name": "TENANT_ID",          "value": tenant_id},
                        {"name": "CONN_ID",            "value": conn_id},
                        {"name": "PROJECT_IDS",        "value": scope["project_id"]},
                        {"name": "WIF_PROJECT_NUMBER", "value": scope["project_number"]},
                        {"name": "SA_EMAIL",           "value": scope["sa_email"]},
                        {"name": "WIF_POOL",           "value": scope["wif_pool"]},
                        {"name": "WIF_PROVIDER",       "value": scope["wif_provider"]},
                        {"name": "SCAN_TIER",          "value": "quick"},
                    ],
                }],
            },
        )
        print(f"gcp onboarding scan {scan_id} started for {conn_id}")
    except Exception as e:
        print(f"WARN: gcp scan RunTask failed for {conn_id}: {e}")
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql="UPDATE scans SET status='failed' WHERE scan_id = CAST(:sid AS UUID)",
            parameters=[{"name": "sid", "value": {"stringValue": scan_id}}],
        )

    return scan_id


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
