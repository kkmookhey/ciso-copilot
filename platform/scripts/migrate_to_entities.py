#!/usr/bin/env python3
"""One-shot data migration: ai_assets/ai_relationships → entities/edges.

Run from KK's laptop. Idempotent (re-runnable; uses ON CONFLICT DO NOTHING
throughout). See:
  - spec   docs/superpowers/specs/2026-05-19-sp1-unified-entity-model-design.md §13
  - plan   docs/superpowers/plans/2026-05-19-sp1-unified-entity-model.md Task 22

Logical steps:
  1. Read all ai_assets rows.
  2. Build {old_asset_id → repo full_name} for per-file natural_key joins.
  3. Upsert one entities row per ai_assets row, deriving (kind, natural_key)
     per the table in spec §5. Cross-repo dedup happens automatically
     because (tenant_id, kind, natural_key) is a UNIQUE constraint.
  4. SELECT back the persisted entity id for every row → build the
     {old_asset_id → new_entity_id} remap.
  5. Migrate ai_relationships rows into edges, remapping source/target.
  6. UPDATE findings SET subject_entity_id where resource_arn matches an
     entity natural_key for the same tenant.
  7. Print summary (entity counts per kind, edge counts per kind, findings
     linked, model-dedup delta — proves cross-repo unification worked).

Usage:
  python3 platform/scripts/migrate_to_entities.py            # run it
  python3 platform/scripts/migrate_to_entities.py --dry-run  # print plan, no writes
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from collections import Counter

CLUSTER_ARN = "arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh"
SECRET_ARN  = "arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp"
DB_NAME     = "ciso_copilot"
REGION      = "us-east-1"

# Asset-types that dedup across the whole tenant (natural_key = bare name).
CROSS_REPO_KINDS = {"framework", "model", "vector_db", "embedding"}
# Asset-types whose natural_key embeds the source repo + path (per-file scope).
PER_FILE_KINDS   = {"mcp_server", "tool", "agent", "prompt"}


# ---------- RDS Data API helper ----------

def run_sql(sql: str, parameters=None) -> dict:
    """Execute a SQL statement via the rds-data CLI. Returns the parsed JSON."""
    cmd = [
        "aws", "rds-data", "execute-statement",
        "--resource-arn", CLUSTER_ARN,
        "--secret-arn", SECRET_ARN,
        "--database", DB_NAME,
        "--region", REGION,
        "--sql", sql,
    ]
    if parameters:
        cmd += ["--parameters", json.dumps(parameters)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(f"\nSQL FAILED:\n  sql: {sql}\n  stderr: {result.stderr}\n")
        raise SystemExit(result.returncode)
    return json.loads(result.stdout) if result.stdout else {}


def _cell(field: dict) -> str | None:
    """Pull a value out of an rds-data response cell. Returns None for SQL NULL."""
    if field.get("isNull"):
        return None
    return field.get("stringValue")


# ---------- natural-key derivation (spec §5) ----------

def derive_kind_and_nk(
    asset_type: str,
    name: str,
    source_repo_id: str | None,
    source_path: str | None,
    repo_name_by_id: dict[str, str],
) -> tuple[str | None, str | None, str | None]:
    """Return (kind, natural_key, skip_reason). skip_reason is non-None iff we
    cannot derive a key and the row should be skipped."""
    if asset_type in CROSS_REPO_KINDS:
        return f"ai_{asset_type}", name, None

    if asset_type in PER_FILE_KINDS:
        if not source_repo_id:
            return None, None, f"per-file kind {asset_type!r} missing source_repo_id"
        repo_name = repo_name_by_id.get(source_repo_id)
        if not repo_name:
            return None, None, (
                f"per-file kind {asset_type!r} references source_repo_id "
                f"{source_repo_id} which is not a 'repository' asset"
            )
        repo_nk = f"github.com/{repo_name}"
        nk = f"{repo_nk}::{source_path or ''}::{name}"
        return f"ai_{asset_type}", nk, None

    if asset_type == "repository":
        # name is the full_name like "kkmookhey/ciso-copilot"
        return "github_repo", f"github.com/{name}", None

    return None, None, f"unknown asset_type {asset_type!r}"


def domain_for(kind: str) -> str:
    if kind == "github_repo":
        return "repo"
    if kind.startswith("ai_"):
        return "ai"
    # Cloud kinds aren't produced by this migration but kept for completeness.
    if kind.startswith("aws_"):
        return "cloud"
    return "ai"


# ---------- migration steps ----------

def load_ai_assets() -> list[dict]:
    """Return ai_assets rows shaped as dicts (one per row)."""
    res = run_sql(
        "SELECT id::text, tenant_id::text, asset_type, name, "
        "       source_repo_id::text, source_path, attributes::text, "
        "       evidence_packet::text, detector_id, detector_version, "
        "       connection_id::text "
        "FROM ai_assets "
        "ORDER BY first_seen_at"
    )
    rows: list[dict] = []
    for r in res.get("records", []):
        rows.append({
            "id":               _cell(r[0]),
            "tenant_id":        _cell(r[1]),
            "asset_type":       _cell(r[2]),
            "name":             _cell(r[3]),
            "source_repo_id":   _cell(r[4]),
            "source_path":      _cell(r[5]),
            "attributes":       _cell(r[6]) or "{}",
            "evidence_packet":  _cell(r[7]),
            "detector_id":      _cell(r[8]),
            "detector_version": _cell(r[9]),
            "connection_id":    _cell(r[10]),
        })
    return rows


def upsert_entities(
    assets: list[dict],
    repo_name_by_id: dict[str, str],
    dry_run: bool,
) -> tuple[dict[str, str], Counter, list[str]]:
    """For each asset, derive (kind, natural_key) and INSERT … ON CONFLICT
    DO NOTHING into entities. Then SELECT back the id to build the
    {old_id → new_id} remap. Returns (old_to_new, kind_counter, skipped_msgs).
    kind_counter counts *distinct* entities per kind (post-dedup), so two
    ai_assets rows that collapse onto one entity only count once."""
    old_to_new: dict[str, str] = {}
    seen_ids_by_kind: dict[str, set[str]] = {}
    skipped: list[str] = []

    for a in assets:
        kind, nk, skip_reason = derive_kind_and_nk(
            a["asset_type"], a["name"], a["source_repo_id"],
            a["source_path"], repo_name_by_id,
        )
        if skip_reason:
            msg = f"SKIP id={a['id']} name={a['name']!r}: {skip_reason}"
            skipped.append(msg)
            print(f"  {msg}")
            continue

        new_id = str(uuid.uuid4())
        domain = domain_for(kind)

        if dry_run:
            old_to_new[a["id"]] = new_id  # synthetic mapping for dry-run flow
            # In dry-run we don't have a DB-resolved id to dedupe against, so
            # approximate dedup using (tenant_id, kind, natural_key) as the key.
            dedup_key = f"{a['tenant_id']}::{kind}::{nk}"
            seen_ids_by_kind.setdefault(kind, set()).add(dedup_key)
            continue

        run_sql(
            "INSERT INTO entities (id, tenant_id, kind, natural_key, display_name, "
            "  domain, attributes, evidence_packet, detector_id, detector_version) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), :kind, :nk, :dn, :dom, "
            "        CAST(:attrs AS JSONB), "
            "        CASE WHEN :ev = '' THEN NULL ELSE CAST(:ev AS JSONB) END, "
            "        :did, :dver) "
            "ON CONFLICT (tenant_id, kind, natural_key) DO NOTHING",
            [
                {"name": "id",    "value": {"stringValue": new_id}},
                {"name": "tid",   "value": {"stringValue": a["tenant_id"]}},
                {"name": "kind",  "value": {"stringValue": kind}},
                {"name": "nk",    "value": {"stringValue": nk}},
                {"name": "dn",    "value": {"stringValue": a["name"]}},
                {"name": "dom",   "value": {"stringValue": domain}},
                {"name": "attrs", "value": {"stringValue": a["attributes"]}},
                {"name": "ev",    "value": {"stringValue": a["evidence_packet"] or ""}},
                {"name": "did",   "value": {"stringValue": a["detector_id"]}},
                {"name": "dver",  "value": {"stringValue": a["detector_version"]}},
            ],
        )

        # SELECT back the now-persisted id (works for both our just-inserted
        # row and a row that was already present from a previous run / cross-
        # repo dedupe). ON CONFLICT DO NOTHING does not RETURN anything.
        resolved = run_sql(
            "SELECT id::text FROM entities "
            "WHERE tenant_id = CAST(:tid AS UUID) AND kind = :kind AND natural_key = :nk",
            [
                {"name": "tid",  "value": {"stringValue": a["tenant_id"]}},
                {"name": "kind", "value": {"stringValue": kind}},
                {"name": "nk",   "value": {"stringValue": nk}},
            ],
        )
        if not resolved.get("records"):
            msg = f"SKIP id={a['id']}: entity insert reported no rows for kind={kind} nk={nk}"
            skipped.append(msg)
            print(f"  {msg}")
            continue

        resolved_id = resolved["records"][0][0]["stringValue"]
        old_to_new[a["id"]] = resolved_id
        seen_ids_by_kind.setdefault(kind, set()).add(resolved_id)

    kind_counter: Counter[str] = Counter({
        k: len(ids) for k, ids in seen_ids_by_kind.items()
    })
    return old_to_new, kind_counter, skipped


def migrate_edges(
    old_to_new: dict[str, str],
    dry_run: bool,
) -> tuple[int, Counter, list[str]]:
    res = run_sql(
        "SELECT source_asset_id::text, target_asset_id::text, relationship_type, "
        "       attributes::text, evidence_packet::text, "
        "       detector_id, detector_version, tenant_id::text "
        "FROM ai_relationships"
    )
    edge_kind_counter: Counter[str] = Counter()
    skipped: list[str] = []
    migrated = 0

    for r in res.get("records", []):
        src_old = _cell(r[0])
        tgt_old = _cell(r[1])
        rel     = _cell(r[2]) or ""
        src_new = old_to_new.get(src_old) if src_old else None
        tgt_new = old_to_new.get(tgt_old) if tgt_old else None
        if not src_new or not tgt_new:
            msg = (f"SKIP edge {rel}: src={src_old} tgt={tgt_old} — "
                   f"one side missing from remap")
            skipped.append(msg)
            print(f"  {msg}")
            continue

        new_id     = str(uuid.uuid4())
        tenant_id  = _cell(r[7])
        attrs      = _cell(r[3]) or "{}"
        evidence   = _cell(r[4]) or "{}"
        detector_id      = _cell(r[5]) or ""
        detector_version = _cell(r[6]) or ""

        if not dry_run:
            run_sql(
                "INSERT INTO edges (id, tenant_id, source_entity_id, target_entity_id, "
                "  kind, attributes, evidence_packet, detector_id, detector_version) "
                "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), CAST(:src AS UUID), "
                "        CAST(:tgt AS UUID), :kind, CAST(:attrs AS JSONB), "
                "        CAST(:ev AS JSONB), :did, :dver) "
                "ON CONFLICT (source_entity_id, target_entity_id, kind) DO NOTHING",
                [
                    {"name": "id",    "value": {"stringValue": new_id}},
                    {"name": "tid",   "value": {"stringValue": tenant_id}},
                    {"name": "src",   "value": {"stringValue": src_new}},
                    {"name": "tgt",   "value": {"stringValue": tgt_new}},
                    {"name": "kind",  "value": {"stringValue": rel}},
                    {"name": "attrs", "value": {"stringValue": attrs}},
                    {"name": "ev",    "value": {"stringValue": evidence}},
                    {"name": "did",   "value": {"stringValue": detector_id}},
                    {"name": "dver",  "value": {"stringValue": detector_version}},
                ],
            )

        edge_kind_counter[rel] += 1
        migrated += 1

    return migrated, edge_kind_counter, skipped


def backfill_findings(dry_run: bool) -> int:
    """Populate findings.subject_entity_id where resource_arn resolves to an
    entity natural_key in the same tenant. Returns the count of rows updated."""
    if dry_run:
        # Preview only: count rows that *would* be updated.
        res = run_sql(
            "SELECT COUNT(*) "
            "FROM findings f "
            "JOIN entities e "
            "  ON e.tenant_id = f.tenant_id "
            " AND e.natural_key = f.resource_arn "
            "WHERE f.subject_entity_id IS NULL "
            "  AND f.resource_arn IS NOT NULL "
            "  AND f.resource_arn != ''"
        )
        records = res.get("records") or [[{"longValue": 0}]]
        return int(records[0][0].get("longValue", 0))

    res = run_sql(
        "UPDATE findings f "
        "SET subject_entity_id = e.id "
        "FROM entities e "
        "WHERE f.subject_entity_id IS NULL "
        "  AND f.resource_arn IS NOT NULL "
        "  AND f.resource_arn != '' "
        "  AND e.natural_key = f.resource_arn "
        "  AND e.tenant_id  = f.tenant_id"
    )
    return int(res.get("numberOfRecordsUpdated", 0))


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Read ai_assets/ai_relationships and print what would happen, but "
             "do not write to entities/edges/findings.",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN — no writes will be issued ===\n")

    # ---- Step 1: load ai_assets ----
    print("STEP 1 — loading ai_assets …")
    assets = load_ai_assets()
    print(f"  loaded {len(assets)} rows")

    # ---- Step 2: build repo_name_by_id ----
    repo_name_by_id: dict[str, str] = {
        a["id"]: a["name"] for a in assets if a["asset_type"] == "repository"
    }
    print(f"  found {len(repo_name_by_id)} repository asset(s)")

    # Count source asset_types for the dedup-delta summary at the end.
    asset_kind_pre = Counter(f"ai_{a['asset_type']}" if a["asset_type"] != "repository"
                             else "github_repo" for a in assets)

    # ---- Step 3+4: upsert entities, build old→new map ----
    print("\nSTEP 2 — upserting entities …")
    old_to_new, kind_counter, ent_skipped = upsert_entities(
        assets, repo_name_by_id, args.dry_run,
    )
    print(f"  mapped {len(old_to_new)} ai_assets → entities "
          f"({len(ent_skipped)} skipped)")

    # ---- Step 5: migrate edges ----
    print("\nSTEP 3 — migrating ai_relationships → edges …")
    edges_migrated, edge_kind_counter, edge_skipped = migrate_edges(
        old_to_new, args.dry_run,
    )
    print(f"  migrated {edges_migrated} edges ({len(edge_skipped)} skipped)")

    # ---- Step 6: backfill findings ----
    print("\nSTEP 4 — backfilling findings.subject_entity_id …")
    backfilled = backfill_findings(args.dry_run)
    verb = "would update" if args.dry_run else "updated"
    print(f"  {verb} {backfilled} findings row(s)")

    # ---- Step 7: summary ----
    print("\n========== SUMMARY ==========")
    print("Entities by kind:")
    for kind, count in sorted(kind_counter.items()):
        pre_count = asset_kind_pre.get(kind, count)
        delta = pre_count - count
        delta_note = f"   (deduped {delta} from {pre_count} source rows)" if delta > 0 else ""
        print(f"  {kind:20s} {count}{delta_note}")

    if edge_kind_counter:
        print("\nEdges by kind:")
        for kind, count in sorted(edge_kind_counter.items()):
            print(f"  {kind:20s} {count}")
    else:
        print("\nEdges by kind: (none)")

    print(f"\nFindings linked to entities: {backfilled}")

    # Model-dedup delta — proves cross-repo unification worked.
    model_src = asset_kind_pre.get("ai_model", 0)
    model_ent = kind_counter.get("ai_model", 0)
    framework_src = asset_kind_pre.get("ai_framework", 0)
    framework_ent = kind_counter.get("ai_framework", 0)
    print(f"\nModel dedup:     {model_src} ai_assets → {model_ent} entities "
          f"(delta {model_src - model_ent})")
    print(f"Framework dedup: {framework_src} ai_assets → {framework_ent} entities "
          f"(delta {framework_src - framework_ent})")

    if ent_skipped or edge_skipped:
        print(f"\nSkipped: {len(ent_skipped)} entities, {len(edge_skipped)} edges "
              f"(see lines tagged SKIP above)")

    if args.dry_run:
        print("\n(dry run — nothing was written)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
