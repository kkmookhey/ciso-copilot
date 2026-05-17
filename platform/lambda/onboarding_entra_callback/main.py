"""GET /onboarding/entra/callback?state=...&tenant=...&admin_consent=True

Microsoft redirects here after the customer's Entra admin grants consent.
Authentication is via the `state` query param matching a pending entra
connection we created in /initiate.

We don't get an auth code (admin_consent flow is consent-only — no tokens).
We get the customer's tenant ID in the `tenant` query param. From there,
the scanner Lambda uses our app's client credentials + the customer
tenant ID to call Graph (client-credentials flow).
"""
from __future__ import annotations

import json
import os
import uuid

import boto3

DB_CLUSTER_ARN   = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN    = os.environ["DB_SECRET_ARN"]
DB_NAME          = os.environ["DB_NAME"]
ENTRA_RUNNER_FN  = os.environ.get("ENTRA_RUNNER_FN", "")
APP_DOMAIN       = os.environ.get("APP_DOMAIN", "https://dil1ztnjosz43.cloudfront.net")

rds_data       = boto3.client("rds-data")
lambda_client  = boto3.client("lambda")


def handler(event: dict, context) -> dict:
    qs = event.get("queryStringParameters") or {}
    state           = qs.get("state")
    entra_tenant_id = qs.get("tenant")
    admin_consent   = qs.get("admin_consent", "").lower() == "true"
    err             = qs.get("error")
    err_desc        = qs.get("error_description")

    if err:
        return _html_redirect(
            f"Microsoft returned an error: <strong>{err}</strong><br>{err_desc or ''}",
            success=False,
        )

    if not state or not entra_tenant_id:
        return _html_redirect("Missing state or tenant in the redirect.", success=False)

    if not admin_consent:
        return _html_redirect("Admin consent was not granted.", success=False)

    conn = _get_connection_by_state(state)
    if not conn:
        return _html_redirect("Unknown or expired state token.", success=False)
    if conn["status"] != "pending":
        return _html_redirect(
            "This consent link has already been used.", success=False,
        )

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE cloud_connections "
            "SET status = 'active', "
            "    account_identifier = :etid, "
            "    signals = jsonb_build_object('pull_scan', true, 'alerts', false, 'drift', false), "
            "    updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        ),
        parameters=[
            {"name": "cid",  "value": {"stringValue": conn["conn_id"]}},
            {"name": "etid", "value": {"stringValue": entra_tenant_id}},
        ],
    )

    print(f"entra connection {conn['conn_id']} active for tenant {entra_tenant_id}")

    _enqueue_initial_scan(
        tenant_id       = conn["tenant_id"],
        conn_id         = conn["conn_id"],
        entra_tenant_id = entra_tenant_id,
    )

    return _html_redirect(
        f"Entra tenant <code>{entra_tenant_id}</code> connected. Initial scan running — "
        "results will appear in the app within a few minutes.",
        success=True,
    )


def _get_connection_by_state(state: str) -> dict | None:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT conn_id::text, tenant_id::text, status "
            "FROM cloud_connections "
            "WHERE external_id = :state AND cloud_type = 'entra'"
        ),
        parameters=[{"name": "state", "value": {"stringValue": state}}],
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


def _enqueue_initial_scan(*, tenant_id: str, conn_id: str, entra_tenant_id: str) -> None:
    if not ENTRA_RUNNER_FN:
        print("WARN: ENTRA_RUNNER_FN not configured; skipping initial scan")
        return

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
            {"name": "scope", "value": {"stringValue": json.dumps({"entra_tenant_id": entra_tenant_id})}},
        ],
    )

    try:
        lambda_client.invoke(
            FunctionName   = ENTRA_RUNNER_FN,
            InvocationType = "Event",
            Payload=json.dumps({
                "scan_id":         scan_id,
                "tenant_id":       tenant_id,
                "conn_id":         conn_id,
                "entra_tenant_id": entra_tenant_id,
            }).encode(),
        )
        print(f"entra scan {scan_id} enqueued")
    except Exception as e:
        print(f"WARN: entra scan invoke failed: {e}")


def _html_redirect(message: str, *, success: bool) -> dict:
    color = "#10b981" if success else "#ef4444"
    icon = "✓" if success else "✗"
    body = (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>CISO Copilot — Entra</title></head>"
        '<body style="font-family:-apple-system,sans-serif;max-width:480px;margin:64px auto;padding:0 24px;">'
        f'<div style="font-size:48px;color:{color};text-align:center;margin-bottom:16px;">{icon}</div>'
        f'<h1 style="text-align:center;font-weight:600;">'
        + ("Entra tenant connected" if success else "Consent failed")
        + "</h1>"
        f'<p style="text-align:center;color:#475569;line-height:1.6;">{message}</p>'
        f'<p style="text-align:center;margin-top:32px;">'
        f'<a href="{APP_DOMAIN}" style="color:#2563eb;text-decoration:none;font-weight:500;">'
        "← Back to CISO Copilot</a></p>"
        "</body></html>"
    )
    return {
        "statusCode": 200 if success else 400,
        "headers":    {"content-type": "text/html; charset=utf-8"},
        "body":       body,
    }
