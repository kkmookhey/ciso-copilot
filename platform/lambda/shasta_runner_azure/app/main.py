"""shasta-runner-azure — runs Shasta's Azure checks across a customer's
selected subscriptions.

Invoked as a Fargate task (via run.py) or directly as a Lambda with:
  {
    "scan_id": "uuid", "tenant_id": "uuid", "conn_id": "uuid",
    "azure_tenant_id": "<customer Entra tenant>",
    "client_id": "<SP appId>", "secret_arn": "<Secrets Manager ARN>",
    "subscription_ids": ["<sub>", ...], "scan_tier": "quick|medium|deep"
  }

Three stages (spec sections 4-5):
  1. Subscription eligibility — the selected subscription list.
  2. Footprint probe — per-subscription active/empty/unknown.
  3. Tier-aware parallel scan — subscription x Shasta-module ScanUnits
     through scanner_core.run_units; two-phase Quick early-commit.
"""
from __future__ import annotations

import json
import os
import traceback
from dataclasses import dataclass

import boto3

# === Shasta imports ===
from shasta.azure.client import AzureClient
from shasta.azure.appservice         import run_all_azure_appservice_checks
from shasta.azure.backup             import run_all_azure_backup_checks
from shasta.azure.compute            import run_all_azure_compute_checks
from shasta.azure.databases          import run_all_azure_database_checks
from shasta.azure.diagnostic_settings import run_all_azure_diagnostic_settings_checks
from shasta.azure.encryption         import run_all_azure_encryption_checks
from shasta.azure.governance         import run_all_azure_governance_checks
from shasta.azure.iam                import run_all_azure_iam_checks
from shasta.azure.monitoring         import run_all_azure_monitoring_checks
from shasta.azure.networking         import run_all_azure_networking_checks
from shasta.azure.private_endpoints  import run_all_azure_private_endpoint_checks
from shasta.azure.storage            import run_all_azure_storage_checks

# === Adapter modules (this package) ===
from ai_pass                import run_ai_pass
from azure_credential       import apply_sp_credentials
from azure_findings         import convert_azure_findings, subscription_entity
from azure_units            import modules_for_tier
from subscription_discovery import discover_subscriptions

# === Shared modules (copied in by build.sh) ===
from detectors.base import EntityEmission
from scan_pipeline  import ConcurrencyLimiter, ScanUnit, run_units
from scan_state     import record_scan_scope, update_scan
from unified_writer import commit_scan, mark_scan_failed

_SCANNER_VERSION  = "shasta_runner_azure.0.2.0"

DB_CLUSTER_ARN = os.environ.get("DB_CLUSTER_ARN", "")
DB_SECRET_ARN  = os.environ.get("DB_SECRET_ARN", "")
DB_NAME        = os.environ.get("DB_NAME", "")

sm  = boto3.client("secretsmanager")
rds = boto3.client("rds-data")

# Module name -> Shasta entry point. Each takes an AzureClient, returns
# list[Finding]. The names match azure_units' tier lists.
# "ai" is wired via _ai_unit in _build_units — not Shasta's standard
# signature (run_ai_pass returns a unified {entities, edges, findings}
# dict, not a list[Finding]) so it does not appear here.
AZURE_MODULES = {
    "iam":                 run_all_azure_iam_checks,
    "governance":          run_all_azure_governance_checks,
    "storage":             run_all_azure_storage_checks,
    "networking":          run_all_azure_networking_checks,
    "compute":             run_all_azure_compute_checks,
    "encryption":          run_all_azure_encryption_checks,
    "databases":           run_all_azure_database_checks,
    "appservice":          run_all_azure_appservice_checks,
    "monitoring":          run_all_azure_monitoring_checks,
    "backup":              run_all_azure_backup_checks,
    "diagnostic_settings": run_all_azure_diagnostic_settings_checks,
    "private_endpoints":   run_all_azure_private_endpoint_checks,
}

# Subscriptions in these states are scanned; `empty` is skipped.
_SCANNABLE = ("active", "unknown")


@dataclass(frozen=True)
class CloudScanContext:
    """Minimal ScanContext for unified_writer (reads these by attr)."""
    scan_id:         str
    tenant_id:       str
    connection_id:   str
    scanner_version: str = _SCANNER_VERSION


def handler(event: dict, context) -> dict:
    scan_id          = event["scan_id"]
    tenant_id        = event["tenant_id"]
    conn_id          = event["conn_id"]
    azure_tenant_id  = event["azure_tenant_id"]
    secret_arn       = event["secret_arn"]
    subscription_ids = event["subscription_ids"]
    scan_tier        = event.get("scan_tier", "quick")

    print(f"azure scan start: scan={scan_id} tier={scan_tier} "
          f"subs={subscription_ids}")
    ctx = CloudScanContext(scan_id=scan_id, tenant_id=tenant_id,
                           connection_id=conn_id)
    update_scan(scan_id, status="running", phase="region_discovery")

    try:
        # --- Credentials: one SP, shared by every subscription ----------
        secret = json.loads(
            sm.get_secret_value(SecretId=secret_arn)["SecretString"])
        apply_sp_credentials(secret)
        base_client = AzureClient(tenant_id=azure_tenant_id)

        # Persist subscription display names onto the connection so the
        # web subscription picker can show readable names, not GUIDs.
        # Best-effort — a name-capture failure must never fail the scan.
        try:
            names = {s["subscription_id"]: s["display_name"]
                     for s in base_client.list_subscriptions()
                     if s.get("subscription_id") and s.get("display_name")}
            _record_subscription_names(conn_id, names)
        except Exception as e:
            print(f"WARN: subscription-name capture failed: {e}")

        # --- Stage 1 + 2: subscription discovery ------------------------
        def _probe(sub_id: str) -> str:
            c = base_client.for_subscription(sub_id)
            c.validate_credentials()              # raises if unreachable
            return "active" if c.discover_services() else "empty"

        states = discover_subscriptions(subscription_ids, _probe)
        print(f"subscription discovery: {states}")
        scannable = [s for s, st in states.items() if st in _SCANNABLE]

        # --- Stage 3: build + run scan units ----------------------------
        phase1_mods, phase2_mods = modules_for_tier(scan_tier)
        limiter = ConcurrencyLimiter(default=8)
        coverage_map = {s: {"state": states[s], "modules_run": [],
                            "errors": []} for s in subscription_ids}

        entities: list[EntityEmission] = [
            subscription_entity(s, tenant_id) for s in scannable]
        edges: list = []
        findings: list = []

        phase1_units = _build_units(scannable, phase1_mods, base_client,
                                    tenant_id, azure_tenant_id)
        phase2_units = _build_units(scannable, phase2_mods, base_client,
                                    tenant_id, azure_tenant_id)

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
            "subscriptions": coverage_map,
        })
        update_scan(scan_id, status=final_status, phase="done", stats={
            "entities": len(entities), "edges": len(edges),
            "findings": len(findings), "tier": scan_tier,
            "subscriptions": scannable,
        })
        print(f"azure scan complete ({final_status}): {len(entities)} "
              f"entities, {len(edges)} edges, {len(findings)} findings")
        return {"scan_id": scan_id, "status": final_status,
                "findings_written": len(findings)}

    except Exception as e:
        err = f"{e}: {traceback.format_exc()}"[:1000]
        print(f"AZURE SCAN FAILED: {err}")
        try:
            mark_scan_failed(ctx, err)
        except Exception:
            pass
        update_scan(scan_id, status="failed", phase="done", error=err)
        raise


def _build_units(subscriptions: list[str], module_names: list[str],
                 base_client, tenant_id: str,
                 azure_tenant_id: str) -> list[ScanUnit]:
    """One ScanUnit per (subscription, module). Each unit builds its own
    AzureClient inside `run` — fresh per unit, mirroring the AWS
    scanner's per-unit client — so concurrent units never share a
    mutable Azure SDK client."""
    units: list[ScanUnit] = []
    for sub_id in subscriptions:
        for name in module_names:
            if name == "ai":
                units.append(ScanUnit(
                    name=f"{sub_id}/ai", service="ai",
                    run=_ai_unit(base_client, sub_id, tenant_id)))
                continue
            run_fn = AZURE_MODULES[name]
            units.append(ScanUnit(
                name=f"{sub_id}/{name}", service=name,
                run=_module_unit(run_fn, base_client, sub_id,
                                 tenant_id)))
    return units


def _ai_unit(base_client, sub_id: str, tenant_id: str):
    """Build the run callable for one (subscription, ai_pass) unit.

    Mirrors _module_unit's per-unit-fresh-client pattern but returns the
    unified emissions dict from run_ai_pass directly rather than going
    through convert_azure_findings — ai_pass already produces
    EntityEmission / EdgeEmission / FindingEmission instances."""
    def _run() -> dict:
        client = base_client.for_subscription(sub_id)
        client.validate_credentials()
        out = run_ai_pass(client, subscription_id=sub_id, tenant_id=tenant_id)
        return {"entities": out["entities"], "edges": out["edges"],
                "findings": out["findings"]}
    return _run


def _module_unit(run_fn, base_client, sub_id: str, tenant_id: str):
    """Build the `run` callable for one (subscription, module) unit."""
    def _run() -> dict:
        # Fresh AzureClient per unit: `for_subscription` returns an
        # unvalidated client with its own mgmt-client cache, so units
        # never share mutable Azure SDK state across the thread pool.
        # `validate_credentials` is required — `for_subscription` does
        # not call it — and is what populates the client's account_info.
        client = base_client.for_subscription(sub_id)
        client.validate_credentials()
        shasta_findings = run_fn(client)
        return convert_azure_findings(shasta_findings, tenant_id, sub_id)
    return _run


def _absorb(results, entities, edges, findings, coverage_map) -> None:
    """Merge a run_units UnitResults into the accumulators + coverage
    map. Unit name format is `<subscription_id>/<module>`."""
    entities.extend(results.entities)
    edges.extend(results.edges)
    findings.extend(results.findings)
    for o in results.outcomes:
        sub_id = o.name.split("/", 1)[0]
        bucket = coverage_map.get(sub_id)
        if bucket is None:
            continue
        if o.status == "success":
            bucket["modules_run"].append(o.name)
        else:
            bucket["errors"].append(
                f"{o.status}: {o.name} {o.detail}".strip())


def _record_subscription_names(conn_id: str, names: dict[str, str]) -> None:
    """Persist {subscription_id: display_name} into the connection's
    scope so the web subscription picker shows readable names. Additive
    — jsonb_set leaves scope.subscriptions / scope.selected untouched."""
    if not names:
        return
    rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("UPDATE cloud_connections "
             "SET scope = jsonb_set(COALESCE(scope, '{}'::jsonb), "
             "                      '{subscription_names}', CAST(:names AS JSONB)) "
             "WHERE conn_id = CAST(:cid AS UUID)"),
        parameters=[
            {"name": "cid",   "value": {"stringValue": conn_id}},
            {"name": "names", "value": {"stringValue": json.dumps(names)}},
        ],
    )
