"""Admin endpoints for tenant management (Cognito-authed).

  GET  /admin/tenants                    — list tenants (default filter: status='pending')
  POST /admin/tenants/{id}/decision      — body {decision: "approve"|"reject"}

Authorization: caller must be in ADMIN_EMAILS env var (comma-separated).
This is the in-app alternative to the email-link Approve/Reject flow. Useful
when SES delivery is unreliable or you want a fast list view of pending users.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
ADMIN_EMAILS   = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
DOMAIN         = os.environ.get("DOMAIN", "settlingforless.com")

rds_data = boto3.client("rds-data")
ses      = boto3.client("ses")

ALLOWED_DECISIONS = {"approve", "reject"}


def handler(event: dict, context) -> dict:
    caller_email = _caller_email(event)
    if not caller_email:
        return _resp(401, {"error": "no_caller"})
    if caller_email.lower() not in ADMIN_EMAILS:
        return _resp(403, {"error": "not_an_admin", "caller": caller_email})

    method = event.get("httpMethod", "GET")
    path_params = event.get("pathParameters") or {}

    if method == "GET":
        return _list_tenants(event)
    if method == "POST" and path_params.get("id"):
        return _decision(event, path_params["id"], caller_email)

    return _resp(400, {"error": "unsupported"})


def _list_tenants(event: dict) -> dict:
    qp = event.get("queryStringParameters") or {}
    status_filter = (qp.get("status") or "pending").lower()
    valid = {"pending", "approved", "rejected", "suspended", "all"}
    if status_filter not in valid:
        return _resp(400, {"error": "invalid_status_filter"})

    sql = (
        "SELECT t.tenant_id::text, t.display_name, t.email_domain, t.status, "
        "       t.created_at::text, "
        "       (SELECT u.email FROM users u WHERE u.tenant_id = t.tenant_id ORDER BY u.created_at LIMIT 1) AS first_user "
        "FROM tenants t "
        + ("" if status_filter == "all" else "WHERE t.status = :s ")
        + "ORDER BY t.created_at DESC"
    )
    params = []
    if status_filter != "all":
        params.append({"name": "s", "value": {"stringValue": status_filter}})
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params,
    )

    tenants = []
    for r in rs.get("records", []):
        tenants.append({
            "tenant_id":    r[0].get("stringValue"),
            "display_name": r[1].get("stringValue"),
            "email_domain": r[2].get("stringValue"),
            "status":       r[3].get("stringValue"),
            "created_at":   r[4].get("stringValue"),
            "first_user":   r[5].get("stringValue") if not r[5].get("isNull") else None,
        })
    return _resp(200, {"tenants": tenants})


def _decision(event: dict, tenant_id: str, caller_email: str) -> dict:
    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw_body = base64.b64decode(raw_body).decode()
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    decision = (body.get("decision") or "").lower()
    if decision not in ALLOWED_DECISIONS:
        return _resp(400, {"error": "invalid_decision", "allowed": list(ALLOWED_DECISIONS)})

    new_status = "approved" if decision == "approve" else "rejected"

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE tenants SET status = :s, approved_at = CASE WHEN :s = 'approved' THEN now() ELSE approved_at END "
            "WHERE tenant_id = CAST(:t AS UUID) "
            "RETURNING email_domain, (SELECT email FROM users WHERE tenant_id = CAST(:t AS UUID) ORDER BY created_at LIMIT 1) AS notify_email"
        ),
        parameters=[
            {"name": "s", "value": {"stringValue": new_status}},
            {"name": "t", "value": {"stringValue": tenant_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return _resp(404, {"error": "tenant_not_found"})

    email_domain = rows[0][0].get("stringValue")
    notify_email = rows[0][1].get("stringValue") if not rows[0][1].get("isNull") else None

    email_status = "skipped"
    if notify_email:
        try:
            _notify_user(notify_email, new_status)
            email_status = "sent"
        except Exception as e:
            print(f"WARN: user notification to {notify_email} failed: {e}")
            email_status = "failed"

    print(f"admin {caller_email} -> tenant {tenant_id} ({email_domain}): {new_status}")
    return _resp(200, {
        "tenant_id":     tenant_id,
        "new_status":    new_status,
        "notify_email":  notify_email,
        "email_status":  email_status,
        "decided_by":    caller_email,
        "decided_at":    datetime.now(timezone.utc).isoformat(),
    })


def _notify_user(to_email: str, status: str) -> None:
    if status == "approved":
        subject = "[CISO Copilot] Your access has been approved"
        body = (
            '<div style="font-family:-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;">'
            '<h2 style="margin-top:0;">You\'re in.</h2>'
            "<p>Welcome to CISO Copilot. Sign in to connect your clouds and start seeing posture + alerts.</p>"
            "</div>"
        )
    else:
        subject = "[CISO Copilot] Your access request was not approved"
        body = (
            '<div style="font-family:-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;">'
            '<h2 style="margin-top:0;">Sorry, your request was not approved at this time.</h2>'
            "<p>If you think this is in error, reply to this email.</p>"
            "</div>"
        )
    ses.send_email(
        Source=f"CISO Copilot <no-reply@{DOMAIN}>",
        Destination={"ToAddresses": [to_email]},
        Message={
            "Subject": {"Data": subject},
            "Body":    {"Html": {"Data": body}},
        },
    )


def _caller_email(event: dict) -> str | None:
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    return claims.get("email") or claims.get("cognito:username")


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
