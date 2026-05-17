"""shasta-runner-gcp — runs Shasta's GCP checks against a customer project
via Workload Identity Federation.

Invoked with:
  {
    "scan_id":        "uuid",
    "tenant_id":      "uuid (our internal tenant)",
    "conn_id":        "uuid",
    "project_id":     "<customer gcp project>",
    "project_number": "<customer gcp project number>",
    "sa_email":       "ciso-copilot-reader@<project>.iam.gserviceaccount.com",
    "wif_pool":       "ciso-copilot-pool",
    "wif_provider":   "ciso-copilot-aws-provider"
  }

Builds an external_account credential pointing the customer's WIF provider
at our Lambda's AWS role. google-auth's aws.Credentials class handles the
exchange: AWS GetCallerIdentity (our role) → Google STS token → impersonate
the customer's SA → access GCP APIs.
"""
from __future__ import annotations

import json
import os
import traceback
import uuid

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")


def handler(event: dict, context) -> dict:
    scan_id         = event["scan_id"]
    tenant_id       = event["tenant_id"]
    conn_id         = event["conn_id"]
    project_id      = event["project_id"]
    project_number  = event["project_number"]
    sa_email        = event["sa_email"]
    wif_pool        = event["wif_pool"]
    wif_provider    = event["wif_provider"]

    print(f"gcp scan start: scan={scan_id} project={project_id}")
    _update_scan(scan_id, status="running")

    try:
        from google.auth import aws as google_aws
        from shasta.gcp.client import GCPClient
        from shasta.gcp import (
            compute         as gcp_compute,
            storage         as gcp_storage,
            networking      as gcp_networking,
            iam             as gcp_iam,
            encryption      as gcp_encryption,
            logging_checks  as gcp_logging,
            cloud_run       as gcp_cloud_run,
        )

        audience = (
            f"//iam.googleapis.com/projects/{project_number}"
            f"/locations/global/workloadIdentityPools/{wif_pool}"
            f"/providers/{wif_provider}"
        )
        impersonation_url = (
            f"https://iamcredentials.googleapis.com/v1/projects/-"
            f"/serviceAccounts/{sa_email}:generateAccessToken"
        )

        # WIF external_account spec. Lambda inherits AWS credentials from its
        # IAM role at runtime — google-auth's aws module picks them up via
        # boto3's default chain when assembling the GetCallerIdentity subject
        # token. No private key on disk anywhere.
        credentials = google_aws.Credentials.from_info({
            "type":                              "external_account",
            "audience":                          audience,
            "subject_token_type":                "urn:ietf:params:aws:token-type:aws4_request",
            "service_account_impersonation_url": impersonation_url,
            "token_url":                         "https://sts.googleapis.com/v1/token",
            "credential_source": {
                "environment_id":                  "aws1",
                "regional_cred_verification_url":  "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15",
            },
        })

        client = GCPClient(project_id=project_id, credentials=credentials)

        MODULES = [
            ("iam",        gcp_iam.run_all_gcp_iam_checks),
            ("compute",    gcp_compute.run_all_gcp_compute_checks),
            ("storage",    gcp_storage.run_all_gcp_storage_checks),
            ("networking", gcp_networking.run_all_gcp_networking_checks),
            ("encryption", gcp_encryption.run_all_gcp_encryption_checks),
            ("logging",    gcp_logging.run_all_gcp_logging_checks),
            ("cloud_run",  gcp_cloud_run.run_all_gcp_cloud_run_checks),
        ]

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

        written = _insert_findings(all_findings, scan_id, tenant_id, conn_id, project_id)
        _update_scan(scan_id, status="completed", stats={
            "findings":   written,
            "modules":    module_stats,
            "project_id": project_id,
        })
        print(f"gcp scan complete: {written} findings written")
        return {"scan_id": scan_id, "findings_written": written}

    except Exception as e:
        err = f"{e}: {traceback.format_exc()}"[:1000]
        print(f"GCP SCAN FAILED: {err}")
        _update_scan(scan_id, status="failed", error=err)
        raise


# ============================================================================
# Aurora writes
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


def _insert_findings(findings, scan_id, tenant_id, conn_id, project_id):
    if not findings:
        return 0
    written = 0
    for i in range(0, len(findings), _BATCH_SIZE):
        batch = findings[i : i + _BATCH_SIZE]
        param_sets = [_finding_to_params(f, scan_id, tenant_id, conn_id, project_id) for f in batch]
        rds_data.batch_execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=_FINDING_INSERT_SQL, parameterSets=param_sets,
        )
        written += len(batch)
    return written


def _finding_to_params(f, scan_id, tenant_id, conn_id, project_id):
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
        {"name": "region",        "value": {"stringValue": (f.region or project_id)[:50]}},
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
