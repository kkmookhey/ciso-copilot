#!/usr/bin/env python3
"""Re-send the access-approval email for a given pending tenant.

Useful when:
  - A user's tenant was created manually (e.g., via migration) and the
    post_confirmation Lambda's email path didn't fire.
  - The original email was lost / SES bounced / DKIM was still pending.

Usage:
  python3 scripts/send_approval_email.py <tenant_id> [--requester=<email>]

Mirrors the JWT + HTML body produced by platform/lambda/post_confirmation/main.py.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time

import boto3
from dotenv import load_dotenv

# Load platform/.env if present (script lives in repo root scripts/ but reads CDK env)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "platform", ".env"))


def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"Missing env var: {name}. Set in platform/.env or export it.")
    return v


REGION              = "us-east-1"
DB_CLUSTER_ARN      = _required("DB_CLUSTER_ARN")
DB_SECRET_ARN       = _required("DB_SECRET_ARN")
DB_NAME             = "ciso_copilot"
APPROVAL_RECIPIENT  = _required("APPROVAL_RECIPIENT")
SENDER              = "CISO Copilot <no-reply@settlingforless.com>"
API_BASE_URL        = _required("API_BASE_URL")
SIGNING_KEY_SECRET  = "ciso-copilot/approval-signing-key"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tenant_id")
    ap.add_argument("--requester", help="Override requester email shown in body. Defaults to the tenant's first user.")
    args = ap.parse_args()

    sm = boto3.client("secretsmanager", region_name=REGION)
    ses = boto3.client("ses", region_name=REGION)
    rds_data = boto3.client("rds-data", region_name=REGION)

    # Look up tenant + first user
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql="SELECT t.display_name, t.email_domain, t.status, u.email "
            "FROM tenants t LEFT JOIN users u ON u.tenant_id = t.tenant_id "
            "WHERE t.tenant_id = CAST(:t AS UUID) ORDER BY u.created_at LIMIT 1",
        parameters=[{"name": "t", "value": {"stringValue": args.tenant_id}}],
    )
    rows = rs.get("records", [])
    if not rows:
        print(f"tenant {args.tenant_id} not found", file=sys.stderr)
        return 1
    r = rows[0]
    display_name  = r[0].get("stringValue")
    email_domain  = r[1].get("stringValue")
    status        = r[2].get("stringValue")
    first_user    = r[3].get("stringValue") if not r[3].get("isNull") else "(no users)"
    requester = args.requester or first_user

    if status != "pending":
        print(f"tenant status is '{status}' (not 'pending') — sending anyway", file=sys.stderr)

    key = sm.get_secret_value(SecretId=SIGNING_KEY_SECRET)["SecretString"].encode()
    approve_url = _decision_url(args.tenant_id, "approve", key)
    reject_url  = _decision_url(args.tenant_id, "reject",  key)

    body_html = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;padding:24px;">
      <h2 style="margin-top:0;">New CISO Copilot access request</h2>
      <table cellpadding="6" style="border-collapse:collapse;font-size:14px;">
        <tr><td style="color:#666;">Requester</td><td><strong>{requester}</strong></td></tr>
        <tr><td style="color:#666;">Tenant</td><td><strong>{display_name or email_domain}</strong></td></tr>
        <tr><td style="color:#666;">Tenant ID</td><td><code>{args.tenant_id}</code></td></tr>
      </table>
      <p style="margin-top:24px;">
        <a href="{approve_url}" style="background:#16a34a;color:white;padding:12px 20px;text-decoration:none;border-radius:6px;margin-right:8px;display:inline-block;">Approve</a>
        <a href="{reject_url}"  style="background:#dc2626;color:white;padding:12px 20px;text-decoration:none;border-radius:6px;display:inline-block;">Reject</a>
      </p>
      <p style="color:#888;font-size:12px;margin-top:32px;">Links expire in 7 days. Single-use.</p>
    </div>
    """

    resp = ses.send_email(
        Source=SENDER,
        Destination={"ToAddresses": [APPROVAL_RECIPIENT]},
        Message={
            "Subject": {"Data": f"[CISO Copilot] Access request: {email_domain}"},
            "Body":    {"Html": {"Data": body_html}},
        },
    )
    print(f"sent. SES MessageId={resp['MessageId']}")
    print(f"approve_url: {approve_url}")
    return 0


def _decision_url(tenant_id: str, decision: str, key: bytes) -> str:
    token = _sign_jwt({
        "iss":      "ciso-copilot",
        "sub":      tenant_id,
        "decision": decision,
        "nonce":    secrets.token_urlsafe(16),
        "exp":      int(time.time()) + 7 * 24 * 60 * 60,
    }, key)
    return f"{API_BASE_URL}/admin/tenants/{tenant_id}/decision?token={token}"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _sign_jwt(payload: dict, key: bytes) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    h = _b64url(json.dumps(header,  separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(key, f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


if __name__ == "__main__":
    sys.exit(main())
