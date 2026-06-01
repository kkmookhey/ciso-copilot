"""Fan-out hook for the autonomous CRITICAL-finding Slack broadcast.

Owns ONE responsibility: best-effort SQS publish from any scanner's
writer when severity='critical' AND status='fail'. Failures log loudly
(so the drift metric in 2.5 catches them) but are NEVER propagated to
the caller — a missed broadcast is recoverable (the finding is in
Aurora; the next scan's flip re-fires), a failed scanner write is data
loss.

Scanners that don't have sqs:SendMessage granted MUST leave
AUTONOMOUS_BROADCAST_QUEUE_URL unset; the hook then short-circuits.
"""
from __future__ import annotations
import json
import os

import boto3

_sqs = boto3.client("sqs")


def publish_if_critical(*, tenant_id: str, finding_id: str, scan_id: str,
                        severity: str, status: str) -> None:
    queue_url = os.environ.get("AUTONOMOUS_BROADCAST_QUEUE_URL")
    if not queue_url:
        return
    if severity != "critical" or status != "fail":
        return
    try:
        _sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({
                "tenant_id": tenant_id,
                "finding_id": finding_id,
                "scan_id": scan_id,
            }),
        )
    except Exception as e:
        # Log but swallow. Drift metric in 2.5 detects systematic loss.
        print(f"[broadcast_fanout] publish failed: {type(e).__name__}: {e}")
