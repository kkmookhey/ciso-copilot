"""Fan-out hook for the autonomous CRITICAL-finding Slack broadcast.

Owns ONE responsibility: best-effort SQS publish from any scanner's
writer when severity='critical' AND status='fail'. Failures log loudly
(so the drift metric below catches them) but are NEVER propagated to
the caller — a missed broadcast is recoverable (the finding is in
Aurora; the next scan's flip re-fires), a failed scanner write is data
loss.

EMF emission: every critical-fail finding emits CriticalFailWritten;
successful SQS publish emits BroadcastQueued; SQS publish failure emits
BroadcastFanoutFailed. CloudWatch's drift alarm watches
(CriticalFailWritten - BroadcastQueued) > 2 / hour, which catches
silent failures the swallow-and-log behavior would otherwise hide
(e.g. a missing IAM grant or unset env var in a new scanner Lambda).

Scanners that don't have sqs:SendMessage granted MUST leave
AUTONOMOUS_BROADCAST_QUEUE_URL unset; the hook then short-circuits.
"""
from __future__ import annotations
import json
import os
import time

import boto3

_sqs = boto3.client("sqs")


def _emit_emf_metric(metric_name: str, value: int = 1) -> None:
    """Emit an EMF-formatted log line so CloudWatch parses it as a metric.

    Cheaper than a separate PutMetricData call (free; the log line is
    already going to CloudWatch) and aggregates across invocations on
    the same minute. See AWS docs:
    https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html
    """
    print(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "Shasta/AutonomousBroadcast",
                "Dimensions": [[]],
                "Metrics": [{"Name": metric_name, "Unit": "Count"}],
            }],
        },
        metric_name: value,
    }))


def publish_if_critical(*, tenant_id: str, finding_id: str, scan_id: str,
                        severity: str, status: str) -> None:
    if severity != "critical" or status != "fail":
        return
    # Emit the "critical-fail finding written" metric for drift comparison.
    # Done BEFORE the env-var check so we count every critical-fail row,
    # regardless of whether the queue is wired up — that's exactly what
    # the drift alarm needs to detect a missing IAM grant.
    _emit_emf_metric("CriticalFailWritten")

    queue_url = os.environ.get("AUTONOMOUS_BROADCAST_QUEUE_URL")
    if not queue_url:
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
        _emit_emf_metric("BroadcastQueued")
    except Exception as e:
        print(f"[broadcast_fanout] publish failed: {type(e).__name__}: {e}")
        _emit_emf_metric("BroadcastFanoutFailed")
