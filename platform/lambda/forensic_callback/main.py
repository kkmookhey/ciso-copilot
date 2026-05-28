# platform/lambda/forensic_callback/main.py
"""Triggered by the one-time EventBridge rule the run_forensic_scan tool
scheduled. Fires the 'I'll ping you when done' push with the staged
'clean' result tied to the conversation_id."""
from __future__ import annotations
import os

import boto3
from _shared import push as push_mod


DB_CLUSTER_ARN        = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN         = os.environ["DB_SECRET_ARN"]
DB_NAME               = os.environ["DB_NAME"]
APNS_PLATFORM_APP_ARN = os.environ["APNS_PLATFORM_APP_ARN"]

_rds    = boto3.client("rds-data")
_events = boto3.client("events")


def handler(event: dict, context) -> dict:
    scan_id         = event["scan_id"]
    target_arn      = event["target_arn"]
    conversation_id = event.get("conversation_id") or ""
    self_delete     = event.get("self_delete_rule")

    # Prefer tenant_id passed by the caller (run_forensic_scan resolves it
    # from the user's JWT claims). Fall back to conversation lookup only
    # if the caller didn't pre-resolve.
    tenant_id = event.get("tenant_id") or ""
    if not tenant_id and conversation_id:
        rs = _rds.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=(
                "SELECT tenant_id::text FROM conversations "
                "WHERE id = CAST(:c AS UUID) LIMIT 1"
            ),
            parameters=[{"name": "c", "value": {"stringValue": conversation_id}}],
        )
        rows = rs.get("records", [])
        if rows:
            tenant_id = rows[0][0].get("stringValue") or ""
    if not tenant_id:
        print(f"[forensic_callback] no tenant_id resolvable (conv={conversation_id})")
        return {"ok": False}

    # Demo result is staged 'clean'.
    body = "Forensic scan complete — no anomalous activity detected."
    # iOS AppDelegate routes to BriefingView only when finding_id is set.
    # Prefer the original finding_id (voice agent currently passes it as
    # conversation_id since OpenAI Realtime doesn't expose its session id);
    # fall back to scan_id so we always route somewhere.
    routing_id = conversation_id or scan_id
    push_mod.notify_tool_completion(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        body=body,
        payload={
            "finding_id":        routing_id,
            "kind_label":        "Forensic scan",
            "scan_id":           scan_id,
            "target_arn":        target_arn,
            "speakable_summary": body,
            "result":            "clean",
            "tool_name":         "run_forensic_scan",
        },
        rds=_rds,
        db_cluster_arn=DB_CLUSTER_ARN,
        db_secret_arn=DB_SECRET_ARN,
        db_name=DB_NAME,
        platform_app_arn=APNS_PLATFORM_APP_ARN,
    )

    # Clean up the one-time EventBridge rule so it doesn't accumulate.
    if self_delete:
        try:
            _events.remove_targets(Rule=self_delete, Ids=["1"])
            _events.delete_rule(Name=self_delete)
        except Exception as e:
            print(f"[forensic_callback] rule cleanup failed for {self_delete}: {e}")

    return {"ok": True}
