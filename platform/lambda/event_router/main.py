"""Event router — receives every event landed on the central EventBridge bus.

Pipeline (CISOBrief-v2.md §9):
  1. Resolve tenant_id from event['account'] via cloud_connections.
  2. Normalize severity by source-specific rules.
  3. Archive raw event to S3 under raw/<date>/<source>/<event_id>.json.
  4. Insert into events (+ drift_events for kind='drift').
  5. Evaluate push rules; for any match, fire APNs to the tenant's users.

Status: pipeline shell. Source-specific parsers (§9 details) land in the
follow-up commit; for now the router writes the raw payload to S3 and a
minimal events row so the data path is exercised end-to-end.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3

DB_CLUSTER_ARN    = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN     = os.environ["DB_SECRET_ARN"]
DB_NAME           = os.environ["DB_NAME"]
RAW_EVENTS_BUCKET = os.environ["RAW_EVENTS_BUCKET"]

rds_data = boto3.client("rds-data")
s3       = boto3.client("s3")


def handler(event: dict, context) -> dict:
    """EventBridge invokes this with a single event payload."""
    print(json.dumps({"router": "received", "source": event.get("source"), "detail-type": event.get("detail-type")}))

    try:
        source       = event.get("source", "unknown")
        detail_type  = event.get("detail-type", "")
        account_id   = event.get("account")
        fired_at     = event.get("time") or datetime.now(timezone.utc).isoformat()

        if not account_id:
            print(f"DROP: no account in event")
            return {"ok": False, "reason": "no_account"}

        # 1. Resolve tenant + connection
        conn = _find_connection_by_account(account_id)
        if not conn:
            print(f"DROP: no connection for account {account_id} (stranded)")
            _archive_stranded(event, account_id)
            return {"ok": False, "reason": "no_connection"}

        # 2. Archive raw event to S3
        event_id     = str(uuid.uuid4())
        raw_s3_key   = _archive_raw(event, conn["tenant_id"], event_id, source)

        # 3. Normalize → events row
        normalized = _normalize(event, source, detail_type)
        kind       = _classify_kind(source, detail_type, event.get("detail", {}))
        severity   = _severity(source, event.get("detail", {}))

        _insert_event(
            event_id      = event_id,
            tenant_id     = conn["tenant_id"],
            conn_id       = conn["conn_id"],
            kind          = kind,
            source        = source,
            severity      = severity,
            title         = normalized.get("title", detail_type or source),
            description   = normalized.get("description"),
            resource_arn  = normalized.get("resource_arn"),
            actor         = normalized.get("actor"),
            raw_s3_key    = raw_s3_key,
            normalized    = normalized,
            fired_at      = fired_at,
        )

        # 4. Drift extension if applicable
        if kind == "drift":
            _insert_drift(event_id, normalized)

        # 5. Push-rules evaluation — implemented in the follow-up
        # if _should_push(source, severity, event): _send_push(...)

        return {"ok": True, "event_id": event_id, "tenant_id": conn["tenant_id"]}

    except Exception as e:
        print(f"ERROR: router failed: {e}")
        # Don't raise — EventBridge retries, but for a malformed event the
        # retry won't help. Better to drop and continue.
        return {"ok": False, "reason": str(e)}


# ============================================================================
# Connection resolution
# ============================================================================

def _find_connection_by_account(account_id: str) -> dict[str, Any] | None:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT conn_id::text, tenant_id::text FROM cloud_connections "
            "WHERE account_identifier = :a AND cloud_type = 'aws' AND status = 'active' "
            "LIMIT 1"
        ),
        parameters=[{"name": "a", "value": {"stringValue": account_id}}],
    )
    rows = rs.get("records", [])
    if not rows:
        return None
    r = rows[0]
    return {"conn_id": r[0].get("stringValue"), "tenant_id": r[1].get("stringValue")}


# ============================================================================
# S3 raw archive
# ============================================================================

def _archive_raw(event: dict, tenant_id: str, event_id: str, source: str) -> str:
    now = datetime.now(timezone.utc)
    key = f"raw/{now:%Y/%m/%d}/{tenant_id}/{source}/{event_id}.json"
    s3.put_object(
        Bucket=RAW_EVENTS_BUCKET,
        Key=key,
        Body=json.dumps(event).encode(),
        ContentType="application/json",
    )
    return key


def _archive_stranded(event: dict, account_id: str) -> None:
    """Account isn't connected; keep the event in a stranded prefix for inspection."""
    now = datetime.now(timezone.utc)
    key = f"stranded/{now:%Y/%m/%d}/{account_id}/{uuid.uuid4()}.json"
    s3.put_object(
        Bucket=RAW_EVENTS_BUCKET,
        Key=key,
        Body=json.dumps(event).encode(),
        ContentType="application/json",
    )


# ============================================================================
# Normalization (source-specific shapes → unified)
# ============================================================================

def _normalize(event: dict, source: str, detail_type: str) -> dict[str, Any]:
    """Return {title, description, resource_arn, actor}."""
    detail = event.get("detail", {}) or {}

    if source == "aws.guardduty":
        return {
            "title":        detail.get("title", detail_type),
            "description":  detail.get("description"),
            "resource_arn": (detail.get("resource", {}) or {}).get("instanceDetails", {}).get("instanceId"),
            "actor":        None,
        }
    if source == "aws.inspector2":
        return {
            "title":        detail.get("title", detail_type),
            "description":  detail.get("description"),
            "resource_arn": (detail.get("resources") or [{}])[0].get("id"),
            "actor":        None,
        }
    if source == "aws.securityhub":
        finding = (detail.get("findings") or [{}])[0]
        return {
            "title":        finding.get("Title", detail_type),
            "description":  finding.get("Description"),
            "resource_arn": (finding.get("Resources") or [{}])[0].get("Id"),
            "actor":        None,
        }
    if source == "aws.cloudtrail":
        return {
            "title":        detail.get("eventName", detail_type),
            "description":  None,
            "resource_arn": _extract_cloudtrail_resource(detail),
            "actor":        ((detail.get("userIdentity") or {}).get("arn")
                             or (detail.get("userIdentity") or {}).get("userName")),
        }
    if source == "aws.config":
        ci = detail.get("configurationItem", {}) or {}
        return {
            "title":        f"Config change: {ci.get('resourceType')}",
            "description":  ci.get("configurationStateId"),
            "resource_arn": ci.get("ARN") or ci.get("resourceId"),
            "actor":        None,
        }

    # Unknown source — fall back to the raw fields.
    return {
        "title":        detail_type or source,
        "description":  None,
        "resource_arn": None,
        "actor":        None,
    }


def _extract_cloudtrail_resource(detail: dict) -> str | None:
    """Pull the most relevant resource ARN/ID from a CloudTrail event."""
    resources = detail.get("resources") or []
    if resources:
        return resources[0].get("ARN") or resources[0].get("resourceName")
    req_params = detail.get("requestParameters") or {}
    for key in ("bucketName", "roleName", "userName", "groupId", "instanceId", "keyId"):
        if key in req_params:
            return f"{key}:{req_params[key]}"
    return None


def _classify_kind(source: str, detail_type: str, detail: dict) -> str:
    """alert: native security detector. drift: configuration change."""
    if source in ("aws.cloudtrail", "aws.config"):
        return "drift"
    return "alert"


def _severity(source: str, detail: dict) -> str:
    """Normalize source-specific severities to {critical, high, medium, low, info}."""
    if source == "aws.guardduty":
        # GuardDuty: 0-10 scale; 7+ high, 8+ critical
        sev = detail.get("severity", 0)
        if sev >= 8: return "critical"
        if sev >= 7: return "high"
        if sev >= 4: return "medium"
        if sev >= 1: return "low"
        return "info"
    if source == "aws.inspector2":
        label = (detail.get("severity") or "").upper()
        return {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium",
                "LOW": "low", "INFORMATIONAL": "info"}.get(label, "info")
    if source == "aws.securityhub":
        finding = (detail.get("findings") or [{}])[0]
        label = ((finding.get("Severity") or {}).get("Label") or "").upper()
        return {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium",
                "LOW": "low", "INFORMATIONAL": "info"}.get(label, "info")
    # CloudTrail + Config drift events default to medium; push-rule layer
    # promotes specific high-blast-radius actions to critical.
    return "medium"


# ============================================================================
# Aurora inserts
# ============================================================================

def _insert_event(
    *,
    event_id: str,
    tenant_id: str,
    conn_id: str,
    kind: str,
    source: str,
    severity: str,
    title: str,
    description: str | None,
    resource_arn: str | None,
    actor: str | None,
    raw_s3_key: str,
    normalized: dict,
    fired_at: str,
) -> None:
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO events (event_id, tenant_id, conn_id, kind, source, severity, "
            "                    title, description, resource_arn, actor, raw_s3_key, "
            "                    normalized, fired_at) "
            "VALUES (CAST(:eid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        :kind, :source, :severity, :title, :description, :resource_arn, "
            "        :actor, :raw_s3_key, CAST(:normalized AS JSONB), "
            "        CAST(:fired_at AS TIMESTAMPTZ))"
        ),
        parameters=[
            {"name": "eid",          "value": {"stringValue": event_id}},
            {"name": "tid",          "value": {"stringValue": tenant_id}},
            {"name": "cid",          "value": {"stringValue": conn_id}},
            {"name": "kind",         "value": {"stringValue": kind}},
            {"name": "source",       "value": {"stringValue": source}},
            {"name": "severity",     "value": {"stringValue": severity}},
            {"name": "title",        "value": {"stringValue": title}},
            {"name": "description",  "value": ({"stringValue": description} if description else {"isNull": True})},
            {"name": "resource_arn", "value": ({"stringValue": resource_arn} if resource_arn else {"isNull": True})},
            {"name": "actor",        "value": ({"stringValue": actor} if actor else {"isNull": True})},
            {"name": "raw_s3_key",   "value": {"stringValue": raw_s3_key}},
            {"name": "normalized",   "value": {"stringValue": json.dumps(normalized)}},
            {"name": "fired_at",     "value": {"stringValue": fired_at}},
        ],
    )


def _insert_drift(event_id: str, normalized: dict) -> None:
    # before/after state extraction lives in the source-specific parsers;
    # for now we just record the action.
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO drift_events (event_id, action, before_state, after_state) "
            "VALUES (CAST(:eid AS UUID), :action, NULL, NULL)"
        ),
        parameters=[
            {"name": "eid",    "value": {"stringValue": event_id}},
            {"name": "action", "value": {"stringValue": normalized.get("title", "unknown")}},
        ],
    )
