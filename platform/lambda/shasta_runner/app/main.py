"""shasta-runner — runs Shasta's AWS checks against a customer's account.

Invoked by Step Functions (or direct test invoke) with:
  {
    "scan_id":     "uuid",
    "tenant_id":   "uuid",
    "conn_id":     "uuid",
    "role_arn":    "arn:aws:iam::<customer-acct>:role/CISOCopilotReader",
    "external_id": "<one-time>",
    "account_id":  "<customer-acct>",
    "regions":     ["us-east-1", ...]   # optional; default ['us-east-1']
  }

Lifecycle:
  1. UPDATE scans SET status='running'.
  2. STS AssumeRole into the customer's account.
  3. Build boto3 clients off the assumed-role credentials.
  4. Run global enums (IAM, S3) once.
  5. For each region: run regional enums (compute, network) + Shasta modules.
  6. Convert Shasta findings → FindingEmission, derive entity FKs via ARN.
  7. Single transactional write via unified_writer.commit_scan.
  8. UPDATE scans SET status='completed' WITH stats.

Per-module failures are caught and logged — one bad module doesn't kill the
whole scan. The scan completes with whichever findings it could produce.
"""
from __future__ import annotations

import json
import os
import traceback
from dataclasses import dataclass
from typing import Any

import boto3

# === Shasta imports ===
from shasta.aws.client import AWSAccountInfo, AWSClient
from shasta.aws import (
    backup           as shasta_backup,
    cloudfront       as shasta_cloudfront,
    cloudwatch_logs  as shasta_logs,
    compute          as shasta_compute,
    data_warehouse   as shasta_dw,
    databases        as shasta_databases,
    encryption       as shasta_encryption,
    iam              as shasta_iam,
    kms              as shasta_kms,
    logging_checks   as shasta_logging,
    networking       as shasta_networking,
    organizations    as shasta_orgs,
    serverless       as shasta_serverless,
    storage          as shasta_storage,
    vpc_endpoints    as shasta_vpc_endpoints,
    vulnerabilities  as shasta_vulns,
)

# === Entity-emission helpers (this module) ===
from ai_pass           import run_ai_pass
from arn_to_entity     import parse_arn
from aws_config        import SCAN_BOTO_CONFIG
from assumed_role      import build_refreshable_credentials, session_from_credentials
from botocore.credentials import RefreshableCredentials
from coverage.engine   import run_coverage_for_region
from enumerate_compute import enumerate_compute
from enumerate_iam     import enumerate_iam
from enumerate_network import enumerate_network
from enumerate_storage import enumerate_storage
from framework_map     import merge_framework_map
from region_discovery  import discover_regions
from scan_pipeline     import ConcurrencyLimiter, ScanUnit, run_units
from scan_policy       import build_scan_plan

# === Shared writer + emission types (copied in by build.sh) ===
from detectors.base import EdgeEmission, EntityEmission, FindingEmission
from unified_writer import commit_scan, mark_scan_failed

# === Config ===
DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

_SCANNER_VERSION  = "shasta_runner.0.2.0"
_DETECTOR_ID_BASE = "shasta_runner"

rds_data = boto3.client("rds-data")
sts      = boto3.client("sts")

# Modules global to an account (no region iteration).
GLOBAL_MODULES = [
    ("iam",            shasta_iam.run_all_iam_checks),
    ("organizations",  shasta_orgs.run_all_aws_organizations_checks),
    ("cloudfront",     shasta_cloudfront.run_all_aws_cloudfront_checks),
    ("logging",        shasta_logging.run_all_logging_checks),
]

# Modules to run per-region.
REGIONAL_MODULES = [
    ("compute",        shasta_compute.run_all_aws_compute_checks),
    ("storage",        shasta_storage.run_all_storage_checks),
    ("networking",     shasta_networking.run_all_networking_checks),
    ("encryption",     shasta_encryption.run_all_encryption_checks),
    ("databases",      shasta_databases.run_all_aws_database_checks),
    ("backup",         shasta_backup.run_all_aws_backup_checks),
    ("logs",           shasta_logs.run_all_aws_cloudwatch_log_checks),
    ("vpc_endpoints",  shasta_vpc_endpoints.run_all_aws_vpc_endpoint_checks),
    ("kms",            shasta_kms.run_all_aws_kms_checks),
    ("data_warehouse", shasta_dw.run_all_aws_data_warehouse_checks),
    ("serverless",     shasta_serverless.run_all_aws_serverless_checks),
    ("vulnerabilities", shasta_vulns.run_all_vulnerability_checks),
]


@dataclass(frozen=True)
class CloudScanContext:
    """Minimal ScanContext for unified_writer (reads these fields by attr)."""
    scan_id:         str
    tenant_id:       str
    connection_id:   str
    scanner_version: str = _SCANNER_VERSION


class AssumedRoleAWSClient(AWSClient):
    """Shasta AWSClient that uses pre-fetched assumed-role credentials.

    Shasta's stock AWSClient relies on the default boto3 credential chain
    (profile / env / instance profile). We override `_create_session` to
    inject the credentials returned by STS:AssumeRole, and pre-populate
    `account_info` so per-check sts:GetCallerIdentity calls aren't needed.
    """

    def __init__(self, credentials: RefreshableCredentials, region: str, account_id: str,
                 scan_regions: list[str] | None = None):
        super().__init__(region=region)
        self._credentials = credentials
        self._scan_regions = scan_regions
        self._account_info = AWSAccountInfo(
            account_id=account_id,
            account_aliases=[],
            user_arn=f"arn:aws:iam::{account_id}:assumed-role/CISOCopilotReader/CISOCopilotScan",
            user_id="AROAEXAMPLE:CISOCopilotScan",
            region=region,
            services_in_use=[],
        )

    def _create_session(self) -> boto3.Session:
        return session_from_credentials(self._credentials, self._region)

    @property
    def account_info(self) -> AWSAccountInfo:
        return self._account_info

    def client(self, service_name: str, **kwargs):
        """Build a client with the shared timeout Config (a slow regional
        endpoint must never hang a scan). A caller-supplied config wins."""
        kwargs.setdefault("config", SCAN_BOTO_CONFIG)
        return super().client(service_name, **kwargs)

    def get_enabled_regions(self) -> list[str]:
        """Scope AI discovery (and any region-iterating Shasta check) to
        the scan's discovered active regions, not all ~17 enabled regions.
        Falls back to Shasta's all-enabled enumeration if no scan scope
        was supplied."""
        if self._scan_regions:
            return list(self._scan_regions)
        return super().get_enabled_regions()

    def for_region(self, region: str) -> "AssumedRoleAWSClient":
        """Return an AssumedRoleAWSClient for `region`, preserving the
        assumed-role credentials. Shasta's base for_region drops them and
        returns a credential-less, timeout-less base client."""
        return AssumedRoleAWSClient(self._credentials, region,
                                    self._account_info.account_id,
                                    scan_regions=self._scan_regions)


def handler(event: dict, context) -> dict:
    scan_id     = event["scan_id"]
    tenant_id   = event["tenant_id"]
    conn_id     = event["conn_id"]
    role_arn    = event["role_arn"]
    external_id = event["external_id"]
    account_id  = event["account_id"]
    explicit_regions = event.get("regions")
    scan_tier   = event.get("scan_tier", "quick")

    print(f"scan start: scan={scan_id} account={account_id} tier={scan_tier}")
    ctx = CloudScanContext(scan_id=scan_id, tenant_id=tenant_id, connection_id=conn_id)
    _update_scan(scan_id, status="running", phase="region_discovery")

    try:
        credentials = build_refreshable_credentials(sts, role_arn, external_id)
        boto_session = _make_session(credentials, "us-east-1")

        # --- Stage 1 + 2: region discovery -------------------------------
        if explicit_regions:
            region_states = {r: "active" for r in explicit_regions}
            discovery_method = "explicit_override"
            print(f"region scope: explicit override {list(explicit_regions)}")
        else:
            rd = discover_regions(
                boto_session.client("ec2", config=SCAN_BOTO_CONFIG),
                lambda region: (lambda service:
                    _make_session(credentials, region).client(
                        service, config=SCAN_BOTO_CONFIG)),
            )
            region_states = rd.region_states
            discovery_method = rd.method
            print(f"region discovery: method={discovery_method} "
                  f"states={region_states}")

        regions = sorted(region_states)
        plan = build_scan_plan(scan_tier, region_states)
        limiter = ConcurrencyLimiter(default=8, per_service={"iam": 3})

        # --- Stage 3: build + run scan units -----------------------------
        # coverage_map: region -> {state, modules_run, modules_skipped, errors}
        coverage_map = {r: {"state": region_states[r], "modules_run": [],
                            "modules_skipped": [], "errors": []}
                        for r in regions}
        entities: list[EntityEmission] = [_account_entity(account_id, tenant_id)]
        edges:    list[EdgeEmission]   = []
        findings: list[FindingEmission] = []

        global_units, region_units = _build_units(
            plan, credentials, account_id, tenant_id, regions, scan_tier)

        committed = 0
        if scan_tier == "quick":
            # Phase 1 — First Signal: global units, early commit.
            _update_scan(scan_id, status="running", phase="first_signal")
            r1 = run_units(global_units, limiter=limiter)
            _absorb(r1, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))
            committed = len(findings)
            print(f"quick phase 1 committed: {committed} findings")
            # Phase 2 — Crown Jewel: per-region units.
            _update_scan(scan_id, status="running", phase="crown_jewel")
            r2 = run_units(region_units, limiter=limiter)
            _absorb(r2, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))
        else:
            _update_scan(scan_id, status="running", phase="full")
            res = run_units(global_units + region_units, limiter=limiter)
            _absorb(res, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))

        # A scan with any unit error/timeout is 'partial', else 'completed'.
        had_gap = any(c["errors"] for c in coverage_map.values())
        final_status = "partial" if had_gap else "completed"
        _record_scan_scope(scan_id, scan_tier, discovery_method, coverage_map)
        _update_scan(scan_id, status=final_status, phase="done", stats={
            "entities": len(entities), "edges": len(edges),
            "findings": len(findings), "tier": scan_tier,
            "regions": regions,
        })
        print(f"scan complete ({final_status}): {len(entities)} entities, "
              f"{len(edges)} edges, {len(findings)} findings")
        return {"scan_id": scan_id, "status": final_status,
                "findings_written": len(findings)}

    except Exception as e:
        err = f"{e}: {traceback.format_exc()}"[:1000]
        print(f"SCAN FAILED: {err}")
        try:
            mark_scan_failed(ctx, err)
        except Exception:
            pass
        _update_scan(scan_id, status="failed", phase="done", error=err)
        raise


# ============================================================================
# Scan-unit builders + absorb
# ============================================================================

def _build_units(plan, credentials, account_id, tenant_id, regions, scan_tier):
    """Build the global and per-region ScanUnits for `plan`.

    Returns (global_units, region_units). Each unit's `run` returns
    {entities, edges, findings}; Shasta findings are converted to
    FindingEmission inside the unit (per-unit, no shared state — safe to
    run concurrently)."""
    global_units: list[ScanUnit] = []
    region_units: list[ScanUnit] = []

    # Global entity enums (IAM, S3).
    if plan.run_global_enums:
        global_units.append(ScanUnit(
            name="global/enum_iam", service="iam",
            run=lambda: _enum_unit(enumerate_iam,
                _make_session(credentials, "us-east-1").client("iam", config=SCAN_BOTO_CONFIG),
                account_id=account_id, tenant_id=tenant_id)))
        global_units.append(ScanUnit(
            name="global/enum_s3", service="s3",
            run=lambda: _enum_unit(enumerate_storage,
                _make_session(credentials, "us-east-1").client("s3", config=SCAN_BOTO_CONFIG),
                account_id=account_id, tenant_id=tenant_id)))

    # Global Shasta modules.
    if plan.global_modules:
        for name, run_fn in GLOBAL_MODULES:
            global_units.append(ScanUnit(
                name=f"global/{name}", service=name,
                run=_shasta_unit_fn(run_fn, credentials, "us-east-1",
                                    account_id, tenant_id, regions)))

    # AI pass (Medium+) — a single global unit.
    if plan.run_ai_pass:
        global_units.append(ScanUnit(
            name="global/ai_pass", service="ai",
            run=_ai_unit_fn(credentials, "us-east-1", account_id, tenant_id, regions)))

    # Per-region units.
    for region, rp in plan.per_region.items():
        if rp.run_enums:
            region_units.append(ScanUnit(
                name=f"{region}/enum_compute", service="ec2",
                run=_compute_enum_fn(credentials, region, account_id, tenant_id)))
            region_units.append(ScanUnit(
                name=f"{region}/enum_network", service="ec2",
                run=_network_enum_fn(credentials, region, account_id, tenant_id)))
        if rp.coverage:
            region_units.append(ScanUnit(
                name=f"{region}/coverage", service="coverage",
                run=_coverage_unit_fn(credentials, region, account_id,
                                      tenant_id, scan_tier)))
        if rp.regional_shasta:
            for name, run_fn in REGIONAL_MODULES:
                region_units.append(ScanUnit(
                    name=f"{region}/{name}", service=name,
                    run=_shasta_unit_fn(run_fn, credentials, region,
                                        account_id, tenant_id, regions)))
    return global_units, region_units


def _enum_unit(enum_fn, client, **kw) -> dict:
    out = enum_fn(client, **kw)
    return {"entities": out["entities"], "edges": out["edges"], "findings": []}


def _compute_enum_fn(credentials, region, account_id, tenant_id):
    def _run():
        s = _make_session(credentials, region)
        out = enumerate_compute(
            s.client("ec2", config=SCAN_BOTO_CONFIG),
            s.client("lambda", config=SCAN_BOTO_CONFIG),
            account_id=account_id, tenant_id=tenant_id, region=region)
        return {"entities": out["entities"], "edges": out["edges"], "findings": []}
    return _run


def _network_enum_fn(credentials, region, account_id, tenant_id):
    def _run():
        s = _make_session(credentials, region)
        out = enumerate_network(
            s.client("ec2", config=SCAN_BOTO_CONFIG),
            account_id=account_id, tenant_id=tenant_id, region=region)
        return {"entities": out["entities"], "edges": out["edges"], "findings": []}
    return _run


def _shasta_unit_fn(run_fn, credentials, region, account_id, tenant_id, regions):
    def _run():
        client = AssumedRoleAWSClient(credentials, region, account_id,
                                      scan_regions=regions)
        shasta_findings = run_fn(client)
        return convert_shasta_findings(shasta_findings, tenant_id, account_id)
    return _run


def _ai_unit_fn(credentials, region, account_id, tenant_id, regions):
    def _run():
        client = AssumedRoleAWSClient(credentials, region, account_id,
                                      scan_regions=regions)
        ai = run_ai_pass(client, account_id=account_id, tenant_id=tenant_id)
        return {"entities": ai["entities"], "edges": ai["edges"],
                "findings": ai["findings"]}
    return _run


def _coverage_unit_fn(credentials, region, account_id, tenant_id, scan_tier):
    def _run():
        return run_coverage_for_region(
            _make_session(credentials, region), region,
            account_id=account_id, tenant_id=tenant_id, scan_tier=scan_tier)
    return _run


def _absorb(results, entities, edges, findings, coverage_map):
    """Merge a run_units UnitResults into the scan accumulators and the
    coverage map. Unit name format is 'region/module' or 'global/module'."""
    entities.extend(results.entities)
    edges.extend(results.edges)
    findings.extend(results.findings)
    for o in results.outcomes:
        region = o.name.split("/", 1)[0]
        bucket = coverage_map.get(region)
        if bucket is None:           # 'global/...' units
            continue
        if o.status == "success":
            bucket["modules_run"].append(o.name)
        else:
            bucket["errors"].append(f"{o.status}: {o.name} {o.detail}".strip())
    return findings


# ============================================================================
# Session helper
# ============================================================================

def _make_session(credentials, region: str) -> boto3.Session:
    return session_from_credentials(credentials, region)


# ============================================================================
# Entity helpers
# ============================================================================

def _account_entity(account_id: str, tenant_id: str) -> EntityEmission:
    return EntityEmission(
        tenant_id=tenant_id,
        kind="aws_account",
        natural_key=account_id,
        display_name=account_id,
        domain="cloud",
        attributes={"service": "aws", "account": account_id},
        evidence_packet=None,
        detector_id=f"{_DETECTOR_ID_BASE}.account",
        detector_version="0.1.0",
    )


def convert_shasta_findings(shasta_findings: list[Any], tenant_id: str,
                            account_id: str) -> dict:
    """Convert Shasta Finding objects to a {entities, edges, findings}
    dict — pure, no shared state, safe to call concurrently. ARN-derived
    subject entities + 'contains' edges are emitted alongside; the
    writer's natural-key UPSERT dedupes overlaps across units."""
    out_findings: list[FindingEmission] = []
    out_entities: list[EntityEmission] = []
    out_edges:    list[EdgeEmission]   = []
    seen: set[tuple[str, str]] = set()

    for f in shasta_findings:
        if f.status.value.lower() in ("not_assessed", "not_applicable"):
            continue
        arn = (getattr(f, "resource_id", "") or "").strip()
        subj_kind = subj_nk = None
        parsed = parse_arn(arn) if arn else None
        if parsed:
            subj_kind, subj_nk = parsed["kind"], parsed["natural_key"]
            if (subj_kind, subj_nk) not in seen:
                seen.add((subj_kind, subj_nk))
                out_entities.append(EntityEmission(
                    tenant_id=tenant_id, kind=subj_kind, natural_key=subj_nk,
                    display_name=parsed["display_name"], domain="cloud",
                    attributes=parsed["attributes"], evidence_packet=None,
                    detector_id=f"{_DETECTOR_ID_BASE}.finding_arn",
                    detector_version="0.1.0"))
                out_edges.append(EdgeEmission(
                    tenant_id=tenant_id, source_kind="aws_account",
                    source_natural_key=account_id, target_kind=subj_kind,
                    target_natural_key=subj_nk, kind="contains", attributes={},
                    evidence_packet={"version": "0.1", "via": "finding.resource_id"},
                    detector_id=f"{_DETECTOR_ID_BASE}.finding_arn",
                    detector_version="0.1.0"))
        out_findings.append(_shasta_to_emission(f, tenant_id, subj_kind, subj_nk))

    return {"entities": out_entities, "edges": out_edges, "findings": out_findings}


def _shasta_to_emission(
    f, tenant_id: str, subj_kind: str | None, subj_nk: str | None,
) -> FindingEmission:
    frameworks = {
        "soc2":      f.soc2_controls,
        "cis_aws":   f.cis_aws_controls,
        "cis_azure": f.cis_azure_controls,
        "cis_gcp":   f.cis_gcp_controls,
        "mcsb":      f.mcsb_controls,
        "iso27001":  f.iso27001_controls,
        "hipaa":     f.hipaa_controls,
    }
    frameworks = {k: v for k, v in frameworks.items() if v}
    frameworks = merge_framework_map(f.check_id, frameworks)

    status = f.status.value.lower()
    domain = f.domain.value.lower()
    if domain == "ai_governance":
        domain = "ai"
    region = f.region or None

    evidence = {
        "version":     "0.1",
        "shasta": {
            "check_id":      f.check_id,
            "status":        status,
            "domain":        domain,
            "region":        f.region,
            "resource_type": f.resource_type,
            "resource_id":   f.resource_id,
            "remediation":   (f.remediation or "")[:2000],
            "frameworks":    frameworks,
            "details":       _safe_details(getattr(f, "details", None)),
        },
    }
    return FindingEmission(
        tenant_id=tenant_id,
        finding_type=f.check_id,
        severity=f.severity.value.lower(),
        title=f.title[:500],
        description=(f.description or "")[:2000],
        subject_entity_kind=subj_kind,
        subject_entity_natural_key=subj_nk,
        subject_type=f.resource_type[:200] if f.resource_type else None,
        subject_ref=(f.resource_id or "")[:500] if f.resource_id else None,
        evidence_packet=evidence,
        confidence="high",
        frameworks=frameworks,
        domain=domain,
        status=status,
        region=region,
    )


def _safe_details(details) -> dict:
    """Coerce Shasta finding details into a JSON-safe dict (best effort)."""
    if not details:
        return {}
    try:
        json.dumps(details)
        return details
    except TypeError:
        return {"_repr": str(details)[:1000]}


# ============================================================================
# Legacy `scans` table updates (kept — separate from ai_scans / commit_scan)
# ============================================================================

def _record_scan_scope(scan_id: str, scan_tier: str, discovery_method: str,
                       coverage_map: dict) -> None:
    """Write the per-scan coverage map to scans.scope (spec §9)."""
    scope = {
        "tier": scan_tier,
        "discovery": {"method": discovery_method},
        "regions": coverage_map,
    }
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("UPDATE scans SET scope = CAST(:scope AS JSONB) "
             "WHERE scan_id = CAST(:sid AS UUID)"),
        parameters=[
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )


def _update_scan(scan_id: str, status: str, *, phase: str | None = None,
                  stats: dict | None = None, error: str | None = None) -> None:
    sql_parts = ["UPDATE scans SET status = :status"]
    params = [
        {"name": "sid",    "value": {"stringValue": scan_id}},
        {"name": "status", "value": {"stringValue": status}},
    ]
    if phase is not None:
        sql_parts.append("phase = :phase")
        params.append({"name": "phase", "value": {"stringValue": phase}})
    if status in ("completed", "failed", "partial"):
        sql_parts.append("finished_at = now()")
    if stats is not None:
        sql_parts.append("stats = CAST(:stats AS JSONB)")
        params.append({"name": "stats", "value": {"stringValue": json.dumps(stats)}})
    if error is not None:
        sql_parts.append("error = :error")
        params.append({"name": "error", "value": {"stringValue": error}})
    sql = ", ".join(sql_parts) + " WHERE scan_id = CAST(:sid AS UUID)"
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params)
