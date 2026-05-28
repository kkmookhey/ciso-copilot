# platform/lambda/ai_supply_chain_matcher/main.py
"""CVE-vs-AI-inventory matcher.

Triggered by SQS after the AI scanner emits sca_vuln findings (from Trivy).
Joins them with the ai_framework -> ai_agent edge graph + the KEV
threat_indicators table. When both conditions hold (KEV-listed AND actively
imported), emits a new ai_supply_chain_active finding at CRITICAL severity
and fires a push notification.

Schema notes (confirmed against platform/sql/):
  - findings PK: finding_id (UUID)
  - findings natural-key unique index: (tenant_id, conn_id, check_id,
      COALESCE(resource_arn,''), COALESCE(region,''))
  - findings.status CHECK: 'fail' | 'pass' | 'not_assessed' | 'not_applicable'
  - entities PK: id (UUID)
  - edges columns: source_entity_id, target_entity_id (not source_id/target_id)
  - threat_indicators column: indicator_value (not value)
  - No tenant_id on threat_indicators (global IOC table)
"""
from __future__ import annotations
import json
import os
import traceback
import uuid
from typing import Any

import boto3

from _shared import push as push_mod


DB_CLUSTER_ARN        = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN         = os.environ["DB_SECRET_ARN"]
DB_NAME               = os.environ["DB_NAME"]
APNS_PLATFORM_APP_ARN = os.environ.get("APNS_PLATFORM_APP_ARN", "")

_rds = boto3.client("rds-data")

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
# Joins:
#   findings (sca_vuln, from Trivy)
#   → entities (ai_framework, matched on display_name == package name)
#   → edges (kind='imports', target_entity_id = framework.id)
#   → entities (ai_agent, source of the imports edge)
#   → entities (github_repo, via agent.parent_id — may be NULL)
#   → threat_indicators (KEV CVE match)
#
# Dedup guard: skips if an ai_supply_chain_active finding already exists for
# (tenant, package, cve, agent) so re-scans don't double-emit.
_MATCH_SQL = """
SELECT
  trivy.finding_id::text                                AS trivy_finding_id,
  trivy.evidence_packet->>'package'                     AS package,
  trivy.evidence_packet->>'version'                     AS version,
  trivy.evidence_packet->>'cve'                         AS cve,
  framework.id::text                                    AS framework_entity_id,
  agent.id::text                                        AS agent_entity_id,
  agent.display_name                                    AS agent_name,
  repo.display_name                                     AS repo_full_name
FROM findings trivy
JOIN entities framework
  ON framework.tenant_id = trivy.tenant_id
 AND framework.kind = 'ai_framework'
 AND LOWER(framework.display_name) = LOWER(trivy.evidence_packet->>'package')
JOIN edges e
  ON e.tenant_id = trivy.tenant_id
 AND e.target_entity_id = framework.id
 AND e.kind = 'imports'
JOIN entities agent
  ON agent.id = e.source_entity_id
 AND agent.kind = 'ai_agent'
LEFT JOIN entities repo
  ON repo.id = agent.parent_id
 AND repo.kind = 'github_repo'
JOIN threat_indicators kev
  ON kev.kind = 'cve'
 AND kev.source = 'kev'
 AND kev.indicator_value = trivy.evidence_packet->>'cve'
WHERE trivy.tenant_id = CAST(:t AS UUID)
  AND trivy.check_id = 'sca_vuln'
  AND trivy.severity IN ('critical', 'high')
  AND NOT EXISTS (
    SELECT 1 FROM findings prior
    WHERE prior.tenant_id = trivy.tenant_id
      AND prior.check_id = 'ai_supply_chain_active'
      AND prior.evidence_packet->>'package'        = trivy.evidence_packet->>'package'
      AND prior.evidence_packet->>'cve'            = trivy.evidence_packet->>'cve'
      AND prior.evidence_packet->>'agent_entity_id' = agent.id::text
  )
"""

# The `findings` table has a natural-key unique index on
# (tenant_id, conn_id, check_id, COALESCE(resource_arn,''), COALESCE(region,'')).
# For ai_supply_chain_active findings there is no conn_id (no cloud connection
# drives this) — use a sentinel nil UUID. check_id encodes the CVE+agent pair
# so the natural key is unique per match.
_NIL_UUID = "00000000-0000-0000-0000-000000000000"


def handler(event: dict, context: Any) -> None:
    for record in event.get("Records", []):
        try:
            body      = json.loads(record["body"])
            tenant_id = body["tenant_id"]
            scan_id   = body.get("scan_id") or _NIL_UUID
            matches   = _find_matches(tenant_id=tenant_id)
            print(f"[matcher] tenant={tenant_id} scan={scan_id} matches={len(matches)}")
            for m in matches:
                finding_id = _emit_finding(tenant_id=tenant_id, scan_id=scan_id, match=m)
                _fire_push(tenant_id=tenant_id, finding_id=finding_id, match=m)
        except Exception as e:
            print(f"[matcher] error: {type(e).__name__}: {e}")
            traceback.print_exc()
            # Re-raise so SQS retries via visibility timeout (max 3, then DLQ).
            raise


def _find_matches(*, tenant_id: str) -> list[dict[str, Any]]:
    rs = _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=_MATCH_SQL,
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    out = []
    for row in rs.get("records", []):
        out.append({
            "trivy_finding_id":    _str(row, 0),
            "package":             _str(row, 1),
            "version":             _str(row, 2),
            "cve":                 _str(row, 3),
            "framework_entity_id": _str(row, 4),
            "agent_entity_id":     _str(row, 5),
            "agent_name":          _str(row, 6),
            "repo_full_name":      _str(row, 7),
        })
    return out


def _str(row: list, i: int) -> str:
    return row[i].get("stringValue", "") if i < len(row) else ""


def _emit_finding(*, tenant_id: str, scan_id: str, match: dict) -> str:
    finding_id = str(uuid.uuid4())
    # check_id encodes the unique match: one row per (CVE, agent) pair.
    check_id   = f"ai_supply_chain_active:{match['cve']}:{match['agent_entity_id']}"
    title      = (f"{match['package']} {match['version']} ({match['cve']}) "
                  f"actively imported by {match['agent_name']}")
    description = (
        f"KEV-listed vulnerability {match['cve']} in {match['package']} "
        f"{match['version']} is actively imported by the AI agent "
        f"'{match['agent_name']}' in repo {match['repo_full_name'] or 'unknown'}. "
        "This represents a confirmed AI supply-chain risk: the vulnerable package "
        "is in production use by a live agentic workflow."
    )
    evidence = json.dumps({
        "package":             match["package"],
        "version":             match["version"],
        "cve":                 match["cve"],
        "agent_name":          match["agent_name"],
        "agent_entity_id":     match["agent_entity_id"],
        "framework_entity_id": match["framework_entity_id"],
        "repo_full_name":      match["repo_full_name"],
        "kev_listed":          True,
        "actively_imported":   True,
    })
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO findings "
            "  (finding_id, tenant_id, conn_id, scan_id, kind, check_id, "
            "   title, description, severity, status, domain, "
            "   frameworks, evidence_packet, first_seen, last_seen) "
            "VALUES "
            "  (CAST(:fid AS UUID), CAST(:t AS UUID), CAST(:conn AS UUID), "
            "   CAST(:s AS UUID), 'ai_supply_chain_active', :check_id, "
            "   :title, :desc, 'critical', 'fail', 'ai', "
            "   CAST('[]' AS JSONB), CAST(:ep AS JSONB), NOW(), NOW()) "
            "ON CONFLICT (tenant_id, conn_id, check_id, "
            "             COALESCE(resource_arn, ''), COALESCE(region, '')) "
            "DO UPDATE SET "
            "  scan_id=EXCLUDED.scan_id, "
            "  evidence_packet=EXCLUDED.evidence_packet, "
            "  last_seen=NOW(), "
            "  resolved_at=NULL "
            "RETURNING finding_id::text"
        ),
        parameters=[
            {"name": "fid",      "value": {"stringValue": finding_id}},
            {"name": "t",        "value": {"stringValue": tenant_id}},
            {"name": "conn",     "value": {"stringValue": _NIL_UUID}},
            {"name": "s",        "value": {"stringValue": scan_id}},
            {"name": "check_id", "value": {"stringValue": check_id}},
            {"name": "title",    "value": {"stringValue": title[:500]}},
            {"name": "desc",     "value": {"stringValue": description}},
            {"name": "ep",       "value": {"stringValue": evidence}},
        ],
    )
    return finding_id


def _fire_push(*, tenant_id: str, finding_id: str, match: dict) -> None:
    if not APNS_PLATFORM_APP_ARN:
        print("[matcher] APNS_PLATFORM_APP_ARN not set — skipping push")
        return
    tokens = push_mod.tokens_for_tenant(
        tenant_id,
        rds=_rds,
        db_cluster_arn=DB_CLUSTER_ARN,
        db_secret_arn=DB_SECRET_ARN,
        db_name=DB_NAME,
    )
    body = (
        f"AI Supply Chain · Critical — KEV CVE in your live "
        f"{match['agent_name']} ({match['package']})"
    )
    push_mod.send_push_with_payload(
        device_tokens=tokens,
        platform_app_arn=APNS_PLATFORM_APP_ARN,
        body=body,
        payload={
            "finding_id":        finding_id,
            "kind_label":        "AI Supply Chain",
            "speakable_summary": body,
        },
    )
