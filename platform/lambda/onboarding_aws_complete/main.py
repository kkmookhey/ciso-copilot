"""POST /onboarding/aws/complete

NOT JWT-authed — called by the CFN custom resource from the customer's
AWS account. Authenticates via the external_id matching the cloud_connections
row created during /initiate (one-time, secret).

Body (from the CFN custom resource ZipFile):
  {
    "connection_id":  "uuid",
    "role_arn":       "arn:aws:iam::<customer-acct>:role/CISOCopilotReader",
    "external_id":    "uuid",
    "account_id":     "<customer-account-id>",
    "config_enabled": true,
    "event_rule_arn": "arn:aws:events:<region>:<acct>:rule/CISOCopilotForwardSecurityEvents"
  }
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
CENTRAL_EVENT_BUS_ARN = os.environ["CENTRAL_EVENT_BUS_ARN"]
SHASTA_RUNNER_FN      = os.environ.get("SHASTA_RUNNER_FN", "")

rds_data = boto3.client("rds-data")
sm       = boto3.client("secretsmanager")
events   = boto3.client("events")
lambda_client = boto3.client("lambda")


def handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    conn_id        = body.get("connection_id")
    role_arn       = body.get("role_arn")
    external_id    = body.get("external_id")
    account_id     = body.get("account_id")
    config_enabled = bool(body.get("config_enabled"))
    event_rule_arn = body.get("event_rule_arn")

    if not all([conn_id, role_arn, external_id, account_id, event_rule_arn]):
        return _resp(400, {"error": "missing_fields"})

    # Verify the external_id matches what we issued in /initiate. If it doesn't,
    # someone is forging this callback — reject loudly.
    conn = _get_connection(conn_id)
    if not conn:
        return _resp(404, {"error": "connection_not_found"})
    if conn["external_id"] != external_id:
        print(f"REJECT: external_id mismatch for {conn_id}")
        return _resp(403, {"error": "external_id_mismatch"})
    if conn["status"] != "pending":
        return _resp(409, {"error": "already_completed", "current_status": conn["status"]})

    # Store the assumed-role credentials reference in Secrets Manager.
    secret_arn = _store_credentials(conn_id, conn["tenant_id"], role_arn, external_id)

    # Allow the customer's account to PutEvents on our central bus.
    _grant_eventbus_putevents(account_id, conn_id)

    # Flip the connection to active.
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "UPDATE cloud_connections "
            "SET status = 'active', "
            "    credentials_secret_arn = :secret_arn, "
            "    account_identifier = :acct, "
            "    signals = jsonb_build_object('pull_scan', true, 'alerts', true, "
            "                                 'drift', :config_enabled::boolean), "
            "    updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        ),
        parameters=[
            {"name": "cid",            "value": {"stringValue": conn_id}},
            {"name": "secret_arn",     "value": {"stringValue": secret_arn}},
            {"name": "acct",           "value": {"stringValue": account_id}},
            {"name": "config_enabled", "value": {"stringValue": "true" if config_enabled else "false"}},
        ],
    )

    print(f"connection {conn_id} activated for tenant {conn['tenant_id']} (account {account_id})")

    # Kick off the initial scan.
    scan_id = _enqueue_initial_scan(
        tenant_id   = conn["tenant_id"],
        conn_id     = conn_id,
        role_arn    = role_arn,
        external_id = external_id,
        account_id  = account_id,
    )

    return _resp(200, {"status": "active", "connection_id": conn_id, "initial_scan_id": scan_id})


def _enqueue_initial_scan(
    *, tenant_id: str, conn_id: str, role_arn: str, external_id: str, account_id: str,
) -> str | None:
    """Insert a scan row and async-invoke the shasta-runner Lambda.

    Fails open — if the invoke fails, the connection is still active and the
    user can re-trigger from the app. We don't want a temporary Lambda hiccup
    to block onboarding.
    """
    import uuid
    if not SHASTA_RUNNER_FN:
        print("WARN: SHASTA_RUNNER_FN not configured; skipping initial scan")
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
            {"name": "scope", "value": {"stringValue": json.dumps({"regions": ["us-east-1"]})}},
        ],
    )

    try:
        lambda_client.invoke(
            FunctionName   = SHASTA_RUNNER_FN,
            InvocationType = "Event",   # async — return immediately
            Payload=json.dumps({
                "scan_id":     scan_id,
                "tenant_id":   tenant_id,
                "conn_id":     conn_id,
                "role_arn":    role_arn,
                "external_id": external_id,
                "account_id":  account_id,
                "regions":     ["us-east-1"],
            }).encode(),
        )
        print(f"initial scan {scan_id} enqueued for {conn_id}")
    except Exception as e:
        print(f"WARN: initial scan invoke failed for {conn_id}: {e}")

    return scan_id


def _get_connection(conn_id: str) -> dict[str, Any] | None:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT tenant_id::text, external_id, status "
            "FROM cloud_connections WHERE conn_id = CAST(:cid AS UUID)"
        ),
        parameters=[{"name": "cid", "value": {"stringValue": conn_id}}],
    )
    rows = rs.get("records", [])
    if not rows:
        return None
    r = rows[0]
    return {
        "tenant_id":   r[0].get("stringValue"),
        "external_id": r[1].get("stringValue"),
        "status":      r[2].get("stringValue"),
    }


def _store_credentials(conn_id: str, tenant_id: str, role_arn: str, external_id: str) -> str:
    """Create or update the per-connection secret in Secrets Manager."""
    secret_name = f"ciso-copilot/connections/{conn_id}"
    secret_value = json.dumps({
        "role_arn":    role_arn,
        "external_id": external_id,
        "tenant_id":   tenant_id,
    })
    try:
        resp = sm.create_secret(
            Name=secret_name,
            SecretString=secret_value,
            Description=f"AWS connection credentials for tenant {tenant_id}",
        )
        return resp["ARN"]
    except sm.exceptions.ResourceExistsException:
        resp = sm.put_secret_value(SecretId=secret_name, SecretString=secret_value)
        return resp["ARN"]


def _grant_eventbus_putevents(account_id: str, conn_id: str) -> None:
    """Allow the customer's account to PutEvents on our central bus."""
    statement_id = f"customer-{account_id}-{conn_id[:8]}"
    try:
        events.put_permission(
            EventBusName=CENTRAL_EVENT_BUS_ARN.split("/")[-1],
            Action="events:PutEvents",
            Principal=account_id,
            StatementId=statement_id,
        )
        print(f"granted PutEvents from {account_id} (sid={statement_id})")
    except events.exceptions.ResourceAlreadyExistsException:
        print(f"PutEvents permission for {account_id} already in place")
    except Exception as e:
        # Don't fail the overall onboarding on this — Lambda admin can fix manually.
        print(f"WARN: PutEvents grant failed for {account_id}: {e}")


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json"},
        "body":       json.dumps(body),
    }
