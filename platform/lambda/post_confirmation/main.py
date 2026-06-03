"""Cognito Post-Confirmation trigger.

Runs once per user on first successful sign-in via federation.
CISOBrief-v2.md §10.0.

Flow:
  1. Extract email + sso_provider + sso_subject from the event.
  2. Find tenant for email_domain. If none, create one in 'pending' status,
     create the user as 'admin', sign an Approve/Reject JWT, email the
     approval recipient via SES.
  3. If tenant exists, link user as 'member'.
  4. Always return the event unchanged so Cognito completes the sign-in.

Auth gating happens elsewhere — the API authorizer rejects requests when
tenant.status != 'approved', so a 'pending' tenant's user can sign in but
cannot use any endpoint except /me.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from typing import Any

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
APPROVAL_RECIPIENT          = os.environ["APPROVAL_RECIPIENT"]
DOMAIN                      = os.environ["DOMAIN"]
# Base URL for the Approve/Reject email links. Falls back to api.<DOMAIN> for
# the eventual DNS state; in dev we override to the API Gateway invoke URL.
API_BASE_URL                = os.environ.get("API_BASE_URL", f"https://api.{DOMAIN}/v1")
APPROVAL_TOKEN_SECRET_NAME  = os.environ["APPROVAL_TOKEN_SECRET_NAME"]

rds_data = boto3.client("rds-data")
ses      = boto3.client("ses")
sm       = boto3.client("secretsmanager")

# Module-level signing-key cache (Lambda execution context reuse).
_signing_key_cache: bytes | None = None

# Microsoft consumer-account tenant ID. The /organizations OIDC issuer rejects
# these at the IdP layer; this is a backup check.
MS_PERSONAL_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"

def _tenant_key(email: str) -> tuple[str, str]:
    """Returns (tenant_key, display_name) — the unique identifier used to
    find/create a tenant. Always the full email: every user gets their own
    isolated workspace.

    We deliberately do NOT key on email_domain. Doing so made the first user
    at a domain create a tenant that every subsequent colleague auto-joined as
    a 'member' with full read access to each other's cloud connections and
    findings — the classic multi-tenant SaaS leak. Sharing a workspace across
    colleagues must be an explicit invite flow, never an implicit domain match.
    """
    return (email, email)


def handler(event: dict, context) -> dict:
    print(json.dumps({"trigger": "PostConfirmation", "event": event}))

    attrs = event.get("request", {}).get("userAttributes", {})
    email = (attrs.get("email") or "").lower().strip()
    if not email or "@" not in email:
        print("skip: no valid email attribute")
        return event

    sso_provider, sso_subject = _resolve_identity(attrs)
    if not sso_subject:
        print("skip: no SSO identity in claims")
        return event

    if sso_provider == "microsoft" and attrs.get("custom:tid") == MS_PERSONAL_TENANT:
        print(f"skip: personal Microsoft account ({email})")
        return event

    tenant_key, tenant_display = _tenant_key(email)
    tenant = _find_tenant_by_key(tenant_key)
    if tenant is None:
        tenant_id = str(uuid.uuid4())
        _create_tenant(tenant_id, tenant_key, tenant_display)
        _upsert_user(tenant_id, email, sso_provider, sso_subject, role="admin")
        # Email failure must not block sign-in (DNS-pending, SES-throttled, etc.).
        # The admin can also approve via the AWS console; we log loudly here.
        try:
            _send_approval_email(tenant_id, tenant_display, email)
        except Exception as ses_err:
            print(f"WARN: approval email send failed for tenant {tenant_id}: {ses_err}")
        print(f"created pending tenant {tenant_id} for {tenant_key}")
    else:
        _upsert_user(tenant["tenant_id"], email, sso_provider, sso_subject, role="member")
        print(f"linked {email} to existing tenant {tenant['tenant_id']} (status={tenant['status']})")

    return event


# ============================================================================
# Identity resolution
# ============================================================================

def _resolve_identity(attrs: dict) -> tuple[str, str | None]:
    """Returns (sso_provider, sso_subject) extracted from Cognito attrs.

    For federated users, Cognito surfaces the IdP identity in the 'identities'
    attribute as a JSON array string with keys: providerName, userId.
    """
    raw = attrs.get("identities")
    if not raw:
        return ("cognito", attrs.get("sub"))
    try:
        ids = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return ("unknown", None)
    if not ids:
        return ("unknown", None)
    first = ids[0]
    provider_name = (first.get("providerName") or "").lower()
    subject = first.get("userId")
    # Per-tenant Microsoft IdPs we lazy-create are named "MS-<29hex>"; the
    # base Microsoft IdP is just "microsoft". Normalize both to "microsoft"
    # so downstream filters don't have to know about every per-tenant alias.
    if "microsoft" in provider_name or provider_name.startswith("ms-"):
        return ("microsoft", subject)
    if "google" in provider_name:
        return ("google", subject)
    return (provider_name or "unknown", subject)


# ============================================================================
# Aurora Data API
# ============================================================================

def _execute(sql: str, params: list | None = None) -> dict:
    return rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=sql,
        parameters=params or [],
    )


def _find_tenant_by_key(tenant_key: str) -> dict[str, Any] | None:
    """Lookup is keyed on tenants.email_domain — which for personal-email
    domains we use to store the *full email*, not just the domain part. The
    column name is legacy; semantically it's now the tenant identity key.
    """
    rs = _execute(
        "SELECT tenant_id::text, status FROM tenants WHERE email_domain = :k",
        [{"name": "k", "value": {"stringValue": tenant_key}}],
    )
    rows = rs.get("records", [])
    if not rows:
        return None
    r = rows[0]
    return {"tenant_id": r[0].get("stringValue"), "status": r[1].get("stringValue")}


def _create_tenant(tenant_id: str, tenant_key: str, display_name: str) -> None:
    _execute(
        """INSERT INTO tenants (tenant_id, display_name, email_domain, status)
           VALUES (CAST(:id AS UUID), :name, :key, 'pending')""",
        [
            {"name": "id",   "value": {"stringValue": tenant_id}},
            {"name": "name", "value": {"stringValue": display_name}},
            {"name": "key",  "value": {"stringValue": tenant_key}},
        ],
    )


def _upsert_user(tenant_id: str, email: str, sso_provider: str, sso_subject: str, role: str) -> None:
    _execute(
        """INSERT INTO users (user_id, tenant_id, email, sso_provider, sso_subject, role)
           VALUES (CAST(:uid AS UUID), CAST(:tid AS UUID), :email, :p, :s, :r)
           ON CONFLICT (sso_provider, sso_subject) DO UPDATE
             SET email = excluded.email""",
        [
            {"name": "uid",   "value": {"stringValue": str(uuid.uuid4())}},
            {"name": "tid",   "value": {"stringValue": tenant_id}},
            {"name": "email", "value": {"stringValue": email}},
            {"name": "p",     "value": {"stringValue": sso_provider}},
            {"name": "s",     "value": {"stringValue": sso_subject}},
            {"name": "r",     "value": {"stringValue": role}},
        ],
    )


# ============================================================================
# Approval email
# ============================================================================

def _send_approval_email(tenant_id: str, email_domain: str, requester_email: str) -> None:
    approve_url = _decision_url(tenant_id, "approve")
    reject_url  = _decision_url(tenant_id, "reject")

    body_html = f"""
    <div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;padding:24px;">
      <h2 style="margin-top:0;">New CISO Copilot access request</h2>
      <table cellpadding="6" style="border-collapse:collapse;font-size:14px;">
        <tr><td style="color:#666;">Requester</td><td><strong>{requester_email}</strong></td></tr>
        <tr><td style="color:#666;">Tenant</td><td><strong>{email_domain}</strong></td></tr>
        <tr><td style="color:#666;">Tenant ID</td><td><code>{tenant_id}</code></td></tr>
      </table>
      <p style="margin-top:24px;">
        <a href="{approve_url}" style="background:#16a34a;color:white;padding:12px 20px;text-decoration:none;border-radius:6px;margin-right:8px;display:inline-block;">Approve</a>
        <a href="{reject_url}"  style="background:#dc2626;color:white;padding:12px 20px;text-decoration:none;border-radius:6px;display:inline-block;">Reject</a>
      </p>
      <p style="color:#888;font-size:12px;margin-top:32px;">Links expire in 7 days. Single-use.</p>
    </div>
    """

    # Send FROM the verified domain so Gmail-side DKIM/SPF/DMARC checks pass.
    # Until 2026-05-18 we were sending Source=kkmookhey@gmail.com → Gmail
    # silently spam-foldered or dropped because a Gmail From: coming via AWS
    # IPs looks like spoofing. DNS for settlingforless.com is now verified.
    ses.send_email(
        Source=f"CISO Copilot <no-reply@{DOMAIN}>",
        Destination={"ToAddresses": [APPROVAL_RECIPIENT]},
        Message={
            "Subject": {"Data": f"[CISO Copilot] Access request: {email_domain}"},
            "Body":    {"Html": {"Data": body_html}},
        },
    )


def _decision_url(tenant_id: str, decision: str) -> str:
    token = _sign_jwt({
        "iss":      "ciso-copilot",
        "sub":      tenant_id,
        "decision": decision,
        "nonce":    secrets.token_urlsafe(16),
        "exp":      int(time.time()) + 7 * 24 * 60 * 60,
    })
    return f"{API_BASE_URL}/admin/tenants/{tenant_id}/decision?token={token}"


# ============================================================================
# JWT HS256 (stdlib only — no PyJWT layer needed)
# ============================================================================

def _signing_key() -> bytes:
    global _signing_key_cache
    if _signing_key_cache is None:
        v = sm.get_secret_value(SecretId=APPROVAL_TOKEN_SECRET_NAME)
        _signing_key_cache = v["SecretString"].encode()
    return _signing_key_cache


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _sign_jwt(payload: dict) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    h = _b64url(json.dumps(header,  separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_signing_key(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"
