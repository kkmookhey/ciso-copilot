"""POST /onboarding/azure/complete

NOT JWT-authed — called by the Azure onboarding bash script running in the
customer's Azure CLI session. Authenticates via the external_id matching
the pending cloud_connections row created during /initiate.

Body:
  {
    "external_id":      "<one-time>",
    "azure_tenant_id":  "<customer Entra tenant ID>",
    "client_id":        "<SP appId>",
    "client_secret":    "<SP password>",
    "subscription_ids": ["<sub_id>", ...]
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
AZURE_RUNNER_FN = os.environ.get("AZURE_RUNNER_FN", "")

rds_data       = boto3.client("rds-data")
sm             = boto3.client("secretsmanager")
lambda_client  = boto3.client("lambda")


def handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    external_id      = body.get("external_id")
    azure_tenant_id  = body.get("azure_tenant_id")
    client_id        = body.get("client_id")
    client_secret    = body.get("client_secret")
    subscription_ids = body.get("subscription_ids") or []

    if not all([external_id, azure_tenant_id, client_id, client_secret]) or not subscription_ids:
        return _resp(400, {"error": "missing_fields"})

    conn = _get_connection_by_external_id(external_id)
    if not conn:
        return _resp(404, {"error": "external_id_unknown"})
    if conn["status"] != "pending":
        return _resp(409, {"error": "already_completed", "current_status": conn["status"]})

    secret_arn = _store_credentials(
        conn_id        = conn["conn_id"],
        tenant_id      = conn["tenant_id"],
        azure_tenant_id = azure_tenant_id,
        client_id      = client_id,
        client_secret  = client_secret,
    )

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE cloud_connections "
            "SET status = 'active', "
            "    credentials_secret_arn = :sec, "
            "    account_identifier = :tid, "
            "    signals = jsonb_build_object('pull_scan', true, 'alerts', false, 'drift', false), "
            "    scope = CAST(:scope AS JSONB), "
            "    updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        ),
        parameters=[
            {"name": "cid",   "value": {"stringValue": conn["conn_id"]}},
            {"name": "sec",   "value": {"stringValue": secret_arn}},
            {"name": "tid",   "value": {"stringValue": azure_tenant_id}},
            {"name": "scope", "value": {"stringValue": json.dumps({"subscriptions": subscription_ids})}},
        ],
    )

    print(f"azure connection {conn['conn_id']} active — {len(subscription_ids)} subscription(s)")

    # Kick an initial scan per subscription. Each scan is independent so the
    # scanner code stays single-sub; the connection's full posture is the union
    # of all scans tied to it.
    scan_ids = [
        _enqueue_initial_scan(
            tenant_id        = conn["tenant_id"],
            conn_id          = conn["conn_id"],
            azure_tenant_id  = azure_tenant_id,
            client_id        = client_id,
            secret_arn       = secret_arn,
            subscription_id  = sub_id,
        )
        for sub_id in subscription_ids
    ]
    scan_ids = [s for s in scan_ids if s]

    return _resp(200, {
        "status":              "active",
        "connection_id":       conn["conn_id"],
        "subscriptions_count": len(subscription_ids),
        "initial_scan_ids":    scan_ids,
    })


def _get_connection_by_external_id(external_id: str) -> dict | None:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT conn_id::text, tenant_id::text, status "
            "FROM cloud_connections "
            "WHERE external_id = :eid AND cloud_type = 'azure'"
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


def _store_credentials(*, conn_id: str, tenant_id: str, azure_tenant_id: str,
                       client_id: str, client_secret: str) -> str:
    secret_name = f"ciso-copilot/connections/{conn_id}"
    payload = json.dumps({
        "cloud":           "azure",
        "tenant_id":       tenant_id,
        "azure_tenant_id": azure_tenant_id,
        "client_id":       client_id,
        "client_secret":   client_secret,
    })
    try:
        resp = sm.create_secret(
            Name=secret_name,
            SecretString=payload,
            Description=f"Azure SP credentials for tenant {tenant_id}",
        )
        return resp["ARN"]
    except sm.exceptions.ResourceExistsException:
        resp = sm.put_secret_value(SecretId=secret_name, SecretString=payload)
        return resp["ARN"]


def _enqueue_initial_scan(*, tenant_id: str, conn_id: str, azure_tenant_id: str,
                          client_id: str, secret_arn: str, subscription_id: str) -> str | None:
    if not AZURE_RUNNER_FN:
        print("WARN: AZURE_RUNNER_FN not configured; skipping initial scan")
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
            {"name": "scope", "value": {"stringValue": json.dumps({"subscription_id": subscription_id})}},
        ],
    )

    try:
        lambda_client.invoke(
            FunctionName   = AZURE_RUNNER_FN,
            InvocationType = "Event",
            Payload=json.dumps({
                "scan_id":          scan_id,
                "tenant_id":        tenant_id,
                "conn_id":          conn_id,
                "azure_tenant_id":  azure_tenant_id,
                "client_id":        client_id,
                "secret_arn":       secret_arn,
                "subscription_id":  subscription_id,
            }).encode(),
        )
        print(f"azure scan {scan_id} enqueued for {conn_id}")
    except Exception as e:
        print(f"WARN: azure scan invoke failed for {conn_id}: {e}")

    return scan_id


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
