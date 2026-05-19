# platform/lambda/ai_scanner/writer.py
"""Transactional writes to ai_assets / ai_relationships / findings / ai_scans."""
from __future__ import annotations

import json
import os
import uuid

import boto3

from detectors.base import AssetEmission, RelEmission, FindingEmission

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

_rds = boto3.client("rds-data")


def commit_scan(ctx, assets: list[AssetEmission],
                relationships: list[RelEmission],
                findings:      list[FindingEmission]) -> None:
    """Run all writes inside one transaction. Raises on failure (callers handle)."""
    tx = _rds.begin_transaction(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
    )["transactionId"]
    try:
        # Map asset emission → assigned UUID for later relationship resolution.
        # The repository node is created by the API before the scan starts (it
        # lives in Aurora but is never emitted by a detector), so pre-seed the
        # map with its ref. Detectors emit edges as
        # `repository::::<repo_uuid>` → `<asset_type>::<repo_uuid>::<path>::<name>`
        # and without this seed every repo-rooted edge would be silently
        # dropped by the "skip if not in map" guard below.
        asset_id_by_ref: dict[str, str] = {
            f"repository::::{ctx.repo_asset_id}": ctx.repo_asset_id,
        }
        for a in assets:
            assigned_id = a.id or str(uuid.uuid4())
            persisted_id = _upsert_asset(tx, a, assigned_id, scan_id=ctx.scan_id)
            asset_id_by_ref[_asset_ref(a)] = persisted_id

        for r in relationships:
            source_id = asset_id_by_ref.get(r.source_asset_ref)
            target_id = asset_id_by_ref.get(r.target_asset_ref)
            if not source_id or not target_id:
                # Detector emitted a relationship pointing at an asset that
                # wasn't emitted in the same scan AND isn't the repo root —
                # skip silently rather than rolling back (benign for
                # cross-repo edges).
                continue
            _upsert_relationship(tx, r, source_id, target_id, scan_id=ctx.scan_id)

        for f in findings:
            subject_id = asset_id_by_ref.get(f.subject_ref) or f.subject_ref
            _insert_finding(tx, f, subject_id, scan_id=ctx.scan_id, ctx=ctx)

        _update_scan(tx, ctx, len(assets), len(relationships), len(findings),
                     status="success")

        _rds.commit_transaction(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, transactionId=tx,
        )
    except Exception:
        _rds.rollback_transaction(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, transactionId=tx,
        )
        raise


def mark_scan_failed(ctx, error_message: str) -> None:
    """Update an in-progress scan to status=failed (used when clone_repo errors)."""
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


def _asset_ref(a: AssetEmission) -> str:
    """Stable key for asset_id_by_ref before the row has an id."""
    return f"{a.asset_type}::{a.source_repo_id or ''}::{a.source_path or ''}::{a.name}"


def _upsert_asset(tx: str, a: AssetEmission, assigned_id: str, scan_id: str) -> str:
    """Insert-or-update an asset; returns the id of the row that ended up in
    the table. On ON CONFLICT, that's the EXISTING row's id (not the freshly
    generated assigned_id) — relationships in this scan must reference that
    persisted id or the FK on ai_relationships will reject them."""
    result = _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO ai_assets "
            "  (id, tenant_id, connection_id, asset_type, name, source_repo_id, "
            "   source_path, attributes, evidence_packet, detector_id, detector_version, scan_id) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        :atype, :name, "
            "        CASE WHEN :repo='' THEN NULL ELSE CAST(:repo AS UUID) END, "
            "        :spath, CAST(:attrs AS JSONB), CAST(:ev AS JSONB), "
            "        :did, :dver, CAST(:sid AS UUID)) "
            "ON CONFLICT (tenant_id, asset_type, source_repo_id, source_path, name) "
            "  DO UPDATE SET last_seen_at=NOW(), evidence_packet=EXCLUDED.evidence_packet, "
            "                attributes=EXCLUDED.attributes "
            "RETURNING id::text"
        ),
        parameters=[
            {"name": "id",    "value": {"stringValue": assigned_id}},
            {"name": "tid",   "value": {"stringValue": a.tenant_id}},
            {"name": "cid",   "value": {"stringValue": a.connection_id}},
            {"name": "atype", "value": {"stringValue": a.asset_type}},
            {"name": "name",  "value": {"stringValue": a.name}},
            {"name": "repo",  "value": {"stringValue": a.source_repo_id or ""}},
            {"name": "spath", "value": {"stringValue": a.source_path or ""}},
            {"name": "attrs", "value": {"stringValue": json.dumps(a.attributes)}},
            {"name": "ev",    "value": {"stringValue": json.dumps(a.evidence_packet)}},
            {"name": "did",   "value": {"stringValue": a.detector_id}},
            {"name": "dver",  "value": {"stringValue": a.detector_version}},
            {"name": "sid",   "value": {"stringValue": scan_id}},
        ],
    )
    rows = result.get("records", [])
    if rows and rows[0] and "stringValue" in rows[0][0]:
        return rows[0][0]["stringValue"]
    return assigned_id  # fallback (shouldn't happen with RETURNING)


def _upsert_relationship(tx: str, r: RelEmission, source_id: str, target_id: str,
                         scan_id: str) -> None:
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO ai_relationships "
            "  (id, tenant_id, source_asset_id, target_asset_id, relationship_type, "
            "   attributes, evidence_packet, detector_id, detector_version, scan_id) "
            "VALUES (CAST(:rid AS UUID), CAST(:tid AS UUID), CAST(:src AS UUID), "
            "        CAST(:tgt AS UUID), :rtype, CAST(:attrs AS JSONB), CAST(:ev AS JSONB), "
            "        :did, :dver, CAST(:sid AS UUID)) "
            "ON CONFLICT (source_asset_id, target_asset_id, relationship_type) "
            "  DO UPDATE SET last_seen_at=NOW(), evidence_packet=EXCLUDED.evidence_packet, "
            "                attributes=EXCLUDED.attributes"
        ),
        parameters=[
            {"name": "rid",   "value": {"stringValue": str(uuid.uuid4())}},
            {"name": "tid",   "value": {"stringValue": r.tenant_id}},
            {"name": "src",   "value": {"stringValue": source_id}},
            {"name": "tgt",   "value": {"stringValue": target_id}},
            {"name": "rtype", "value": {"stringValue": r.relationship_type}},
            {"name": "attrs", "value": {"stringValue": json.dumps(r.attributes)}},
            {"name": "ev",    "value": {"stringValue": json.dumps(r.evidence_packet)}},
            {"name": "did",   "value": {"stringValue": r.detector_id}},
            {"name": "dver",  "value": {"stringValue": r.detector_version}},
            {"name": "sid",   "value": {"stringValue": scan_id}},
        ],
    )


def _insert_finding(tx: str, f: FindingEmission, subject_id: str, scan_id: str, ctx) -> None:
    """Insert into the existing findings table with category='ai'."""
    fid = str(uuid.uuid4())
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        transactionId=tx,
        sql=(
            "INSERT INTO findings "
            "  (finding_id, tenant_id, conn_id, scan_id, check_id, title, description, "
            "   severity, status, resource_arn, resource_type, region, domain, frameworks, "
            "   remediation, first_seen, last_seen, evidence_packet) "
            "VALUES (CAST(:fid AS UUID), CAST(:tid AS UUID), CAST(:conn AS UUID), "
            "        CAST(:sid AS UUID), :ftype, :title, :desc, :sev, 'fail', :subj, "
            "        :stype, NULL, 'ai', '{}'::jsonb, NULL, NOW(), NOW(), CAST(:ev AS JSONB))"
        ),
        parameters=[
            {"name": "fid",   "value": {"stringValue": fid}},
            {"name": "tid",   "value": {"stringValue": f.tenant_id}},
            {"name": "conn",  "value": {"stringValue": ctx.connection_id}},
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "ftype", "value": {"stringValue": f.finding_type}},
            {"name": "title", "value": {"stringValue": f.title}},
            {"name": "desc",  "value": {"stringValue": f.description}},
            {"name": "sev",   "value": {"stringValue": f.severity}},
            {"name": "subj",  "value": {"stringValue": subject_id}},
            {"name": "stype", "value": {"stringValue": f.subject_type}},
            {"name": "ev",    "value": {"stringValue": json.dumps(f.evidence_packet)}},
        ],
    )


def _update_scan(tx: str, ctx, asset_count: int, rel_count: int, finding_count: int,
                 status: str) -> None:
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
            {"name": "ac",  "value": {"longValue":   asset_count}},
            {"name": "rc",  "value": {"longValue":   rel_count}},
            {"name": "fc",  "value": {"longValue":   finding_count}},
            {"name": "sv",  "value": {"stringValue": ctx.scanner_version}},
            {"name": "sid", "value": {"stringValue": ctx.scan_id}},
        ],
    )
