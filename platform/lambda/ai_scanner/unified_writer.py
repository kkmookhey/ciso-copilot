# platform/lambda/ai_scanner/unified_writer.py
"""Transactional writes to entities / edges / findings / ai_scans.

Public surface:
  commit_scan(ctx, entities=, edges=, findings=) -> None
  mark_scan_failed(ctx, error_message) -> None

Key semantics:
  - Entity UPSERT uses ON CONFLICT (tenant_id, kind, natural_key) DO UPDATE
    SET last_seen_at=NOW(), ... RETURNING id::text. The returned id is the
    PERSISTED id (existing row on conflict, new row on insert). Always use
    the returned id; never trust the client-side UUID after a possible
    conflict. (Spec §9.3 regression #2.)
  - Edges resolve target/source by (kind, natural_key). If an edge points
    at an entity NOT emitted in this scan AND not yet in the table, a stub
    entity is created with attributes={'_stub': true}. (Spec §9.2.)
  - Findings link to entities via subject_entity_id when the detector
    provides (subject_entity_kind, subject_entity_natural_key). NULL FK
    is allowed for legacy / unresolvable findings.
"""
from __future__ import annotations

import json
import os
import uuid

import boto3

from detectors.base import EntityEmission, EdgeEmission, FindingEmission

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

_rds = boto3.client("rds-data")


def commit_scan(ctx, *,
                entities: list[EntityEmission],
                edges:    list[EdgeEmission],
                findings: list[FindingEmission]) -> None:
    tx = _rds.begin_transaction(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
    )["transactionId"]
    try:
        id_by_key: dict[tuple[str, str], str] = {}
        for e in entities:
            persisted_id = _upsert_entity(tx, e, ctx.tenant_id, scan_id=ctx.scan_id, stub=False)
            id_by_key[(e.kind, e.natural_key)] = persisted_id

        for edge in edges:
            src_id = _resolve_or_stub(tx, ctx.tenant_id, edge.source_kind,
                                       edge.source_natural_key, ctx.scan_id, id_by_key)
            tgt_id = _resolve_or_stub(tx, ctx.tenant_id, edge.target_kind,
                                       edge.target_natural_key, ctx.scan_id, id_by_key)
            _upsert_edge(tx, edge, src_id, tgt_id, scan_id=ctx.scan_id)

        for f in findings:
            entity_id = None
            if f.subject_entity_kind and f.subject_entity_natural_key:
                entity_id = _resolve_or_stub(tx, ctx.tenant_id, f.subject_entity_kind,
                                              f.subject_entity_natural_key, ctx.scan_id, id_by_key)
            _insert_finding(tx, f, entity_id, scan_id=ctx.scan_id, ctx=ctx)

        _update_scan(tx, ctx, len(entities), len(edges), len(findings), status="success")

        _rds.commit_transaction(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, transactionId=tx,
        )
    except Exception:
        _rds.rollback_transaction(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, transactionId=tx,
        )
        raise


def mark_scan_failed(ctx, error_message: str) -> None:
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE ai_scans SET status='failed', completed_at=NOW(), "
            "error_message=:msg WHERE id=CAST(:id AS UUID)"
        ),
        parameters=[
            {"name": "id",  "value": {"stringValue": ctx.scan_id}},
            {"name": "msg", "value": {"stringValue": error_message[:1000]}},
        ],
    )


# ---- internal write helpers ------------------------------------------------

def _upsert_entity(tx: str, e: EntityEmission | None,
                    tenant_id: str | None = None, *,
                    scan_id: str | None = None, stub: bool = False,
                    kind: str | None = None, natural_key: str | None = None,
                    display_name: str | None = None, domain: str | None = None) -> str:
    """Upsert and return the PERSISTED id. Either pass a full EntityEmission,
    OR pass stub=True with (tenant_id, kind, natural_key, display_name, domain)
    to create a placeholder stub for cross-domain edges."""
    if e is not None:
        params = {
            "id":     str(uuid.uuid4()),
            "tid":    e.tenant_id,
            "kind":   e.kind,
            "nk":     e.natural_key,
            "name":   e.display_name,
            "dom":    e.domain,
            "attrs":  json.dumps({**e.attributes, "_stub": False} if stub else e.attributes),
            "ev":     json.dumps(e.evidence_packet) if e.evidence_packet else None,
            "did":    e.detector_id,
            "dver":   e.detector_version,
            "sid":    scan_id,
        }
    else:
        params = {
            "id":     str(uuid.uuid4()),
            "tid":    tenant_id,
            "kind":   kind,
            "nk":     natural_key,
            "name":   display_name,
            "dom":    domain,
            "attrs":  json.dumps({"_stub": True}),
            "ev":     None,
            "did":    "manual.stub",
            "dver":   "0.1.0",
            "sid":    scan_id,
        }
    result = _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO entities "
            "  (id, tenant_id, kind, natural_key, display_name, domain, "
            "   attributes, evidence_packet, detector_id, detector_version, scan_id) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), :kind, :nk, :name, :dom, "
            "        CAST(:attrs AS JSONB), "
            "        CAST(:ev AS JSONB), "
            "        :did, :dver, "
            "        CAST(:sid AS UUID)) "
            "ON CONFLICT (tenant_id, kind, natural_key) "
            "  DO UPDATE SET last_seen_at=NOW(), "
            "                attributes=COALESCE(EXCLUDED.attributes - '_stub', entities.attributes), "
            "                evidence_packet=COALESCE(EXCLUDED.evidence_packet, entities.evidence_packet), "
            "                display_name=EXCLUDED.display_name "
            "RETURNING id::text"
        ),
        parameters=[
            {"name": "id",    "value": {"stringValue": params["id"]}},
            {"name": "tid",   "value": {"stringValue": params["tid"]}},
            {"name": "kind",  "value": {"stringValue": params["kind"]}},
            {"name": "nk",    "value": {"stringValue": params["nk"]}},
            {"name": "name",  "value": {"stringValue": params["name"]}},
            {"name": "dom",   "value": {"stringValue": params["dom"]}},
            {"name": "attrs", "value": {"stringValue": params["attrs"]}},
            {"name": "ev",
             "value": {"isNull": True} if params["ev"] is None
                      else {"stringValue": params["ev"]}},
            {"name": "did",   "value": {"stringValue": params["did"]}},
            {"name": "dver",  "value": {"stringValue": params["dver"]}},
            {"name": "sid",
             "value": {"isNull": True} if params["sid"] is None
                      else {"stringValue": params["sid"]}},
            {"name": "stub",  "value": {"booleanValue": bool(stub)}},
        ],
    )
    rows = result.get("records", [])
    if rows and rows[0] and "stringValue" in rows[0][0]:
        return rows[0][0]["stringValue"]
    return params["id"]


def _resolve_or_stub(tx, tenant_id, kind, natural_key, scan_id,
                     id_by_key: dict[tuple[str, str], str]) -> str:
    """Look up the entity id for (kind, natural_key). First check the
    in-scan map; then upsert a stub if absent. Returns the entity id."""
    if (kind, natural_key) in id_by_key:
        return id_by_key[(kind, natural_key)]
    persisted_id = _upsert_entity(
        tx, e=None, tenant_id=tenant_id, scan_id=scan_id, stub=True,
        kind=kind, natural_key=natural_key,
        display_name=natural_key, domain=_domain_for(kind),
    )
    id_by_key[(kind, natural_key)] = persisted_id
    return persisted_id


def _domain_for(kind: str) -> str:
    if kind.startswith("ai_"):     return "ai"
    if kind.startswith("aws_"):    return "cloud"
    if kind.startswith("azure_"):  return "cloud"
    if kind.startswith("gcp_"):    return "cloud"
    if kind.startswith("entra_"):  return "identity"
    if kind.startswith("github_"): return "repo"
    return "asm"


def _upsert_edge(tx: str, e: EdgeEmission, source_id: str, target_id: str,
                 scan_id: str | None) -> None:
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO edges "
            "  (id, tenant_id, source_entity_id, target_entity_id, kind, "
            "   attributes, evidence_packet, detector_id, detector_version, scan_id) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), CAST(:src AS UUID), "
            "        CAST(:tgt AS UUID), :kind, CAST(:attrs AS JSONB), CAST(:ev AS JSONB), "
            "        :did, :dver, "
            "        CAST(:sid AS UUID)) "
            "ON CONFLICT (source_entity_id, target_entity_id, kind) "
            "  DO UPDATE SET last_seen_at=NOW(), evidence_packet=EXCLUDED.evidence_packet, "
            "                attributes=EXCLUDED.attributes"
        ),
        parameters=[
            {"name": "id",    "value": {"stringValue": str(uuid.uuid4())}},
            {"name": "tid",   "value": {"stringValue": e.tenant_id}},
            {"name": "src",   "value": {"stringValue": source_id}},
            {"name": "tgt",   "value": {"stringValue": target_id}},
            {"name": "kind",  "value": {"stringValue": e.kind}},
            {"name": "attrs", "value": {"stringValue": json.dumps(e.attributes)}},
            {"name": "ev",    "value": {"stringValue": json.dumps(e.evidence_packet)}},
            {"name": "did",   "value": {"stringValue": e.detector_id}},
            {"name": "dver",  "value": {"stringValue": e.detector_version}},
            {"name": "sid",
             "value": {"isNull": True} if scan_id is None
                      else {"stringValue": scan_id}},
        ],
    )


def _insert_finding(tx, f: FindingEmission, entity_id: str | None,
                     scan_id: str, ctx) -> None:
    """Upsert a finding on its natural key (tenant, conn, check_id, resource,
    region) so re-scans refresh in place rather than accumulate a fresh row.
    `first_seen` is preserved on conflict; `last_seen` + mutable state are
    refreshed and `resolved_at` is cleared (the finding was seen again)."""
    fid = str(uuid.uuid4())
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO findings "
            "  (finding_id, tenant_id, conn_id, scan_id, check_id, title, description, "
            "   severity, status, resource_arn, resource_type, region, domain, frameworks, "
            "   remediation, first_seen, last_seen, evidence_packet, subject_entity_id) "
            "VALUES (CAST(:fid AS UUID), CAST(:tid AS UUID), CAST(:conn AS UUID), "
            "        CAST(:sid AS UUID), :ftype, :title, :desc, :sev, :status, :subj, "
            "        :stype, :region, :domain, CAST(:fw AS JSONB), NULL, NOW(), NOW(), "
            "        CAST(:ev AS JSONB), CAST(:eid AS UUID)) "
            "ON CONFLICT (tenant_id, conn_id, check_id, "
            "             COALESCE(resource_arn, ''), COALESCE(region, '')) "
            "  DO UPDATE SET scan_id=EXCLUDED.scan_id, title=EXCLUDED.title, "
            "                description=EXCLUDED.description, severity=EXCLUDED.severity, "
            "                status=EXCLUDED.status, resource_type=EXCLUDED.resource_type, "
            "                domain=EXCLUDED.domain, frameworks=EXCLUDED.frameworks, "
            "                evidence_packet=EXCLUDED.evidence_packet, "
            "                subject_entity_id=EXCLUDED.subject_entity_id, "
            "                last_seen=NOW(), resolved_at=NULL"
        ),
        parameters=[
            {"name": "fid",    "value": {"stringValue": fid}},
            {"name": "tid",    "value": {"stringValue": f.tenant_id}},
            {"name": "conn",   "value": {"stringValue": ctx.connection_id}},
            {"name": "sid",    "value": {"stringValue": scan_id}},
            {"name": "ftype",  "value": {"stringValue": f.finding_type}},
            {"name": "title",  "value": {"stringValue": f.title}},
            {"name": "desc",   "value": {"stringValue": f.description}},
            {"name": "sev",    "value": {"stringValue": f.severity}},
            {"name": "status", "value": {"stringValue": f.status}},
            {"name": "subj",   "value": {"stringValue": f.subject_ref or ""}},
            {"name": "stype",  "value": {"stringValue": f.subject_type or "ai_module"}},
            {"name": "region",
             "value": {"isNull": True} if f.region is None
                      else {"stringValue": f.region}},
            {"name": "domain", "value": {"stringValue": f.domain}},
            {"name": "ev",     "value": {"stringValue": json.dumps(f.evidence_packet)}},
            {"name": "fw",     "value": {"stringValue": json.dumps(f.frameworks)}},
            {"name": "eid",
             "value": {"isNull": True} if entity_id is None
                      else {"stringValue": entity_id}},
        ],
    )


def _update_scan(tx, ctx, entity_count, edge_count, finding_count, status):
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "UPDATE ai_scans SET status=:st, completed_at=NOW(), "
            "  assets_discovered_count=:ac, relationships_discovered_count=:rc, "
            "  findings_generated_count=:fc, scanner_version=:sv "
            "WHERE id = CAST(:sid AS UUID)"
        ),
        parameters=[
            {"name": "st",  "value": {"stringValue": status}},
            {"name": "ac",  "value": {"longValue":   entity_count}},
            {"name": "rc",  "value": {"longValue":   edge_count}},
            {"name": "fc",  "value": {"longValue":   finding_count}},
            {"name": "sv",  "value": {"stringValue": ctx.scanner_version}},
            {"name": "sid", "value": {"stringValue": ctx.scan_id}},
        ],
    )
