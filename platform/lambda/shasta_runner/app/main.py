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
from coverage.engine   import run_coverage
from enumerate_compute import enumerate_compute
from enumerate_iam     import enumerate_iam
from enumerate_network import enumerate_network
from enumerate_storage import enumerate_storage
from framework_map     import merge_framework_map
from region_discovery  import discover_regions

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
    _update_scan(scan_id, status="running")

    try:
        credentials = build_refreshable_credentials(sts, role_arn, external_id)
        boto_session = _make_session(credentials, "us-east-1")

        # --- Step 0: region discovery. Pick the regions to scan from the
        # account's real footprint. An explicit event 'regions' overrides
        # discovery (operator re-scan of a specific region).
        if explicit_regions:
            regions = list(explicit_regions)
            region_discovery = None
            print(f"region scope: explicit override {regions}")
        else:
            region_discovery = discover_regions(
                boto_session.client("ec2", config=SCAN_BOTO_CONFIG),
                lambda r: _make_session(credentials, r).client(
                    "resourcegroupstaggingapi", config=SCAN_BOTO_CONFIG),
            )
            regions = region_discovery.active_regions
            print(f"region discovery: method={region_discovery.method} "
                  f"active={regions} "
                  f"skipped_empty={len(region_discovery.skipped_empty)} "
                  f"errored={region_discovery.errored_regions}")
        _record_scan_scope(scan_id, regions, region_discovery)

        # Always emit the account entity itself.
        entities: list[EntityEmission] = [_account_entity(account_id, tenant_id)]
        edges:    list[EdgeEmission]   = []
        all_shasta_findings: list[Any] = []
        module_stats: dict[str, dict[str, int]] = {}

        # --- Global enums (IAM + S3, single pass) -------------------------
        try:
            iam_out = enumerate_iam(
                boto_session.client("iam", config=SCAN_BOTO_CONFIG),
                account_id=account_id, tenant_id=tenant_id,
            )
            entities.extend(iam_out["entities"])
            edges.extend(iam_out["edges"])
            module_stats["enum/iam"] = {
                "entities": len(iam_out["entities"]),
                "edges":    len(iam_out["edges"]),
            }
        except Exception as e:
            print(f"enum/iam FAILED: {e}\n{traceback.format_exc()}")
            module_stats["enum/iam"] = {"error": str(e)[:200]}

        try:
            s3_out = enumerate_storage(
                boto_session.client("s3", config=SCAN_BOTO_CONFIG),
                account_id=account_id, tenant_id=tenant_id,
            )
            entities.extend(s3_out["entities"])
            edges.extend(s3_out["edges"])
            module_stats["enum/s3"] = {
                "entities": len(s3_out["entities"]),
                "edges":    len(s3_out["edges"]),
            }
        except Exception as e:
            print(f"enum/s3 FAILED: {e}\n{traceback.format_exc()}")
            module_stats["enum/s3"] = {"error": str(e)[:200]}

        # --- Global Shasta modules (region 'us-east-1' for the session) ---
        for name, run_fn in GLOBAL_MODULES:
            try:
                client = AssumedRoleAWSClient(credentials, "us-east-1", account_id, scan_regions=regions)
                findings = run_fn(client)
                all_shasta_findings.extend(findings)
                module_stats[name] = {"findings": len(findings)}
                print(f"global/{name}: {len(findings)} findings")
            except Exception as e:
                print(f"global/{name} FAILED: {e}\n{traceback.format_exc()}")
                module_stats[name] = {"error": str(e)[:200]}

        # --- Per-region enums + Shasta modules ---------------------------
        for region in regions:
            region_session = _make_session(credentials, region)

            # Compute enum
            try:
                comp_out = enumerate_compute(
                    region_session.client("ec2", config=SCAN_BOTO_CONFIG),
                    region_session.client("lambda", config=SCAN_BOTO_CONFIG),
                    account_id=account_id, tenant_id=tenant_id, region=region,
                )
                entities.extend(comp_out["entities"])
                edges.extend(comp_out["edges"])
                module_stats[f"{region}/enum/compute"] = {
                    "entities": len(comp_out["entities"]),
                    "edges":    len(comp_out["edges"]),
                }
            except Exception as e:
                print(f"{region}/enum/compute FAILED: {e}\n{traceback.format_exc()}")
                module_stats[f"{region}/enum/compute"] = {"error": str(e)[:200]}

            # Network enum
            try:
                net_out = enumerate_network(
                    region_session.client("ec2", config=SCAN_BOTO_CONFIG),
                    account_id=account_id, tenant_id=tenant_id, region=region,
                )
                entities.extend(net_out["entities"])
                edges.extend(net_out["edges"])
                module_stats[f"{region}/enum/network"] = {
                    "entities": len(net_out["entities"]),
                    "edges":    len(net_out["edges"]),
                }
            except Exception as e:
                print(f"{region}/enum/network FAILED: {e}\n{traceback.format_exc()}")
                module_stats[f"{region}/enum/network"] = {"error": str(e)[:200]}

            # Shasta regional checks
            for name, run_fn in REGIONAL_MODULES:
                key = f"{region}/{name}"
                try:
                    client = AssumedRoleAWSClient(credentials, region, account_id, scan_regions=regions)
                    findings = run_fn(client)
                    all_shasta_findings.extend(findings)
                    module_stats[key] = {"findings": len(findings)}
                    print(f"{key}: {len(findings)} findings")
                except Exception as e:
                    print(f"{key} FAILED: {e}\n{traceback.format_exc()}")
                    module_stats[key] = {"error": str(e)[:200]}

        # --- Cloud-AI pass: Shasta AI discovery + 15 AI checks + framework mapping.
        # Wrapped like every other module so one failure doesn't kill the scan.
        ai_finding_emissions: list[FindingEmission] = []
        try:
            ai_client = AssumedRoleAWSClient(credentials, "us-east-1", account_id, scan_regions=regions)
            ai_result = run_ai_pass(ai_client, account_id=account_id, tenant_id=tenant_id)
            entities.extend(ai_result["entities"])
            edges.extend(ai_result["edges"])
            ai_finding_emissions = ai_result["findings"]
            module_stats["ai_pass"] = {
                "entities": len(ai_result["entities"]),
                "findings": len(ai_result["findings"]),
            }
            print(f"ai_pass: {len(ai_result['entities'])} entities, "
                  f"{len(ai_result['findings'])} findings")
        except Exception as e:
            print(f"ai_pass FAILED: {e}\n{traceback.format_exc()}")
            module_stats["ai_pass"] = {"error": str(e)[:200]}

        # --- Coverage engine: in-repo posture checks, tier-filtered.
        # Wrapped like every other pass so one failure doesn't kill the scan.
        coverage_finding_emissions: list[FindingEmission] = []
        try:
            coverage_result = run_coverage(
                lambda region: _make_session(credentials, region),
                account_id=account_id, tenant_id=tenant_id,
                regions=regions, scan_tier=scan_tier,
            )
            entities.extend(coverage_result["entities"])
            edges.extend(coverage_result["edges"])
            coverage_finding_emissions = coverage_result["findings"]
            module_stats["coverage"] = {
                "entities": len(coverage_result["entities"]),
                "findings": len(coverage_result["findings"]),
                "tier":     scan_tier,
            }
            print(f"coverage: {len(coverage_result['entities'])} entities, "
                  f"{len(coverage_result['findings'])} findings (tier={scan_tier})")
        except Exception as e:
            print(f"coverage FAILED: {e}\n{traceback.format_exc()}")
            module_stats["coverage"] = {"error": str(e)[:200]}

        # --- Convert Shasta findings to FindingEmission, derive ARN→entity FKs
        finding_emissions = _convert_findings(
            all_shasta_findings, tenant_id, account_id, entities, edges,
        )

        # --- Single transactional write
        commit_scan(ctx, entities=entities, edges=edges,
                    findings=finding_emissions + ai_finding_emissions
                             + coverage_finding_emissions)

        total_findings = (len(finding_emissions) + len(ai_finding_emissions)
                          + len(coverage_finding_emissions))
        _update_scan(scan_id, status="completed", stats={
            "entities":      len(entities),
            "edges":         len(edges),
            "findings":      total_findings,
            "modules":       module_stats,
            "regions":       regions,
            "global_runs":   len(GLOBAL_MODULES),
            "regional_runs": len(REGIONAL_MODULES) * len(regions),
        })
        print(f"scan complete: {len(entities)} entities, {len(edges)} edges, "
              f"{total_findings} findings")
        return {
            "scan_id":          scan_id,
            "entities_written": len(entities),
            "edges_written":    len(edges),
            "findings_written": total_findings,
        }

    except Exception as e:
        err = f"{e}: {traceback.format_exc()}"[:1000]
        print(f"SCAN FAILED: {err}")
        try:
            mark_scan_failed(ctx, err)
        except Exception:
            pass  # ai_scans table not relevant for cloud scans yet
        _update_scan(scan_id, status="failed", error=err)
        raise


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


def _convert_findings(
    shasta_findings: list[Any],
    tenant_id:       str,
    account_id:      str,
    entities:        list[EntityEmission],
    edges:           list[EdgeEmission],
) -> list[FindingEmission]:
    """Turn Shasta `Finding` objects into FindingEmission, deriving the
    `subject_entity_*` FK from `resource_id` when ARN parsing succeeds.

    Side-effect: for every ARN that resolves to a kind, we ALSO append a
    (best-effort) entity row for that resource + an `aws_account → contains`
    edge. unified_writer dedupes on (tenant_id, kind, natural_key), so it's
    safe if the enum already emitted the same entity.
    """
    out: list[FindingEmission] = []
    seen_entity_keys: set[tuple[str, str]] = {(e.kind, e.natural_key) for e in entities}

    for f in shasta_findings:
        # Drop non-actionable results — not_assessed ("Unable to check …")
        # and not_applicable are noise, not findings.
        if f.status.value.lower() in ("not_assessed", "not_applicable"):
            continue
        arn = (getattr(f, "resource_id", "") or "").strip()
        subj_kind: str | None = None
        subj_nk:   str | None = None

        parsed = parse_arn(arn) if arn else None
        if parsed:
            subj_kind = parsed["kind"]
            subj_nk   = parsed["natural_key"]
            key = (subj_kind, subj_nk)
            if key not in seen_entity_keys:
                # Append a minimal entity for the finding's subject. If the
                # enum already emitted a richer row for the same key, the
                # writer's ON CONFLICT keeps the richer attributes (display
                # name from EXCLUDED, but evidence_packet/attributes only
                # overwrite when non-null/non-empty per writer SQL).
                entities.append(EntityEmission(
                    tenant_id=tenant_id,
                    kind=subj_kind,
                    natural_key=subj_nk,
                    display_name=parsed["display_name"],
                    domain="cloud",
                    attributes=parsed["attributes"],
                    evidence_packet=None,
                    detector_id=f"{_DETECTOR_ID_BASE}.finding_arn",
                    detector_version="0.1.0",
                ))
                edges.append(EdgeEmission(
                    tenant_id=tenant_id,
                    source_kind="aws_account",
                    source_natural_key=account_id,
                    target_kind=subj_kind,
                    target_natural_key=subj_nk,
                    kind="contains",
                    attributes={},
                    evidence_packet={"version": "0.1",
                                     "via": "finding.resource_id"},
                    detector_id=f"{_DETECTOR_ID_BASE}.finding_arn",
                    detector_version="0.1.0",
                ))
                seen_entity_keys.add(key)

        out.append(_shasta_to_emission(f, tenant_id, subj_kind, subj_nk))

    return out


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

def _record_scan_scope(scan_id: str, regions: list[str], discovery) -> None:
    """Write the region-discovery outcome to scans.scope so the scanned /
    skipped / errored breakdown is auditable per scan. `discovery` is a
    RegionDiscovery, or None when an explicit region override was used."""
    if discovery is None:
        scope = {"regions": regions,
                 "discovery": {"method": "explicit_override"}}
    else:
        scope = {
            "regions":         regions,
            "enabled_regions": discovery.enabled_regions,
            "skipped_empty":   discovery.skipped_empty,
            "discovery": {"method":          discovery.method,
                          "errored_regions": discovery.errored_regions},
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


def _update_scan(scan_id: str, status: str, stats: dict | None = None,
                  error: str | None = None) -> None:
    sql_parts = ["UPDATE scans SET status = :status"]
    params = [
        {"name": "sid",    "value": {"stringValue": scan_id}},
        {"name": "status", "value": {"stringValue": status}},
    ]
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
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=sql,
        parameters=params,
    )
