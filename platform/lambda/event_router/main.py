"""Event router — receives every event landed on the central EventBridge bus.

Pipeline (CISOBrief-v2.md §9):
  1. Resolve tenant_id from event['account'] via cloud_connections.
  2. Normalize severity by source-specific rules.
  3. Archive raw event to S3 under raw/<date>/<source>/<event_id>.json.
  4. Insert into events (+ drift_events for kind='drift').
  5. Evaluate push rules; for any match, fire APNs to the tenant's users.

Slice 1.3 — Bedrock runtime detector:
  Bedrock InvokeModel/Converse/etc. CloudTrail events are intercepted BEFORE
  the standard SOC flow and written to entities + findings (NOT events table).
  A daily EventBridge schedule triggers the high-volume rollup detector.

Status: pipeline shell. Source-specific parsers (§9 details) land in the
follow-up commit; for now the router writes the raw payload to S3 and a
minimal events row so the data path is exercised end-to-end.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from severity_rules import drift_severity
import push
import spend_cap

DB_CLUSTER_ARN    = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN     = os.environ["DB_SECRET_ARN"]
DB_NAME           = os.environ["DB_NAME"]
RAW_EVENTS_BUCKET = os.environ["RAW_EVENTS_BUCKET"]
APNS_PLATFORM_APP_ARN = os.environ.get("APNS_PLATFORM_APPLICATION_ARN", "")
ENRICHMENT_QUEUE_URL  = os.environ.get("ENRICHMENT_QUEUE_URL", "")

rds_data = boto3.client("rds-data")
s3       = boto3.client("s3")
sqs      = boto3.client("sqs")

# ---------------------------------------------------------------------------
# Slice 1.3 — Bedrock runtime constants
# ---------------------------------------------------------------------------

_BEDROCK_EVENT_NAMES = frozenset({
    "InvokeModel",
    "InvokeModelWithResponseStream",
    "Converse",
    "ConverseStream",
    "InvokeAgent",
    "Retrieve",
    "RetrieveAndGenerate",
})

# Sentinel scan_id used when a finding is not produced by a scheduled scan.
# NOTE: findings.scan_id is NOT NULL FK → scans(scan_id). A sentinel row with
# this UUID must exist in the scans table (seeded once at DB bootstrap).
# Follows the same pattern as ai_supply_chain_matcher._NIL_UUID.
# TODO: add the sentinel row seed to the next schema migration.
_NIL_UUID = "00000000-0000-0000-0000-000000000000"

_BEDROCK_HIGH_VOLUME_DEFAULT = 10_000


def handler(event: dict, context) -> dict:
    """EventBridge invokes this with a single event payload."""
    print(json.dumps({"router": "received", "source": event.get("source"), "detail-type": event.get("detail-type")}))

    # Slice 1.3: Daily rollup branch — runs before account resolution (no account field).
    if event.get("detail-type") == "shasta.scheduled.bedrock_daily_rollup":
        return _handle_bedrock_daily_rollup(event)

    # Slice 1.3: Bedrock runtime detection branch — runs before the standard SOC flow.
    # Writes to entities + findings, NOT the events table.
    if _is_bedrock_event(event):
        return _handle_bedrock(event)

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
        before_state, after_state = _extract_states(source, detail_type, event.get("detail", {}))
        severity   = _severity(source, event.get("detail", {}), kind, after_state)

        source_event_id = _source_event_id(event)

        inserted = _insert_event(
            event_id        = event_id,
            tenant_id       = conn["tenant_id"],
            conn_id         = conn["conn_id"],
            kind            = kind,
            source          = source,
            severity        = severity,
            title           = normalized.get("title", detail_type or source),
            description     = normalized.get("description"),
            resource_arn    = normalized.get("resource_arn"),
            actor           = normalized.get("actor"),
            raw_s3_key      = raw_s3_key,
            normalized      = normalized,
            fired_at        = fired_at,
            source_event_id = source_event_id,
            source_ip       = _extract_source_ip(event),
        )

        if not inserted:
            print(f"DROP: duplicate (tenant={conn['tenant_id']}, source={source}, sei={source_event_id})")
            return {"ok": True, "deduped": True}

        # 4. Drift extension if applicable
        if kind == "drift":
            action = event.get("detail", {}).get("eventName") or \
                     (event.get("detail", {}).get("configurationItem") or {}).get("resourceType", "drift")
            _insert_drift(event_id, action, before_state, after_state, normalized.get("resource_arn"))

        # 5. Push-rule evaluation
        try:
            current = spend_cap.push_count_current(conn["tenant_id"])
            if push.should_push(severity, current) and APNS_PLATFORM_APP_ARN:
                tokens = _device_tokens_for_tenant(conn["tenant_id"])
                if tokens:
                    body = push.format_push_body(
                        kind=kind, severity=severity,
                        title=normalized.get("title", ""),
                        resource_arn=normalized.get("resource_arn"),
                        actor=normalized.get("actor"),
                    )
                    push.send_push(device_tokens=tokens,
                                   platform_app_arn=APNS_PLATFORM_APP_ARN,
                                   body=body)
                    spend_cap.push_count_increment(conn["tenant_id"])
                    rds_data.execute_statement(
                        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
                        sql="UPDATE events SET push_sent = true WHERE event_id = CAST(:e AS UUID)",
                        parameters=[{"name": "e", "value": {"stringValue": event_id}}],
                    )
        except Exception as e:
            print(f"WARN: push failed (non-fatal): {e}")

        # 6. Enqueue for async AI enrichment (Slice 1 = drift only)
        if kind == "drift" and ENRICHMENT_QUEUE_URL:
            try:
                sqs.send_message(
                    QueueUrl=ENRICHMENT_QUEUE_URL,
                    MessageBody=json.dumps({"event_id": event_id, "tenant_id": conn["tenant_id"]}),
                )
            except Exception as e:
                print(f"WARN: enrichment enqueue failed (non-fatal, will rely on backfill): {e}")

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


def _device_tokens_for_tenant(tenant_id: str) -> list[str]:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql="SELECT device_token FROM users WHERE tenant_id = CAST(:t AS UUID) AND device_token IS NOT NULL",
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    return [r[0].get("stringValue", "") for r in rs.get("records", []) if r[0].get("stringValue")]


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
    # CloudTrail management API events arrive with source=aws.<service>
    # (e.g. aws.ec2, aws.iam, aws.s3) — not aws.cloudtrail. We key on
    # detail-type which is unique to these events.
    if detail_type == "AWS API Call via CloudTrail":
        return {
            "title":        detail.get("eventName", detail_type),
            "description":  None,
            "resource_arn": _extract_cloudtrail_resource(detail),
            "actor":        ((detail.get("userIdentity") or {}).get("arn")
                             or (detail.get("userIdentity") or {}).get("userName")),
        }
    if detail_type == "Configuration Item Change Notification":
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


def _extract_source_ip(event: dict) -> str | None:
    """Return CloudTrail's caller IP, or None for non-CloudTrail / AWS-internal calls."""
    if event.get("detail-type") != "AWS API Call via CloudTrail":
        return None
    raw = (event.get("detail") or {}).get("sourceIPAddress")
    if not raw or not isinstance(raw, str):
        return None
    # AWS uses service-principal strings for internal calls; only keep dotted-quad / IPv6.
    if "." not in raw and ":" not in raw:
        return None
    if raw.endswith(".amazonaws.com") or raw.endswith(".amazon.com"):
        return None
    return raw


def _classify_kind(source: str, detail_type: str, detail: dict) -> str:
    """alert: native security detector. drift: configuration change."""
    if detail_type in ("AWS API Call via CloudTrail", "Configuration Item Change Notification"):
        return "drift"
    return "alert"


def _extract_states(source: str, detail_type: str, detail: dict) -> tuple[dict | None, dict | None]:
    """Return (before_state, after_state) JSON-like dicts. (None, None) for non-drift sources."""
    if detail_type == "Configuration Item Change Notification":
        ci   = detail.get("configurationItem", {}) or {}
        diff = detail.get("configurationItemDiff", {}) or {}
        after  = ci.get("configuration") or {}
        before: dict = {}
        for path, change in (diff.get("changedProperties") or {}).items():
            if "previousValue" in change:
                before[path] = change["previousValue"]
        return (before or None), (after or None)

    if detail_type == "AWS API Call via CloudTrail":
        return None, (detail.get("requestParameters") or None)

    return None, None


def _source_event_id(event: dict) -> str | None:
    """Return a stable per-source idempotency key, or None for unknown sources."""
    source      = event.get("source", "")
    detail_type = event.get("detail-type", "")
    detail      = event.get("detail", {}) or {}

    if detail_type == "AWS API Call via CloudTrail":
        return detail.get("eventID")
    if detail_type == "Configuration Item Change Notification":
        ci = detail.get("configurationItem", {}) or {}
        capture = ci.get("configurationItemCaptureTime")
        rid     = ci.get("resourceId")
        return f"{capture}:{rid}" if capture and rid else None
    if source == "aws.guardduty":
        return detail.get("id")
    if source == "aws.inspector2":
        return (detail.get("findingArn") or "").split("/")[-1] or None
    if source == "aws.securityhub":
        first = (detail.get("findings") or [{}])[0]
        return first.get("Id")
    return None


def _severity(source: str, detail: dict, kind: str, after_state: dict | None) -> str:
    """Normalize source-specific severities to {critical, high, medium, low, info}."""
    if source == "aws.guardduty":
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

    # CloudTrail + Config drift use the rule table over after_state
    if kind == "drift":
        action = detail.get("eventName") or (detail.get("configurationItem") or {}).get("resourceType", "")
        return drift_severity(action=action, after=(after_state or {}))

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
    source_event_id: str | None,
    source_ip: str | None,
) -> bool:
    """INSERT into events with ON CONFLICT DO NOTHING. Returns True if inserted, False if dup."""
    result = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO events (event_id, tenant_id, conn_id, kind, source, severity, "
            "                    title, description, resource_arn, actor, raw_s3_key, "
            "                    normalized, fired_at, source_event_id, source_ip) "
            "VALUES (CAST(:eid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        :kind, :source, :severity, :title, :description, :resource_arn, "
            "        :actor, :raw_s3_key, CAST(:normalized AS JSONB), "
            "        CAST(:fired_at AS TIMESTAMPTZ), :sei, :source_ip) "
            "ON CONFLICT (tenant_id, source, source_event_id) WHERE source_event_id IS NOT NULL DO NOTHING "
            "RETURNING event_id"
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
            {"name": "sei",          "value": ({"stringValue": source_event_id} if source_event_id else {"isNull": True})},
            {"name": "source_ip",    "value": ({"stringValue": source_ip} if source_ip else {"isNull": True})},
        ],
    )
    return len(result.get("records", [])) > 0


def _insert_drift(event_id: str, action: str, before_state: dict | None, after_state: dict | None,
                  target_resource_arn: str | None) -> None:
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO drift_events (event_id, action, before_state, after_state, target_resource_arn) "
            "VALUES (CAST(:eid AS UUID), :action, "
            "        CAST(:before AS JSONB), CAST(:after AS JSONB), :tgt) "
            "ON CONFLICT (event_id) DO NOTHING"
        ),
        parameters=[
            {"name": "eid",    "value": {"stringValue": event_id}},
            {"name": "action", "value": {"stringValue": action}},
            {"name": "before", "value": ({"stringValue": json.dumps(before_state)} if before_state else {"isNull": True})},
            {"name": "after",  "value": ({"stringValue": json.dumps(after_state)}  if after_state  else {"isNull": True})},
            {"name": "tgt",    "value": ({"stringValue": target_resource_arn}      if target_resource_arn else {"isNull": True})},
        ],
    )


# ============================================================================
# Slice 1.3 — Bedrock runtime detector
# ============================================================================

def _is_bedrock_event(event: dict) -> bool:
    """True iff this is a CloudTrail event for a Bedrock runtime API call."""
    return (
        event.get("detail-type") == "AWS API Call via CloudTrail"
        and (event.get("detail") or {}).get("eventName") in _BEDROCK_EVENT_NAMES
    )


def _handle_bedrock(event: dict) -> dict:
    """Per-event Bedrock handler.  Writes to entities + findings; NOT the events table."""
    detail     = event.get("detail") or {}
    account_id = detail.get("recipientAccountId") or event.get("account")

    conn = _find_connection_by_account(account_id)
    if not conn:
        print(f"[bedrock] DROP: no connection for account {account_id}")
        return {"ok": False, "reason": "no_tenant_for_account", "account_id": account_id}

    tenant_id = conn["tenant_id"]
    conn_id   = conn["conn_id"]

    model_id   = (detail.get("requestParameters") or {}).get("modelId") or "unknown"
    principal  = (detail.get("userIdentity") or {}).get("arn") or "unknown"
    region     = detail.get("awsRegion") or "unknown"
    event_day  = (detail.get("eventTime") or "")[:10]  # YYYY-MM-DD

    # 1) Upsert bedrock_model entity (per tenant + model + region)
    model_nk        = f"bedrock_model::{region}::{model_id}"
    model_entity_id = _upsert_bedrock_entity(
        tenant_id=tenant_id,
        kind="bedrock_model",
        natural_key=model_nk,
        display_name=model_id,
        attributes={"region": region, "model_id": model_id},
    )

    # 2) Upsert bedrock_invocation rollup entity (per tenant + principal + model + day + region)
    inv_nk = f"bedrock_invocation::{principal}::{model_id}::{event_day}::{region}"
    _upsert_invocation_rollup(
        tenant_id=tenant_id,
        natural_key=inv_nk,
        principal=principal,
        model_id=model_id,
        day=event_day,
        region=region,
    )

    # 3) Upsert iam_principal entity
    principal_nk        = f"iam_principal::{principal}"
    principal_entity_id = _upsert_bedrock_entity(
        tenant_id=tenant_id,
        kind="iam_principal",
        natural_key=principal_nk,
        display_name=principal,
        attributes={"arn": principal},
    )

    # 4) Upsert edge: iam_principal --uses-> bedrock_model
    _upsert_bedrock_edge(
        tenant_id=tenant_id,
        source_entity_id=principal_entity_id,
        target_entity_id=model_entity_id,
        kind="uses",
    )

    # 5) Detectors
    allowed = _bedrock_allowed_principals(conn_id)

    # Detector A: unsanctioned principal (only when allowed-list IS configured)
    if allowed is not None and principal not in allowed:
        _emit_bedrock_finding(
            tenant_id=tenant_id,
            conn_id=conn_id,
            check_id="aws_bedrock_invoke_unsanctioned",
            title=f"Unsanctioned principal invoking Bedrock: {principal}",
            description=(
                f"Principal {principal} invoked Bedrock model {model_id} in {region} "
                "but is not in the tenant's bedrock_allowed_principals list."
            ),
            severity="medium",
            status="fail",
            region=region,
            resource_arn=model_id,
            entity_id=model_entity_id,
            evidence_packet={
                "principal": principal,
                "model_id":  model_id,
                "region":    region,
                "allowed_count": len(allowed),
            },
            frameworks={
                "nist_ai_rmf":    ["GOVERN 1.1", "MANAGE 1.3"],
                "owasp_llm_top10": ["LLM08:2025"],
            },
        )

    # Detector B: model inventory — always emit (ON CONFLICT DO NOTHING makes it idempotent)
    _emit_bedrock_finding(
        tenant_id=tenant_id,
        conn_id=conn_id,
        check_id="aws_bedrock_model_inventory",
        title=f"Bedrock model first seen: {model_id} in {region}",
        description=f"Model {model_id} was invoked in region {region}.",
        severity="informational",
        status="pass",
        region=region,
        resource_arn=model_id,
        entity_id=model_entity_id,
        evidence_packet={"model_id": model_id, "region": region},
        frameworks={"nist_ai_rmf": ["MAP 1.1"]},
    )

    # Detector C: cross-region — deferred to Slice 2.
    # In Bedrock, the event's awsRegion IS the model's region (the call is always
    # regional). A meaningful cross-region signal requires comparing the caller's
    # home region (from IAM principal metadata) vs the invocation region, which
    # needs an additional lookup not available from the raw event alone.
    # Tracking: docs/superpowers/plans/2026-06-05-ai-security-slice-1.md TODO.

    return {"ok": True, "model": model_id, "principal": principal, "tenant": tenant_id}


def _handle_bedrock_daily_rollup(event: dict) -> dict:
    """Emit aws_bedrock_invoke_high_volume for any (principal, model, day) above threshold."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT tenant_id::text, "
            "       attributes->>'principal', "
            "       attributes->>'model_id', "
            "       (attributes->>'invocation_count')::int, "
            "       attributes->>'region' "
            "FROM entities "
            "WHERE kind = 'bedrock_invocation' "
            "  AND attributes->>'day' = :day"
        ),
        parameters=[{"name": "day", "value": {"stringValue": yesterday}}],
    )
    emitted = 0
    for r in rs.get("records", []):
        tenant_id  = r[0].get("stringValue")
        principal  = r[1].get("stringValue")
        model_id   = r[2].get("stringValue")
        count      = r[3].get("longValue") or 0
        region     = r[4].get("stringValue") if len(r) > 4 else "unknown"

        threshold = _bedrock_high_volume_threshold(tenant_id)
        if count > threshold:
            conn_id = _conn_id_for_tenant(tenant_id)
            if not conn_id:
                print(f"[bedrock-rollup] no conn for tenant {tenant_id}, skipping")
                continue
            _emit_bedrock_finding(
                tenant_id=tenant_id,
                conn_id=conn_id,
                check_id="aws_bedrock_invoke_high_volume",
                title=f"High Bedrock invocation volume: {principal} → {model_id}",
                description=(
                    f"Principal {principal} made {count:,} Bedrock calls to {model_id} "
                    f"on {yesterday} (threshold: {threshold:,})."
                ),
                severity="medium",
                status="fail",
                region=region,
                resource_arn=model_id,
                entity_id=None,
                evidence_packet={
                    "principal":   principal,
                    "model_id":    model_id,
                    "invocations": count,
                    "threshold":   threshold,
                    "day":         yesterday,
                },
                frameworks={
                    "nist_ai_rmf":    ["MEASURE 2.3", "MANAGE 2.2"],
                    "owasp_llm_top10": ["LLM10:2025"],
                },
            )
            emitted += 1
    return {"status": "ok", "emitted": emitted, "day": yesterday}


# ============================================================================
# Slice 1.3 — Bedrock entity / edge / finding helpers
# ============================================================================

def _upsert_bedrock_entity(
    *,
    tenant_id: str,
    kind: str,
    natural_key: str,
    display_name: str,
    attributes: dict,
) -> str:
    """Upsert an entity in the entities table.  Returns the persisted id.

    Mirrors entities_api._upsert_repo_entity: ON CONFLICT (tenant_id, kind,
    natural_key) updates last_seen_at + attributes. PK is `id` (UUID).
    """
    new_id   = str(uuid.uuid4())
    # domain: bedrock entities live in 'cloud'; iam_principal lives in 'identity'
    if kind == "iam_principal":
        domain = "identity"
    elif kind.startswith("bedrock_"):
        domain = "cloud"
    else:
        domain = "cloud"

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO entities "
            "  (id, tenant_id, kind, natural_key, display_name, domain, "
            "   attributes, evidence_packet, detector_id, detector_version) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), :kind, "
            "        :nk, :name, :domain, "
            "        CAST(:attrs AS JSONB), NULL, 'event-router-bedrock', '1.0.0') "
            "ON CONFLICT (tenant_id, kind, natural_key) "
            "  DO UPDATE SET last_seen_at=NOW(), "
            "                attributes=EXCLUDED.attributes "
            "RETURNING id::text"
        ),
        parameters=[
            {"name": "id",     "value": {"stringValue": new_id}},
            {"name": "tid",    "value": {"stringValue": tenant_id}},
            {"name": "kind",   "value": {"stringValue": kind}},
            {"name": "nk",     "value": {"stringValue": natural_key}},
            {"name": "name",   "value": {"stringValue": display_name}},
            {"name": "domain", "value": {"stringValue": domain}},
            {"name": "attrs",  "value": {"stringValue": json.dumps(attributes)}},
        ],
    )
    rows = rs.get("records", [])
    if rows and rows[0] and "stringValue" in rows[0][0]:
        return rows[0][0]["stringValue"]
    return new_id


def _upsert_invocation_rollup(
    *,
    tenant_id: str,
    natural_key: str,
    principal: str,
    model_id: str,
    day: str,
    region: str,
) -> str:
    """Upsert a bedrock_invocation rollup entity and increment invocation_count."""
    new_id = str(uuid.uuid4())
    attrs  = {
        "principal":        principal,
        "model_id":         model_id,
        "day":              day,
        "region":           region,
        "invocation_count": 1,
        "last_seen":        datetime.now(timezone.utc).isoformat(),
    }
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO entities "
            "  (id, tenant_id, kind, natural_key, display_name, domain, "
            "   attributes, evidence_packet, detector_id, detector_version) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), 'bedrock_invocation', "
            "        :nk, :name, 'cloud', "
            "        CAST(:attrs AS JSONB), NULL, 'event-router-bedrock', '1.0.0') "
            "ON CONFLICT (tenant_id, kind, natural_key) "
            "  DO UPDATE SET "
            "    last_seen_at=NOW(), "
            "    attributes=jsonb_set( "
            "      jsonb_set(entities.attributes, '{invocation_count}', "
            "        to_jsonb(COALESCE((entities.attributes->>'invocation_count')::int, 0) + 1)), "
            "      '{last_seen}', to_jsonb(NOW()::text)) "
            "RETURNING id::text"
        ),
        parameters=[
            {"name": "id",    "value": {"stringValue": new_id}},
            {"name": "tid",   "value": {"stringValue": tenant_id}},
            {"name": "nk",    "value": {"stringValue": natural_key}},
            {"name": "name",  "value": {"stringValue": f"{principal}→{model_id} ({day})"}},
            {"name": "attrs", "value": {"stringValue": json.dumps(attrs)}},
        ],
    )
    rows = rs.get("records", [])
    if rows and rows[0] and "stringValue" in rows[0][0]:
        return rows[0][0]["stringValue"]
    return new_id


def _upsert_bedrock_edge(
    *,
    tenant_id: str,
    source_entity_id: str,
    target_entity_id: str,
    kind: str,
) -> None:
    """Upsert an edge between two entities.  Uses source_entity_id / target_entity_id
    (NOT source_id / target_id — Aurora schema gotcha)."""
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO edges "
            "  (id, tenant_id, source_entity_id, target_entity_id, kind, "
            "   attributes, evidence_packet, detector_id, detector_version) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), "
            "        CAST(:src AS UUID), CAST(:tgt AS UUID), :kind, "
            "        '{}'::jsonb, '{\"detector\": \"event-router-bedrock\"}'::jsonb, "
            "        'event-router-bedrock', '1.0.0') "
            "ON CONFLICT (source_entity_id, target_entity_id, kind) "
            "  DO UPDATE SET last_seen_at=NOW()"
        ),
        parameters=[
            {"name": "id",   "value": {"stringValue": str(uuid.uuid4())}},
            {"name": "tid",  "value": {"stringValue": tenant_id}},
            {"name": "src",  "value": {"stringValue": source_entity_id}},
            {"name": "tgt",  "value": {"stringValue": target_entity_id}},
            {"name": "kind", "value": {"stringValue": kind}},
        ],
    )


def _emit_bedrock_finding(
    *,
    tenant_id: str,
    conn_id: str,
    check_id: str,
    title: str,
    description: str,
    severity: str,
    status: str,
    region: str | None,
    resource_arn: str | None,
    entity_id: str | None,
    evidence_packet: dict,
    frameworks: dict,
) -> str:
    """Upsert a finding.  Uses ON CONFLICT on the natural key so re-fire is idempotent.

    scan_id uses _NIL_UUID (sentinel) because these findings are not produced by
    a scheduled scan. The sentinel row must exist in the scans table.
    findings.frameworks must be a JSON object {}, never an array (Aurora schema gotcha).
    findings.status enum: fail / pass / partial / not_assessed / not_applicable (no 'open').
    """
    assert isinstance(frameworks, dict), "frameworks must be a dict, not a list"
    finding_id = str(uuid.uuid4())
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO findings "
            "  (finding_id, tenant_id, conn_id, scan_id, check_id, "
            "   title, description, severity, status, region, domain, "
            "   frameworks, evidence_packet, resource_arn, subject_entity_id, "
            "   first_seen, last_seen) "
            "VALUES (CAST(:fid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        CAST(:sid AS UUID), :check_id, "
            "        :title, :desc, :sev, :status, :region, 'cloud', "
            "        CAST(:fw AS JSONB), CAST(:ep AS JSONB), :resource_arn, "
            "        CAST(:eid AS UUID), NOW(), NOW()) "
            "ON CONFLICT (tenant_id, conn_id, check_id, "
            "             COALESCE(resource_arn, ''), COALESCE(region, '')) "
            "  DO UPDATE SET "
            "    last_seen=NOW(), "
            "    evidence_packet=EXCLUDED.evidence_packet, "
            "    resolved_at=NULL "
            "RETURNING finding_id::text"
        ),
        parameters=[
            {"name": "fid",          "value": {"stringValue": finding_id}},
            {"name": "tid",          "value": {"stringValue": tenant_id}},
            {"name": "cid",          "value": {"stringValue": conn_id}},
            {"name": "sid",          "value": {"stringValue": _NIL_UUID}},
            {"name": "check_id",     "value": {"stringValue": check_id}},
            {"name": "title",        "value": {"stringValue": title[:500]}},
            {"name": "desc",         "value": {"stringValue": description}},
            {"name": "sev",          "value": {"stringValue": severity}},
            {"name": "status",       "value": {"stringValue": status}},
            {"name": "region",       "value": ({"stringValue": region} if region else {"isNull": True})},
            {"name": "fw",           "value": {"stringValue": json.dumps(frameworks)}},
            {"name": "ep",           "value": {"stringValue": json.dumps(evidence_packet)}},
            {"name": "resource_arn", "value": ({"stringValue": resource_arn} if resource_arn else {"isNull": True})},
            {"name": "eid",          "value": ({"stringValue": entity_id} if entity_id else {"isNull": True})},
        ],
    )
    records = (rs.get("records") or [])
    if records and records[0]:
        return records[0][0].get("stringValue") or finding_id
    return finding_id


def _bedrock_allowed_principals(conn_id: str) -> list[str] | None:
    """Return the tenant's bedrock_allowed_principals list from cloud_connections.evidence_packet.

    Returns None if the key is not set (meaning 'no restriction configured, never emit
    the unsanctioned finding').  Returns a (possibly empty) list if the key IS set.
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT evidence_packet::text "
            "FROM cloud_connections "
            "WHERE conn_id = CAST(:cid AS UUID) "
            "LIMIT 1"
        ),
        parameters=[{"name": "cid", "value": {"stringValue": conn_id}}],
    )
    rows = rs.get("records", [])
    if not rows or not rows[0]:
        return None
    raw = rows[0][0].get("stringValue") or "{}"
    try:
        ep = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    allowed = ep.get("bedrock_allowed_principals")
    if allowed is None:
        return None
    return list(allowed) if isinstance(allowed, (list, tuple)) else []


def _bedrock_high_volume_threshold(tenant_id: str) -> int:
    """Return per-tenant threshold from cloud_connections.evidence_packet, or default."""
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT evidence_packet->>'bedrock_high_volume_threshold' "
            "FROM cloud_connections "
            "WHERE tenant_id = CAST(:tid AS UUID) "
            "  AND cloud_type = 'aws' AND status = 'active' "
            "LIMIT 1"
        ),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    rows = rs.get("records", [])
    if rows and rows[0]:
        raw = rows[0][0].get("stringValue")
        if raw:
            try:
                return int(raw)
            except (ValueError, TypeError):
                pass
    return _BEDROCK_HIGH_VOLUME_DEFAULT


def _conn_id_for_tenant(tenant_id: str) -> str | None:
    """Return the first active AWS conn_id for a tenant (for daily rollup findings)."""
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "SELECT conn_id::text "
            "FROM cloud_connections "
            "WHERE tenant_id = CAST(:tid AS UUID) "
            "  AND cloud_type = 'aws' AND status = 'active' "
            "LIMIT 1"
        ),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    rows = rs.get("records", [])
    if rows and rows[0]:
        return rows[0][0].get("stringValue")
    return None
