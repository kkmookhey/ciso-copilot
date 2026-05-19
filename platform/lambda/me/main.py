"""GET /me — returns the caller's user + tenant status.

CISOBrief-v2.md §10.0. The iOS app polls every 30s during pending-approval
to detect when an admin has flipped status to 'approved'.

Response shape:
  200 {
    "user":   {"email": str, "role": "admin"|"member"},
    "tenant": {"tenant_id": uuid, "display_name": str,
               "status": "pending"|"approved"|"rejected"|"suspended"}
  }
  200 {"user": null, "tenant": null}   // user row not found yet
  401 {"error": "..."}                  // missing JWT claims
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")


def handler(event: dict, context) -> dict:
    claims = (
        event.get("requestContext", {})
             .get("authorizer", {})
             .get("claims", {})
    )
    sso_subject = _resolve_subject(claims)
    if not sso_subject:
        return _resp(401, {"error": "no_subject_in_jwt"})

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT u.email, u.role, t.tenant_id::text, t.display_name, t.status "
            "FROM users u "
            "JOIN tenants t ON t.tenant_id = u.tenant_id "
            "WHERE u.sso_subject = :s "
            "LIMIT 1"
        ),
        parameters=[{"name": "s", "value": {"stringValue": sso_subject}}],
    )

    rows = rs.get("records", [])
    if not rows:
        return _resp(200, {"user": None, "tenant": None})

    r = rows[0]
    return _resp(200, {
        "user": {
            "email": r[0].get("stringValue"),
            "role":  r[1].get("stringValue"),
        },
        "tenant": {
            "tenant_id":    r[2].get("stringValue"),
            "display_name": r[3].get("stringValue"),
            "status":       r[4].get("stringValue"),
        },
    })


def _resolve_subject(claims: dict[str, Any]) -> str | None:
    """For federated users, prefer the upstream IdP's 'userId' from the
    identities claim — that's what we stored in users.sso_subject. Fall back
    to Cognito's own 'sub' if identities isn't present.
    """
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
