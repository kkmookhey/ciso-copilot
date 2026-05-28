# platform/lambda/tools/run_forensic_scan.py
"""Staged forensic-scan tool for the recorded demo.

Returns a scan_id + ETA immediately, schedules a one-time EventBridge rule
to fire 60s later. That rule triggers the callback-push helper (Task 17)
which delivers the staged 'clean' result as an APNs push tied back to the
conversation_id.
"""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from tools.main import register, subject_from_claims

_events = boto3.client("events")
_rds    = boto3.client("rds-data")
_DB_CLUSTER_ARN = os.environ.get("DB_CLUSTER_ARN", "")
_DB_SECRET_ARN  = os.environ.get("DB_SECRET_ARN", "")
_DB_NAME        = os.environ.get("DB_NAME", "")
_CALLBACK_FN_ARN = os.environ.get("FORENSIC_CALLBACK_FN_ARN", "")
_ETA_SECONDS = 60


@register("run_forensic_scan")
def handle(args: dict, claims: dict) -> dict:
    target_arn       = args["target_arn"]
    check_kind       = args["check_kind"]
    # conversation_id is optional now — the callback can fire push using
    # tenant_id alone, which we resolve from the caller's claims.
    conversation_id  = args.get("conversation_id") or ""
    tenant_id        = _resolve_tenant_id(claims)
    if not tenant_id:
        return {
            "scan_id":   "",
            "eta_seconds": 0,
            "speakable": "Couldn't start the forensic scan — no tenant on the caller.",
        }
    scan_id          = f"scan-{uuid.uuid4().hex[:12]}"

    _schedule_callback(
        scan_id=scan_id, target_arn=target_arn, check_kind=check_kind,
        conversation_id=conversation_id, tenant_id=tenant_id,
    )

    return {
        "scan_id":      scan_id,
        "eta_seconds":  _ETA_SECONDS,
        "speakable":    f"Forensic scan started. I'll ping you when it's done — about a minute.",
    }


def _resolve_tenant_id(claims: dict) -> str:
    """Look up the caller's tenant_id via the canonical sso_subject path."""
    sub = subject_from_claims(claims)
    if not sub or not _DB_CLUSTER_ARN:
        return ""
    rs = _rds.execute_statement(
        resourceArn=_DB_CLUSTER_ARN, secretArn=_DB_SECRET_ARN, database=_DB_NAME,
        sql="SELECT tenant_id::text FROM users WHERE sso_subject = :s LIMIT 1",
        parameters=[{"name": "s", "value": {"stringValue": sub}}],
    )
    rows = rs.get("records", [])
    return rows[0][0].get("stringValue") if rows else ""


def _schedule_callback(*, scan_id: str, target_arn: str, check_kind: str,
                       conversation_id: str, tenant_id: str) -> None:
    if not _CALLBACK_FN_ARN:
        print("FORENSIC_CALLBACK_FN_ARN not set — skipping scheduling (test mode)")
        return
    fire_at = datetime.now(timezone.utc) + timedelta(seconds=_ETA_SECONDS)
    rule_name = f"forensic-{scan_id}"
    cron_expr = fire_at.strftime("cron(%M %H %d %m ? %Y)")
    _events.put_rule(
        Name=rule_name,
        ScheduleExpression=cron_expr,
        State="ENABLED",
    )
    _events.put_targets(
        Rule=rule_name,
        Targets=[{
            "Id":    "1",
            "Arn":   _CALLBACK_FN_ARN,
            "Input": json.dumps({
                "scan_id": scan_id, "target_arn": target_arn,
                "check_kind": check_kind,
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
                "self_delete_rule": rule_name,
            }),
        }],
    )
