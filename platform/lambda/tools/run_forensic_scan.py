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
from tools.main import register


_events = boto3.client("events")
_CALLBACK_FN_ARN = os.environ.get("FORENSIC_CALLBACK_FN_ARN", "")
_ETA_SECONDS = 60


@register("run_forensic_scan")
def handle(args: dict, claims: dict) -> dict:
    target_arn       = args["target_arn"]
    check_kind       = args["check_kind"]
    conversation_id  = args["conversation_id"]
    scan_id          = f"scan-{uuid.uuid4().hex[:12]}"

    _schedule_callback(
        scan_id=scan_id, target_arn=target_arn, check_kind=check_kind,
        conversation_id=conversation_id,
    )

    return {
        "scan_id":      scan_id,
        "eta_seconds":  _ETA_SECONDS,
        "speakable":    f"Forensic scan started. I'll ping you when it's done — about a minute.",
    }


def _schedule_callback(*, scan_id: str, target_arn: str, check_kind: str,
                       conversation_id: str) -> None:
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
                "check_kind": check_kind, "conversation_id": conversation_id,
                "self_delete_rule": rule_name,
            }),
        }],
    )
