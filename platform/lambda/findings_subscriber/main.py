"""Autonomous broadcast subscriber.

SQS-fed (batch=1). For each message:
  1. idempotency check (DDB seen-table, 7d TTL)
  2. global kill switch (SSM, 60s cache)
  3. tenant_bot_connectors lookup (silent ack if missing/disabled/no channel)
  4. findings row re-read (silent ack if missing — race with retention)
  5. POST the Block Kit card to Slack via direct Web API (chat.postMessage)
  6. mark_seen (conditional PutItem; log & swallow if fails AFTER successful send)

Implementation note: step 5 uses Slack's direct Web API
(https://slack.com/api/chat.postMessage) with the bot token rather than
mcp_oauth.get_admin_session. Slack's MCP server only accepts user-scope
tokens (xoxp-...) — bot tokens (xoxb-...) get 401. Direct API works for
both token types and is simpler for fire-and-forget posts.
"""
from __future__ import annotations
import json

import requests

from findings_subscriber import idempotency, kill_switch, block_kit
from mcp_oauth.crypto import decrypt_token
from mcp_oauth.session import (
    _db,
    ConnectorMissingError,
    ConnectorRevokedError,
)


def handler(event: dict, _ctx) -> dict:
    for record in event.get("Records", []):
        try:
            _process(json.loads(record["body"]))
        except (ConnectorMissingError, ConnectorRevokedError) as e:
            # Tenant uninstalled or bot revoked — expected silent ack.
            print(f"[findings_subscriber] silent ack ({type(e).__name__}): {e}")
        # All other exceptions propagate → SQS retry → DLQ after maxReceiveCount.
    return {"ok": True}


def _process(body: dict) -> None:
    tenant_id = body["tenant_id"]
    finding_id = body["finding_id"]
    scan_id = body["scan_id"]

    if idempotency.seen(tenant_id=tenant_id,
                        finding_id=finding_id, scan_id=scan_id):
        print(f"[findings_subscriber] already seen: {finding_id}/{scan_id}")
        return
    if not kill_switch.global_enabled():
        print("[findings_subscriber] global kill switch OFF; skipping")
        return

    # tenant_bot_connectors gate (skip silently if not configured).
    bot = _db().execute(
        """
        SELECT bot_id, broadcast_channel_id, autonomous_rule_enabled,
               access_token_enc, access_data_key_ct,
               mcp_server_url, vendor_workspace_id, access_expires_at
        FROM tenant_bot_connectors
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
          AND status = 'active'
        """,
        [{"name": "tid", "value": {"stringValue": tenant_id}}],
    ).fetchone()
    if not bot:
        return
    if not bot.get("autonomous_rule_enabled"):
        return
    if not bot.get("broadcast_channel_id"):
        return

    # Re-read finding (subscriber may lag writer by ms).
    finding = _db().execute(
        """
        SELECT finding_id::text AS finding_id,
               title, description, severity, status,
               resource_arn, resource_type, region, domain,
               frameworks,
               EXTRACT(EPOCH FROM last_seen)::bigint AS created_at_epoch
        FROM findings WHERE finding_id = :fid::uuid
        """,
        [{"name": "fid", "value": {"stringValue": finding_id}}],
    ).fetchone()
    if not finding:
        return

    blocks = block_kit.format_finding_card({
        "finding_id":       finding.get("finding_id") or finding_id,
        "title":            finding.get("title") or "",
        "resource_arn":     finding.get("resource_arn"),
        "scanner":          _scanner_for_domain(finding.get("domain")),
        "frameworks_list":  _frameworks_to_list(finding.get("frameworks")),
        "created_at_epoch": finding.get("created_at_epoch") or 0,
    })

    # Decrypt the bot token and post via Slack Web API directly.
    token_row = _db().execute("""
        SELECT access_token_enc, access_data_key_ct
        FROM tenant_bot_connectors
        WHERE tenant_id = :tid::uuid AND oauth_provider = 'slack'
          AND status = 'active'
        LIMIT 1
    """, [{"name": "tid", "value": {"stringValue": tenant_id}}]).fetchone()
    if not token_row:
        # Race: revoked between the gate query and now. Silent ack.
        return
    bot_token = decrypt_token(
        token_row["access_token_enc"], token_row["access_data_key_ct"],
    )
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Content-Type":  "application/json; charset=utf-8",
        },
        json={
            "channel": bot["broadcast_channel_id"],
            "blocks":  blocks,
        },
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        # Slack returned 200 with ok=false (e.g. channel_not_found,
        # not_in_channel). Re-raise so the message goes to DLQ after retries
        # — admin needs to fix the channel config.
        raise RuntimeError(f"slack chat.postMessage: {body.get('error', 'unknown')}")

    try:
        idempotency.mark_seen(tenant_id=tenant_id,
                              finding_id=finding_id, scan_id=scan_id)
    except Exception as e:
        # mark_seen failed AFTER successful Slack post — don't re-raise
        # (a duplicate seen-row is much cheaper than a double-broadcast).
        print(f"[findings_subscriber] mark_seen failed post-send: {e!r}")


def _scanner_for_domain(domain: str | None) -> str:
    """Map finding.domain to a human-readable scanner name for the card.

    The findings table doesn't have a `scanner` column; domain is the
    closest proxy (ai/cloud/identity/etc.).
    """
    mapping = {
        "ai":       "AI",
        "cloud":    "Cloud",
        "soc":      "SOC",
        "identity": "Identity",
    }
    return mapping.get(domain or "", domain or "unknown")


def _frameworks_to_list(frameworks) -> list[str]:
    """findings.frameworks is JSONB (per CLAUDE.md gotchas, an object not
    an array). Return a list of human-readable framework labels."""
    if not frameworks:
        return []
    if isinstance(frameworks, str):
        try:
            frameworks = json.loads(frameworks)
        except json.JSONDecodeError:
            return []
    if isinstance(frameworks, dict):
        return [k for k, v in frameworks.items() if v]
    if isinstance(frameworks, list):
        return [str(f) for f in frameworks]
    return []
