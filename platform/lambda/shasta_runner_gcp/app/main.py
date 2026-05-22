"""shasta-runner-gcp — the v2 three-stage GCP scanner.

Invoked as a Fargate task (via run.py) or directly as a Lambda with:
  {
    "scan_id": "uuid", "tenant_id": "uuid", "conn_id": "uuid",
    "project_ids": ["<gcp project>", ...],
    "wif_project_number": "<project hosting the WIF pool>",
    "sa_email": "<reader SA email>",
    "wif_pool": "<pool id>", "wif_provider": "<provider id>",
    "scan_tier": "quick|medium|deep"
  }

Three stages (spec sections 5.3):
  1. Project eligibility — the project_ids list.
  2. Footprint probe — per-project active/empty/unknown.
  3. Tier-aware parallel scan — project x Shasta-module ScanUnits
     through scanner_core.run_units; two-phase Quick early-commit.

Credentials: a WIF external_account credential lets google-auth exchange
the Fargate task's AWS role for an impersonated GCP reader SA. No private
key on disk anywhere.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass

import boto3

# === Shasta imports ===
from shasta.gcp.client import GCPClient
from shasta.gcp import (
    compute        as gcp_compute,
    storage        as gcp_storage,
    networking     as gcp_networking,
    iam            as gcp_iam,
    encryption     as gcp_encryption,
    logging_checks as gcp_logging,
    cloud_run      as gcp_cloud_run,
)

# === Adapter modules (this package) ===
from gcp_credential    import (build_external_account_info,
                               export_aws_credentials_to_env)
from gcp_findings      import convert_gcp_findings, project_entity
from gcp_units         import modules_for_tier
from project_discovery import discover_projects

# === Shared modules (copied in by build.sh) ===
from detectors.base import EntityEmission
from scan_pipeline  import ConcurrencyLimiter, ScanUnit, run_units
from scan_state     import record_scan_scope, update_scan
from unified_writer import commit_scan, mark_scan_failed

_SCANNER_VERSION = "shasta_runner_gcp.0.2.0"

# Module name -> Shasta entry point. Each takes a GCPClient, returns
# list[Finding]. The names match gcp_units' tier lists.
GCP_MODULES = {
    "iam":        gcp_iam.run_all_gcp_iam_checks,
    "storage":    gcp_storage.run_all_gcp_storage_checks,
    "networking": gcp_networking.run_all_gcp_networking_checks,
    "encryption": gcp_encryption.run_all_gcp_encryption_checks,
    "compute":    gcp_compute.run_all_gcp_compute_checks,
    "logging":    gcp_logging.run_all_gcp_logging_checks,
    "cloud_run":  gcp_cloud_run.run_all_gcp_cloud_run_checks,
}

# Projects in these states are scanned; `empty` is skipped.
_SCANNABLE = ("active", "unknown")


@dataclass(frozen=True)
class CloudScanContext:
    """Minimal ScanContext for unified_writer (reads these by attr)."""
    scan_id:         str
    tenant_id:       str
    connection_id:   str
    scanner_version: str = _SCANNER_VERSION


def handler(event: dict, context) -> dict:
    scan_id            = event["scan_id"]
    tenant_id          = event["tenant_id"]
    conn_id            = event["conn_id"]
    project_ids        = event["project_ids"]
    wif_project_number = event["wif_project_number"]
    sa_email           = event["sa_email"]
    wif_pool           = event["wif_pool"]
    wif_provider       = event["wif_provider"]
    scan_tier          = event.get("scan_tier", "quick")

    print(f"gcp scan start: scan={scan_id} tier={scan_tier} "
          f"projects={project_ids}")
    ctx = CloudScanContext(scan_id=scan_id, tenant_id=tenant_id,
                           connection_id=conn_id)
    update_scan(scan_id, status="running", phase="region_discovery")

    try:
        # --- Credentials: one WIF credential, shared by every project ---
        # google-auth's AWS external-account credential source reads AWS
        # creds from env vars or EC2 IMDS — neither is populated for an
        # ECS Fargate task role (Fargate serves them via the container
        # credentials endpoint). Resolve them with boto3, which supports
        # the container provider, and export them so google-auth can sign
        # the GetCallerIdentity subject token.
        aws_creds = boto3.Session().get_credentials()
        if aws_creds is None:
            raise RuntimeError(
                "no AWS credentials available to sign the WIF subject token")
        export_aws_credentials_to_env(aws_creds.get_frozen_credentials())

        from google.auth import aws as google_aws
        info = build_external_account_info(
            wif_project_number, sa_email, wif_pool, wif_provider)
        credentials = google_aws.Credentials.from_info(info)

        # A base client bound to the first project — used only to mint
        # per-project sibling clients via for_project().
        base_client = GCPClient(project_id=project_ids[0],
                                credentials=credentials)

        # --- Stage 1 + 2: project discovery ----------------------------
        def _probe(project_id: str) -> str:
            c = base_client.for_project(project_id)
            c.validate_credentials()             # raises if unreachable
            return "active" if c.discover_services() else "empty"

        states = discover_projects(project_ids, _probe)
        print(f"project discovery: {states}")
        scannable = [p for p, st in states.items() if st in _SCANNABLE]

        # --- Stage 3: build + run scan units ---------------------------
        phase1_mods, phase2_mods = modules_for_tier(scan_tier)
        limiter = ConcurrencyLimiter(default=8)
        coverage_map = {p: {"state": states[p], "modules_run": [],
                            "errors": []} for p in project_ids}

        entities: list[EntityEmission] = [
            project_entity(p, tenant_id) for p in scannable]
        edges: list = []
        findings: list = []

        phase1_units = _build_units(scannable, phase1_mods, base_client,
                                    tenant_id)
        phase2_units = _build_units(scannable, phase2_mods, base_client,
                                    tenant_id)

        if scan_tier.lower() == "quick":
            update_scan(scan_id, status="running", phase="first_signal")
            r1 = run_units(phase1_units, limiter=limiter)
            _absorb(r1, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))
            print(f"quick phase 1 committed: {len(findings)} findings")
            update_scan(scan_id, status="running", phase="crown_jewel")
            r2 = run_units(phase2_units, limiter=limiter)
            _absorb(r2, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))
        else:
            update_scan(scan_id, status="running", phase="full")
            res = run_units(phase1_units + phase2_units, limiter=limiter)
            _absorb(res, entities, edges, findings, coverage_map)
            commit_scan(ctx, entities=list(entities), edges=list(edges),
                        findings=list(findings))

        had_gap = any(c["errors"] for c in coverage_map.values())
        final_status = "partial" if had_gap else "completed"
        record_scan_scope(scan_id, {
            "tier": scan_tier,
            "projects": coverage_map,
        })
        update_scan(scan_id, status=final_status, phase="done", stats={
            "entities": len(entities), "edges": len(edges),
            "findings": len(findings), "tier": scan_tier,
            "projects": scannable,
        })
        print(f"gcp scan complete ({final_status}): {len(entities)} "
              f"entities, {len(edges)} edges, {len(findings)} findings")
        return {"scan_id": scan_id, "status": final_status,
                "findings_written": len(findings)}

    except Exception as e:
        err = f"{e}: {traceback.format_exc()}"[:1000]
        print(f"GCP SCAN FAILED: {err}")
        try:
            mark_scan_failed(ctx, err)
        except Exception:
            pass
        update_scan(scan_id, status="failed", phase="done", error=err)
        raise


def _build_units(projects: list[str], module_names: list[str],
                 base_client, tenant_id: str) -> list[ScanUnit]:
    """One ScanUnit per (project, module). Each unit builds its own
    GCPClient inside `run` — fresh per unit via for_project — so
    concurrent units never share a mutable Shasta GCP client."""
    units: list[ScanUnit] = []
    for project_id in projects:
        for name in module_names:
            run_fn = GCP_MODULES[name]
            units.append(ScanUnit(
                name=f"{project_id}/{name}", service=name,
                run=_module_unit(run_fn, base_client, project_id,
                                 tenant_id)))
    return units


def _module_unit(run_fn, base_client, project_id: str, tenant_id: str):
    """Build the `run` callable for one (project, module) unit."""
    def _run() -> dict:
        # Fresh GCPClient per unit: for_project returns a sibling with
        # its own service-client cache, so units never share mutable
        # Google SDK state across the thread pool. validate_credentials
        # populates the client's account_info and is required before
        # the Shasta module runs.
        client = base_client.for_project(project_id)
        client.validate_credentials()
        shasta_findings = run_fn(client)
        return convert_gcp_findings(shasta_findings, tenant_id, project_id)
    return _run


def _absorb(results, entities, edges, findings, coverage_map) -> None:
    """Merge a run_units UnitResults into the accumulators + coverage
    map. Unit name format is `<project_id>/<module>`."""
    entities.extend(results.entities)
    edges.extend(results.edges)
    findings.extend(results.findings)
    for o in results.outcomes:
        project_id = o.name.split("/", 1)[0]
        bucket = coverage_map.get(project_id)
        if bucket is None:
            continue
        if o.status == "success":
            bucket["modules_run"].append(o.name)
        else:
            bucket["errors"].append(
                f"{o.status}: {o.name} {o.detail}".strip())
