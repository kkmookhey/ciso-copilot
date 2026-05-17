"""shasta-runner-entra — runs Shasta's Entra (Azure AD) checks against a
customer's tenant via Microsoft Graph.

Invoked with:
  {
    "scan_id":         "uuid",
    "tenant_id":       "uuid (our internal tenant)",
    "conn_id":         "uuid",
    "entra_tenant_id": "<customer's Entra tenant ID>"
  }

Loads our app's (multitenant) client_id + client_secret from Secrets
Manager ciso-copilot/entra-scanner-creds. Sets AZURE_* env vars so
DefaultAzureCredential picks them up; the resulting token is scoped to
the customer's tenant and used by Microsoft Graph.
"""
from __future__ import annotations

import json
import os
import traceback
import uuid

import boto3

DB_CLUSTER_ARN              = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN               = os.environ["DB_SECRET_ARN"]
DB_NAME                     = os.environ["DB_NAME"]
ENTRA_SCANNER_SECRET_NAME   = os.environ["ENTRA_SCANNER_SECRET_NAME"]

rds_data = boto3.client("rds-data")
sm       = boto3.client("secretsmanager")


def handler(event: dict, context) -> dict:
    scan_id         = event["scan_id"]
    tenant_id       = event["tenant_id"]
    conn_id         = event["conn_id"]
    entra_tenant_id = event["entra_tenant_id"]

    print(f"entra scan start: scan={scan_id} entra_tenant={entra_tenant_id}")
    _update_scan(scan_id, status="running")

    try:
        # Fetch our app's client credentials and inject env vars BEFORE
        # importing Shasta so DefaultAzureCredential resolves them.
        creds = json.loads(sm.get_secret_value(SecretId=ENTRA_SCANNER_SECRET_NAME)["SecretString"])
        os.environ["AZURE_CLIENT_ID"]     = creds["client_id"]
        os.environ["AZURE_TENANT_ID"]     = entra_tenant_id          # customer tenant
        os.environ["AZURE_CLIENT_SECRET"] = creds["client_secret"]

        from shasta.azure.client import AzureClient
        from shasta.azure import entra as shasta_entra

        # No subscription scope for Entra-only — skip validate_credentials
        # which expects ARM access. Graph SDK gets its credential directly
        # from the AzureClient's credential property.
        client = AzureClient(tenant_id=entra_tenant_id)

        try:
            findings = shasta_entra.run_all_azure_entra_checks(client)
        except Exception as e:
            print(f"entra checks FAILED: {e}\n{traceback.format_exc()}")
            findings = []

        written = _insert_findings(findings, scan_id, tenant_id, conn_id, entra_tenant_id)
        _update_scan(scan_id, status="completed", stats={
            "findings":        written,
            "entra_tenant_id": entra_tenant_id,
            "module":          "entra",
        })
        print(f"entra scan complete: {written} findings written")
        return {"scan_id": scan_id, "findings_written": written}

    except Exception as e:
        err = f"{e}: {traceback.format_exc()}"[:1000]
        print(f"ENTRA SCAN FAILED: {err}")
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


def _insert_findings(findings, scan_id, tenant_id, conn_id, entra_tenant_id):
    if not findings:
        return 0
    written = 0
    for i in range(0, len(findings), _BATCH_SIZE):
        batch = findings[i : i + _BATCH_SIZE]
        param_sets = [_finding_to_params(f, scan_id, tenant_id, conn_id, entra_tenant_id) for f in batch]
        rds_data.batch_execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=_FINDING_INSERT_SQL, parameterSets=param_sets,
        )
        written += len(batch)
    return written


def _finding_to_params(f, scan_id, tenant_id, conn_id, entra_tenant_id):
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
        {"name": "region",        "value": {"stringValue": (f.region or entra_tenant_id)[:50]}},
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
