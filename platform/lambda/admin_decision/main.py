"""GET /admin/tenants/{id}/decision?token=<JWT>

Validates the signed approval/reject token from the admin's email, flips
tenant.status atomically with a single-use nonce check, sends the requester
a "you're in" (or "sorry") email via SES.

CISOBrief-v2.md §10.0.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
APPROVAL_TOKEN_SECRET_NAME = os.environ["APPROVAL_TOKEN_SECRET_NAME"]
DOMAIN = os.environ.get("DOMAIN", "settlingforless.com")

rds_data = boto3.client("rds-data")
ses      = boto3.client("ses")
sm       = boto3.client("secretsmanager")

_signing_key_cache: bytes | None = None


def handler(event: dict, context) -> dict:
    print(json.dumps({"path": "/admin/decision", "event": event}))

    tenant_id = (event.get("pathParameters") or {}).get("id")
    token     = (event.get("queryStringParameters") or {}).get("token")

    if not tenant_id or not token:
        return _html(400, "Missing tenant id or token.")

    try:
        claims = _verify_jwt(token)
    except ValueError as e:
        return _html(401, f"Invalid token: {e}")

    if claims.get("sub") != tenant_id:
        return _html(401, "Token does not match tenant.")
    if claims.get("exp", 0) < int(time.time()):
        return _html(401, "Link expired.")

    decision = claims.get("decision")
    nonce    = claims.get("nonce")
    if decision not in ("approve", "reject") or not nonce:
        return _html(400, "Malformed token.")

    tenant = _get_tenant(tenant_id)
    if not tenant:
        return _html(404, "Tenant not found.")

    if nonce in tenant["nonces"]:
        return _html(409, "This link has already been used.")

    new_status = "approved" if decision == "approve" else "rejected"
    _update_tenant_decision(tenant_id, new_status, nonce)

    requester_email = _get_admin_user_email(tenant_id)
    if requester_email:
        _send_user_email(requester_email, new_status)

    notified = f" {requester_email} has been notified." if requester_email else ""
    return _html(
        200,
        f"Decision recorded: <strong>{new_status}</strong>.{notified}",
        title=f"{new_status.capitalize()}",
    )


# ============================================================================
# Aurora Data API
# ============================================================================

def _get_tenant(tenant_id: str) -> dict[str, Any] | None:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT display_name, status, approval_token_nonces::text "
            "FROM tenants WHERE tenant_id = CAST(:id AS UUID)"
        ),
        parameters=[{"name": "id", "value": {"stringValue": tenant_id}}],
    )
    rows = rs.get("records", [])
    if not rows:
        return None
    r = rows[0]
    return {
        "display_name": r[0].get("stringValue"),
        "status":       r[1].get("stringValue"),
        "nonces":       json.loads(r[2].get("stringValue") or "[]"),
    }


def _update_tenant_decision(tenant_id: str, new_status: str, nonce: str) -> None:
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "UPDATE tenants "
            "SET status                = :status, "
            "    approved_at           = CASE WHEN :status = 'approved' THEN now() ELSE approved_at END, "
            "    approval_token_nonces = approval_token_nonces || jsonb_build_array(:nonce) "
            "WHERE tenant_id = CAST(:id AS UUID)"
        ),
        parameters=[
            {"name": "id",     "value": {"stringValue": tenant_id}},
            {"name": "status", "value": {"stringValue": new_status}},
            {"name": "nonce",  "value": {"stringValue": nonce}},
        ],
    )


def _get_admin_user_email(tenant_id: str) -> str | None:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT email FROM users "
            "WHERE tenant_id = CAST(:id AS UUID) AND role = 'admin' "
            "ORDER BY created_at LIMIT 1"
        ),
        parameters=[{"name": "id", "value": {"stringValue": tenant_id}}],
    )
    rows = rs.get("records", [])
    return rows[0][0].get("stringValue") if rows else None


# ============================================================================
# User notification email
# ============================================================================

def _send_user_email(to_email: str, status: str) -> None:
    if status == "approved":
        subject = "[CISO Copilot] Your access has been approved"
        body = (
            '<div style="font-family:-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;">'
            '<h2 style="margin-top:0;">You\'re in.</h2>'
            "<p>Welcome to CISO Copilot. Open the iOS app, sign in, and start "
            "connecting your clouds.</p>"
            '<p><a href="cisocopilot://app/open" style="background:#2563eb;color:white;'
            'padding:12px 20px;text-decoration:none;border-radius:6px;display:inline-block;">'
            "Open the app</a></p>"
            "</div>"
        )
    else:
        subject = "[CISO Copilot] Your access request was not approved"
        body = (
            '<div style="font-family:-apple-system,sans-serif;max-width:480px;margin:0 auto;padding:24px;">'
            '<h2 style="margin-top:0;">Sorry, your request was not approved at this time.</h2>'
            "<p>If you believe this is in error, reply to this email.</p>"
            "</div>"
        )

    ses.send_email(
        Source=f"no-reply@{DOMAIN}",
        Destination={"ToAddresses": [to_email]},
        Message={
            "Subject": {"Data": subject},
            "Body":    {"Html": {"Data": body}},
        },
    )


# ============================================================================
# JWT HS256 verify (stdlib only)
# ============================================================================

def _signing_key() -> bytes:
    global _signing_key_cache
    if _signing_key_cache is None:
        v = sm.get_secret_value(SecretId=APPROVAL_TOKEN_SECRET_NAME)
        _signing_key_cache = v["SecretString"].encode()
    return _signing_key_cache


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _verify_jwt(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed")
    h_b64, p_b64, sig_b64 = parts
    expected = hmac.new(_signing_key(), f"{h_b64}.{p_b64}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
        raise ValueError("bad signature")
    return json.loads(_b64url_decode(p_b64))


# ============================================================================
# HTML response helper
# ============================================================================

def _html(status: int, message: str, *, title: str = "CISO Copilot") -> dict:
    body = (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head>"
        '<body style="font-family:-apple-system,sans-serif;max-width:480px;margin:48px auto;padding:0 24px;color:#111;">'
        f'<h1 style="margin-top:0;">{title}</h1>'
        f"<p>{message}</p>"
        '<p style="color:#888;font-size:12px;margin-top:32px;">You can close this tab.</p>'
        "</body></html>"
    )
    return {
        "statusCode": status,
        "headers":    {"content-type": "text/html; charset=utf-8"},
        "body":       body,
    }
