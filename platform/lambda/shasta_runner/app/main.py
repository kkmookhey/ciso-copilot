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
  3. Run Shasta's global modules (IAM, Organizations) once.
  4. For each region: run regional modules (compute, storage, networking,
     databases, encryption, KMS, CloudWatch logs, backup, VPC endpoints,
     CloudFront, serverless, vulnerabilities, data warehouse).
  5. Batch-insert findings into Aurora via rds-data Data API.
  6. UPDATE scans SET status='completed' WITH stats.

Per-module failures are caught and logged — one bad module doesn't kill the
whole scan. The scan completes with whichever findings it could produce.
"""
from __future__ import annotations

import json
import os
import traceback
import uuid
from typing import Any

import boto3

# === Shasta imports ===
from shasta.aws.client import AWSClient, AWSAccountInfo
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

# === Config ===
DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

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


class AssumedRoleAWSClient(AWSClient):
    """Shasta AWSClient that uses pre-fetched assumed-role credentials.

    Shasta's stock AWSClient relies on the default boto3 credential chain
    (profile / env / instance profile). We override `_create_session` to
    inject the credentials returned by STS:AssumeRole, and pre-populate
    `account_info` so per-check sts:GetCallerIdentity calls aren't needed.
    """

    def __init__(self, credentials: dict[str, str], region: str, account_id: str):
        super().__init__(region=region)
        self._credentials = credentials
        self._account_info = AWSAccountInfo(
            account_id=account_id,
            account_aliases=[],
            user_arn=f"arn:aws:iam::{account_id}:assumed-role/CISOCopilotReader/CISOCopilotScan",
            user_id="AROAEXAMPLE:CISOCopilotScan",
            region=region,
            services_in_use=[],
        )

    def _create_session(self) -> boto3.Session:
        return boto3.Session(
            aws_access_key_id     = self._credentials["AccessKeyId"],
            aws_secret_access_key = self._credentials["SecretAccessKey"],
            aws_session_token     = self._credentials["SessionToken"],
            region_name           = self._region,
        )

    @property
    def account_info(self) -> AWSAccountInfo:
        return self._account_info


def handler(event: dict, context) -> dict:
    scan_id     = event["scan_id"]
    tenant_id   = event["tenant_id"]
    conn_id     = event["conn_id"]
    role_arn    = event["role_arn"]
    external_id = event["external_id"]
    account_id  = event["account_id"]
    regions     = event.get("regions") or ["us-east-1"]

    print(f"scan start: scan={scan_id} account={account_id} regions={regions}")
    _update_scan(scan_id, status="running")

    try:
        credentials = _assume_role(role_arn, external_id)
        all_findings: list[Any] = []
        module_stats: dict[str, dict[str, int]] = {}

        # Global modules — one pass each (region 'us-east-1' for the session)
        for name, run_fn in GLOBAL_MODULES:
            try:
                client = AssumedRoleAWSClient(credentials, "us-east-1", account_id)
                findings = run_fn(client)
                all_findings.extend(findings)
                module_stats[name] = {"findings": len(findings)}
                print(f"global/{name}: {len(findings)} findings")
            except Exception as e:
                print(f"global/{name} FAILED: {e}\n{traceback.format_exc()}")
                module_stats[name] = {"error": str(e)[:200]}

        # Per-region modules
        for region in regions:
            for name, run_fn in REGIONAL_MODULES:
                key = f"{region}/{name}"
                try:
                    client = AssumedRoleAWSClient(credentials, region, account_id)
                    findings = run_fn(client)
                    all_findings.extend(findings)
                    module_stats[key] = {"findings": len(findings)}
                    print(f"{key}: {len(findings)} findings")
                except Exception as e:
                    print(f"{key} FAILED: {e}\n{traceback.format_exc()}")
                    module_stats[key] = {"error": str(e)[:200]}

        # Batch-insert
        written = _insert_findings(all_findings, scan_id, tenant_id, conn_id)

        _update_scan(scan_id, status="completed", stats={
            "findings":      written,
            "modules":       module_stats,
            "regions":       regions,
            "global_runs":   len(GLOBAL_MODULES),
            "regional_runs": len(REGIONAL_MODULES) * len(regions),
        })
        print(f"scan complete: {written} findings written")
        return {"scan_id": scan_id, "findings_written": written}

    except Exception as e:
        err = f"{e}: {traceback.format_exc()}"[:1000]
        print(f"SCAN FAILED: {err}")
        _update_scan(scan_id, status="failed", error=err)
        raise


# ============================================================================
# STS assume role
# ============================================================================

def _assume_role(role_arn: str, external_id: str) -> dict[str, str]:
    resp = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName="CISOCopilotScan",
        ExternalId=external_id,
        DurationSeconds=3600,
    )
    return resp["Credentials"]


# ============================================================================
# Aurora writes (Data API)
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

_BATCH_SIZE = 25  # Data API batch-execute limit


def _insert_findings(findings: list[Any], scan_id: str, tenant_id: str, conn_id: str) -> int:
    if not findings:
        return 0

    written = 0
    for i in range(0, len(findings), _BATCH_SIZE):
        batch = findings[i : i + _BATCH_SIZE]
        param_sets = [_finding_to_params(f, scan_id, tenant_id, conn_id) for f in batch]
        rds_data.batch_execute_statement(
            resourceArn=DB_CLUSTER_ARN,
            secretArn=DB_SECRET_ARN,
            database=DB_NAME,
            sql=_FINDING_INSERT_SQL,
            parameterSets=param_sets,
        )
        written += len(batch)
    return written


def _finding_to_params(f, scan_id: str, tenant_id: str, conn_id: str) -> list[dict]:
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


def _update_scan(scan_id: str, status: str, stats: dict | None = None, error: str | None = None) -> None:
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
