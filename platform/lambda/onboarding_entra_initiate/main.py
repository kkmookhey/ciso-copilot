"""POST /onboarding/entra/initiate

JWT-authed. Creates a pending cloud_connection (cloud_type='entra'),
returns the Microsoft admin-consent URL the customer's Entra admin
opens in a browser to grant Graph API access to our app.

Response:
  {
    "connection_id": "uuid",
    "state":         "<one-time>",
    "consent_url":   "https://login.microsoftonline.com/organizations/adminconsent?..."
  }
"""
from __future__ import annotations

import json
import os
import secrets
import urllib.parse
import uuid

import boto3

DB_CLUSTER_ARN  = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN   = os.environ["DB_SECRET_ARN"]
DB_NAME         = os.environ["DB_NAME"]
ENTRA_APP_ID    = os.environ["ENTRA_APP_ID"]
CALLBACK_URL    = os.environ["ENTRA_CALLBACK_URL"]
OUR_ACCOUNT_ID  = os.environ["OUR_ACCOUNT_ID"]

rds_data = boto3.client("rds-data")


def handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    display_name = (body.get("display_name") or "Entra Tenant").strip()

    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    conn_id = str(uuid.uuid4())
    state   = secrets.token_urlsafe(24)

    secret_arn_placeholder = (
        f"arn:aws:secretsmanager:us-east-1:{OUR_ACCOUNT_ID}"
        f":secret:ciso-copilot/connections/{conn_id}"
    )

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO cloud_connections "
            "  (conn_id, tenant_id, cloud_type, display_name, status, "
            "   credentials_secret_arn, external_id) "
            "VALUES (CAST(:cid AS UUID), CAST(:tid AS UUID), 'entra', :name, "
            "        'pending', :secret_arn, :state)"
        ),
        parameters=[
            {"name": "cid",        "value": {"stringValue": conn_id}},
            {"name": "tid",        "value": {"stringValue": tenant_id}},
            {"name": "name",       "value": {"stringValue": display_name}},
            {"name": "secret_arn", "value": {"stringValue": secret_arn_placeholder}},
            {"name": "state",      "value": {"stringValue": state}},
        ],
    )

    # Admin consent URL. Using /organizations limits to work/school accounts
    # (rejects personal Microsoft accounts at the Microsoft layer).
    params = {
        "client_id":    ENTRA_APP_ID,
        "state":        state,
        "redirect_uri": CALLBACK_URL,
    }
    consent_url = (
        f"https://login.microsoftonline.com/organizations/adminconsent"
        f"?{urllib.parse.urlencode(params)}"
    )

    return _resp(200, {
        "connection_id": conn_id,
        "state":         state,
        "consent_url":   consent_url,
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
