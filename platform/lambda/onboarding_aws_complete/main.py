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
SCAN_CLUSTER_ARN  = os.environ.get("SCAN_CLUSTER_ARN", "")
SCAN_TASK_DEF_ARN = os.environ.get("SCAN_TASK_DEF_ARN", "")
SCAN_SUBNET_IDS   = os.environ.get("SCAN_SUBNET_IDS", "")
SCAN_SECURITY_GROUP_ID = os.environ.get("SCAN_SECURITY_GROUP_ID", "")

rds_data = boto3.client("rds-data")
sm       = boto3.client("secretsmanager")
events   = boto3.client("events")
ecs      = boto3.client("ecs")


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
    """Insert a scan row and start the scanner Fargate task (Quick tier).

    Fails open — if RunTask fails, the connection is still active and the
    user can re-trigger from the app. A transient ECS hiccup must not
    block onboarding.
    """
    import uuid
    if not (SCAN_CLUSTER_ARN and SCAN_TASK_DEF_ARN and SCAN_SUBNET_IDS):
        print("WARN: scan task not configured; skipping initial scan")
        return None

    scan_id = str(uuid.uuid4())
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO scans (scan_id, tenant_id, conn_id, trigger, status, tier, scope) "
            "VALUES (CAST(:sid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        'onboarding', 'queued', 'quick', CAST(:scope AS JSONB))"
        ),
        parameters=[
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "tid",   "value": {"stringValue": tenant_id}},
            {"name": "cid",   "value": {"stringValue": conn_id}},
            {"name": "scope", "value": {"stringValue": json.dumps({"regions": ["us-east-1"]})}},
        ],
    )

    try:
        ecs.run_task(
            cluster=SCAN_CLUSTER_ARN,
            taskDefinition=SCAN_TASK_DEF_ARN,
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
                        {"name": "SCAN_ID",     "value": scan_id},
                        {"name": "TENANT_ID",   "value": tenant_id},
                        {"name": "CONN_ID",     "value": conn_id},
                        {"name": "ROLE_ARN",    "value": role_arn},
                        {"name": "EXTERNAL_ID", "value": external_id},
                        {"name": "ACCOUNT_ID",  "value": account_id},
                        {"name": "REGIONS",     "value": "us-east-1"},
                        {"name": "SCAN_TIER",   "value": "quick"},
                    ],
                }],
            },
        )
        print(f"initial scan {scan_id} (quick) started for {conn_id}")
    except Exception as e:
        print(f"WARN: initial scan RunTask failed for {conn_id}: {e}")

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
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
