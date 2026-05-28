"""POST /me/device-token — register the caller's APNs device token.

Body: {"token": "<hex-string from APNs>"}

The iOS app calls UIApplication.registerForRemoteNotifications() after
the user grants notification permission; the resulting token is POSTed
here so subsequent pushes (from event_router, ai_supply_chain_matcher,
shasta_runner_entra, forensic_callback) can reach the device.
"""
from __future__ import annotations
import json
import os
import re

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

_HEX_TOKEN = re.compile(r"^[0-9a-fA-F]{32,256}$")


def handler(event: dict, context) -> dict:
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    sso_subject = _resolve_subject(claims)
    if not sso_subject:
        return _resp(401, {"error": "no_subject_in_jwt"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    token = (body.get("token") or "").strip()
    if not token or not _HEX_TOKEN.match(token):
        return _resp(400, {"error": "invalid_token", "detail": "expected APNs hex token (32-256 chars)"})

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE users SET device_token = :tok "
            "WHERE sso_subject = :s "
            "RETURNING email"
        ),
        parameters=[
            {"name": "tok", "value": {"stringValue": token}},
            {"name": "s",   "value": {"stringValue": sso_subject}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return _resp(404, {"error": "user_not_found"})
    email = rows[0][0].get("stringValue", "")
    print(f"[device-token] registered: email={email} token_prefix={token[:8]}")
    return _resp(200, {"registered": True, "email": email})


def _resolve_subject(claims: dict) -> str | None:
    """Canonical subject extraction. Federated logins keep the upstream IdP
    sub under identities[0].userId; bare claims.sub is the Cognito pool sub
    (per CLAUDE.md). users.sso_subject stores the upstream value."""
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
