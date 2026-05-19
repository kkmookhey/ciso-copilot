"""POST /onboarding/aws/initiate

JWT-authed. Generates a one-time external_id, creates a pending
cloud_connections row, returns the CFN console deep-link URL the iOS / web
app deep-links the user into.

Response:
  {
    "connection_id":  "uuid",
    "external_id":    "uuid",
    "cfn_url":        "https://console.aws.amazon.com/cloudformation/...",
    "template_url":   "https://cdn.settlingforless.com/cfn/aws-onboard.yaml"
  }
"""
from __future__ import annotations

import json
import os
import secrets
import urllib.parse
import uuid

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
CFN_TEMPLATE_BUCKET  = os.environ["CFN_TEMPLATE_BUCKET"]
CFN_TEMPLATE_KEY     = os.environ["CFN_TEMPLATE_KEY"]
COMPLETE_WEBHOOK_URL = os.environ["COMPLETE_WEBHOOK_URL"]
OUR_ACCOUNT_ID = os.environ["OUR_ACCOUNT_ID"]
CENTRAL_EVENT_BUS_ARN = os.environ["CENTRAL_EVENT_BUS_ARN"]

rds_data = boto3.client("rds-data")
s3       = boto3.client("s3", config=__import__("botocore").client.Config(signature_version="s3v4"))


def handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    display_name = (body.get("display_name") or "AWS Account").strip()

    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    conn_id     = str(uuid.uuid4())
    external_id = secrets.token_urlsafe(24)

    # Secrets Manager ARN where the role_arn + external_id will be stored once
    # the customer's CFN custom resource calls /complete. Pre-allocated so the
    # row's NOT NULL constraint has a value.
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
            "VALUES (CAST(:cid AS UUID), CAST(:tid AS UUID), 'aws', :name, "
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

    # CloudFormation Console rejects non-S3 templateURLs. Our CDN URL won't
    # work; the S3 bucket itself is private. Presign a short-lived S3 GET so
    # Console can fetch it without granting the customer's IAM principal
    # access to our bucket.
    template_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": CFN_TEMPLATE_BUCKET, "Key": CFN_TEMPLATE_KEY},
        ExpiresIn=3600,  # 1 hour — enough time to click through CFN review
    )

    cfn_url = _build_cfn_deep_link(conn_id, external_id, template_url)

    return _resp(200, {
        "connection_id": conn_id,
        "external_id":   external_id,
        "cfn_url":       cfn_url,
        "template_url":  template_url,
    })


def _build_cfn_deep_link(conn_id: str, external_id: str, template_url: str) -> str:
    """One-click CloudFormation console deep link with our template + params pre-filled."""
    params = {
        "stackName":   f"ciso-copilot-{conn_id[:8]}",
        "templateURL": template_url,
        "param_CisoCopilotAccountId": OUR_ACCOUNT_ID,
        "param_ExternalId":           external_id,
        "param_CentralEventBusArn":   CENTRAL_EVENT_BUS_ARN,
        "param_CompleteWebhookUrl":   COMPLETE_WEBHOOK_URL,
        "param_ConnectionId":         conn_id,
        # Default to false — most production AWS accounts already have a
        # Config delivery channel (limit: 1 per account/region). We still
        # receive Config item changes via the EventBridge forwarding rule,
        # which is created unconditionally. Customer can flip this to true
        # in the CFN review step if their account has no Config recorder yet.
        "param_EnableAwsConfig":      "false",
    }
    query = urllib.parse.urlencode(params)
    return f"https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?{query}"


def _resolve_tenant_id(event: dict) -> str | None:
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    sso_subject = _subject_from_claims(claims)
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


def _subject_from_claims(claims: dict) -> str | None:
    raw = claims.get("identities")
    if raw:
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(ids, dict):
                ids = [ids]
            if ids:
                return ids[0].get("userId") or claims.get("sub")
        except (TypeError, ValueError):
            pass
    return claims.get("sub")


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
