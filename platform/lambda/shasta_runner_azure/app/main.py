"""shasta-runner-azure — runs Shasta's Azure checks against a customer subscription.

Invoked by onboarding_azure_complete (and later by Step Functions for scheduled
scans) with:
  {
    "scan_id":         "uuid",
    "tenant_id":       "uuid",
    "conn_id":         "uuid",
    "azure_tenant_id": "<customer Entra tenant>",
    "client_id":       "<SP appId>",
    "secret_arn":      "<Secrets Manager ARN with client_secret>",
    "subscription_id": "<scope>"
  }

Loads client_secret from Secrets Manager, sets the AZURE_* env vars that
DefaultAzureCredential picks up, then runs Shasta's azure modules.
"""
from __future__ import annotations

import json
import os
import traceback
import uuid

import boto3

from framework_map import merge_framework_map

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")
sm       = boto3.client("secretsmanager")


def handler(event: dict, context) -> dict:
    scan_id          = event["scan_id"]
    tenant_id        = event["tenant_id"]
    conn_id          = event["conn_id"]
    azure_tenant_id  = event["azure_tenant_id"]
    client_id        = event["client_id"]
    secret_arn       = event["secret_arn"]
    subscription_id  = event["subscription_id"]

    print(f"azure scan start: scan={scan_id} subscription={subscription_id}")
    _update_scan(scan_id, status="running")

    try:
        # Pull SP secret + inject env vars BEFORE importing Shasta (its
        # DefaultAzureCredential resolves these at first use).
        secret_value = json.loads(sm.get_secret_value(SecretId=secret_arn)["SecretString"])
        os.environ["AZURE_CLIENT_ID"]     = client_id
        os.environ["AZURE_TENANT_ID"]     = azure_tenant_id
        os.environ["AZURE_CLIENT_SECRET"] = secret_value["client_secret"]

        from shasta.azure.client import AzureClient
        from shasta.azure import (
            appservice            as az_appservice,
            backup                as az_backup,
            compute               as az_compute,
            databases             as az_databases,
            diagnostic_settings   as az_diag,
            encryption            as az_encryption,
            governance            as az_governance,
            iam                   as az_iam,
            monitoring            as az_monitoring,
            networking            as az_networking,
            private_endpoints     as az_pep,
            storage               as az_storage,
        )

        MODULES = [
            ("iam",                  az_iam.run_all_azure_iam_checks),
            ("governance",           az_governance.run_all_azure_governance_checks),
            ("compute",              az_compute.run_all_azure_compute_checks),
            ("storage",              az_storage.run_all_azure_storage_checks),
            ("networking",           az_networking.run_all_azure_networking_checks),
            ("databases",            az_databases.run_all_azure_database_checks),
            ("encryption",           az_encryption.run_all_azure_encryption_checks),
            ("appservice",           az_appservice.run_all_azure_appservice_checks),
            ("monitoring",           az_monitoring.run_all_azure_monitoring_checks),
            ("backup",               az_backup.run_all_azure_backup_checks),
            ("diagnostic_settings",  az_diag.run_all_azure_diagnostic_settings_checks),
            ("private_endpoints",    az_pep.run_all_azure_private_endpoint_checks),
        ]

        client = AzureClient(subscription_id=subscription_id, tenant_id=azure_tenant_id)
        client.validate_credentials()

        all_findings = []
        module_stats: dict[str, dict] = {}
        for name, run_fn in MODULES:
            try:
                findings = run_fn(client)
                all_findings.extend(findings)
                module_stats[name] = {"findings": len(findings)}
                print(f"{name}: {len(findings)} findings")
            except Exception as e:
                print(f"{name} FAILED: {e}\n{traceback.format_exc()}")
                module_stats[name] = {"error": str(e)[:200]}

        written = _insert_findings(all_findings, scan_id, tenant_id, conn_id)
        _update_scan(scan_id, status="completed", stats={
            "findings":        written,
            "modules":         module_stats,
            "subscription_id": subscription_id,
        })
        print(f"azure scan complete: {written} findings written")
        return {"scan_id": scan_id, "findings_written": written}

    except Exception as e:
        err = f"{e}: {traceback.format_exc()}"[:1000]
        print(f"AZURE SCAN FAILED: {err}")
        _update_scan(scan_id, status="failed", error=err)
        raise


# ============================================================================
# Aurora writes — copy of shasta_runner/app/main.py helpers
# ============================================================================

_FINDING_INSERT_SQL = """
INSERT INTO findings (
    finding_id, tenant_id, conn_id, scan_id, check_id, title, description,
    severity, status, resource_arn, resource_type, region, domain,
    frameworks, remediation, first_seen, last_seen
) VALUES (
    CAST(:fid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), CAST(:sid AS UUID),
    :check_id, :title, :description, :severity, :status, :resource_arn,
    :resource_type, :region, :domain,
    CAST(:frameworks AS JSONB), :remediation, now(), now()
)
"""

_BATCH_SIZE = 25


def _insert_findings(findings, scan_id, tenant_id, conn_id):
    if not findings:
        return 0
    written = 0
    for i in range(0, len(findings), _BATCH_SIZE):
        batch = findings[i : i + _BATCH_SIZE]
        param_sets = [_finding_to_params(f, scan_id, tenant_id, conn_id) for f in batch]
        rds_data.batch_execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=_FINDING_INSERT_SQL, parameterSets=param_sets,
        )
        written += len(batch)
    return written


def _finding_to_params(f, scan_id, tenant_id, conn_id):
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
    return [
        {"name": "fid",           "value": {"stringValue": str(uuid.uuid4())}},
        {"name": "tid",           "value": {"stringValue": tenant_id}},
        {"name": "cid",           "value": {"stringValue": conn_id}},
        {"name": "sid",           "value": {"stringValue": scan_id}},
        {"name": "check_id",      "value": {"stringValue": f.check_id}},
        {"name": "title",         "value": {"stringValue": f.title[:500]}},
        {"name": "description",   "value": {"stringValue": (f.description or "")[:2000]}},
        {"name": "severity",      "value": {"stringValue": f.severity.value.lower()}},
        {"name": "status",        "value": {"stringValue": f.status.value.lower()}},
        {"name": "resource_arn",  "value": {"stringValue": (f.resource_id or "")[:500]}},
        {"name": "resource_type", "value": {"stringValue": f.resource_type[:200]}},
        {"name": "region",        "value": {"stringValue": f.region[:50]}},
        {"name": "domain",        "value": {"stringValue": f.domain.value.lower()}},
        {"name": "frameworks",    "value": {"stringValue": json.dumps(frameworks)}},
        {"name": "remediation",   "value": {"stringValue": (f.remediation or "")[:2000]}},
    ]


def _update_scan(scan_id, status, stats=None, error=None):
    parts = ["UPDATE scans SET status = :status"]
    params = [
        {"name": "sid",    "value": {"stringValue": scan_id}},
        {"name": "status", "value": {"stringValue": status}},
    ]
    if status in ("completed", "failed", "partial"):
        parts.append("finished_at = now()")
    if stats is not None:
        parts.append("stats = CAST(:stats AS JSONB)")
        params.append({"name": "stats", "value": {"stringValue": json.dumps(stats)}})
    if error is not None:
        parts.append("error = :error")
        params.append({"name": "error", "value": {"stringValue": error}})
    sql = ", ".join(parts) + " WHERE scan_id = CAST(:sid AS UUID)"
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params,
    )
