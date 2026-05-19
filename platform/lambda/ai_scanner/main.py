"""SQS-triggered handler for the AI scanner Lambda.

Event shape (SQS batch):
  {"Records": [{"body": "{\"scan_id\": \"...\", \"tenant_id\": \"...\", ...}"}]}

For each record, run a full scan + write the result. Errors raise so SQS
retries the message (up to maxReceiveCount, then DLQ).
"""
from __future__ import annotations

import json
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai_scanner")


def handler(event: dict, context) -> dict:
    records = event.get("Records") or []
    log.info("ai_scanner invoked with %d record(s)", len(records))
    for r in records:
        body = json.loads(r.get("body") or "{}")
        scan_id = body.get("scan_id")
        log.info("scan_id=%s (stub — implementation in Tasks 4+)", scan_id)
    return {"statusCode": 200, "body": json.dumps({"scans_processed": len(records)})}
