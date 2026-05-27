"""POST /onboarding/azure/initiate

JWT-authed. Creates a pending cloud_connections row for Azure, returns the
one-time external_id and the curl-pipe command the customer runs in
Azure Cloud Shell.

Response:
  {
    "connection_id": "uuid",
    "external_id":   "<one-time>",
    "script_url":    "https://cdn.settlingforless.com/azure/onboard.sh",
    "run_command":   "curl -fsSL https://.../azure/onboard.sh | CISO_COMPLETE_URL=<url> bash -s -- <ext_id>"
  }

The CISO_COMPLETE_URL env var is prefixed into the run_command so the
onboard.sh script receives the correct API endpoint without any hardcoded
fallback — AZURE_COMPLETE_URL is set by CDK from config.apiBaseUrl.
"""
from __future__ import annotations

import json
import os
import secrets
import uuid

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
SCRIPT_URL        = os.environ["AZURE_SCRIPT_URL"]
AZURE_COMPLETE_URL = os.environ["AZURE_COMPLETE_URL"]
OUR_ACCOUNT_ID    = os.environ["OUR_ACCOUNT_ID"]

rds_data = boto3.client("rds-data")


def handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    display_name = (body.get("display_name") or "Azure Subscription").strip()

    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    conn_id     = str(uuid.uuid4())
    external_id = secrets.token_urlsafe(24)

    secret_arn_placeholder = (
        f"arn:aws:secretsmanager:us-east-1:{OUR_ACCOUNT_ID}"
        f":secret:ciso-copilot/connections/{conn_id}"
    )

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO cloud_connections "
            "  (conn_id, tenant_id, cloud_type, display_name, status, "
            "   credentials_secret_arn, external_id) "
            "VALUES (CAST(:cid AS UUID), CAST(:tid AS UUID), 'azure', :name, "
            "        'pending', :secret_arn, :ext_id)"
        ),
        parameters=[
            {"name": "cid",        "value": {"stringValue": conn_id}},
            {"name": "tid",        "value": {"stringValue": tenant_id}},
            {"name": "name",       "value": {"stringValue": display_name}},
            {"name": "secret_arn", "value": {"stringValue": secret_arn_placeholder}},
            {"name": "ext_id",     "value": {"stringValue": external_id}},
        ],
    )

    return _resp(200, {
        "connection_id": conn_id,
        "external_id":   external_id,
        "script_url":    SCRIPT_URL,
        "run_command":   f"curl -fsSL {SCRIPT_URL} | CISO_COMPLETE_URL={AZURE_COMPLETE_URL} bash -s -- {external_id}",
    })


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
