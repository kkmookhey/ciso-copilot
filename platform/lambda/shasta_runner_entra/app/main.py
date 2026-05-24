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

from ai_signin_pass import run_ai_signin_pass
from framework_map import merge_framework_map

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

        # AI sign-in pass: pages Entra sign-in audit logs, matches against the
        # curated AI-SaaS catalog, emits finding-param dicts directly (no
        # Shasta Finding intermediate). Constructs its own Graph client from
        # the AZURE_* env vars set above. Wrapped so a Graph failure here
        # never fails the Shasta entra scan.
        # AI sign-in pass — returns (param_lists, premium_required) per S2.1.
        try:
            ai_signin_params, ai_signin_premium_required = run_ai_signin_pass(
                graph_client=None,
                tenant_id=tenant_id,
                conn_id=conn_id,
                scan_id=scan_id,
                entra_tenant_id=entra_tenant_id,
            )
        except Exception as e:
            print(f"ai_signin_pass FAILED: {e}\n{traceback.format_exc()}")
            ai_signin_params = []
            ai_signin_premium_required = False

        if ai_signin_params:
            written += _insert_finding_param_lists(ai_signin_params)

        # S2.1: write/clear the licensing banner flag on the connection.
        try:
            _update_connection_premium_flag(
                conn_id,
                premium_required=ai_signin_premium_required,
                signin_count=len(ai_signin_params),
            )
        except Exception as e:
            print(f"WARN: failed to update signin_premium_required flag: {e}")

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
    frameworks, remediation, evidence_packet, first_seen, last_seen
) VALUES (
    CAST(:fid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), CAST(:sid AS UUID),
    :check_id, :title, :description, :severity, :status, :resource_arn,
    :resource_type, :region, :domain,
    CAST(:frameworks AS JSONB), :remediation, CAST(:evidence_packet AS JSONB),
    now(), now()
)
ON CONFLICT (tenant_id, conn_id, check_id, COALESCE(resource_arn, ''::text), COALESCE(region, ''::text))
DO UPDATE SET
    scan_id         = EXCLUDED.scan_id,
    title           = EXCLUDED.title,
    description     = EXCLUDED.description,
    severity        = EXCLUDED.severity,
    status          = EXCLUDED.status,
    resource_type   = EXCLUDED.resource_type,
    domain          = EXCLUDED.domain,
    frameworks      = EXCLUDED.frameworks,
    remediation     = EXCLUDED.remediation,
    evidence_packet = EXCLUDED.evidence_packet,
    last_seen       = now()
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


def _insert_finding_param_lists(param_lists: list[list[dict]]) -> int:
    """Insert findings whose params are already built (AI sign-in pass).

    Mirrors _insert_findings' batching pattern but skips the
    Finding-object-to-params conversion since the caller did it.
    """
    if not param_lists:
        return 0
    written = 0
    for i in range(0, len(param_lists), _BATCH_SIZE):
        batch = param_lists[i : i + _BATCH_SIZE]
        rds_data.batch_execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=_FINDING_INSERT_SQL, parameterSets=batch,
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
        {"name": "region",        "value": {"stringValue": (f.region or entra_tenant_id)[:50]}},
        {"name": "domain",        "value": {"stringValue": f.domain.value.lower()}},
        {"name": "frameworks",    "value": {"stringValue": json.dumps(frameworks)}},
        {"name": "remediation",   "value": {"stringValue": (f.remediation or "")[:2000]}},
        {"name": "evidence_packet", "value": {"stringValue": "{}"}},
    ]


def _update_connection_premium_flag(conn_id: str, *,
                                    premium_required: bool,
                                    signin_count: int) -> None:
    """S2.1: sticky-flag the connection when Graph returned the
    licensing-403, or clear it when a future scan emitted real
    sign-in findings (positive evidence the licensing constraint
    is gone).

    Ambiguous case (no 403 AND no findings) is a no-op — could be
    a Premium tenant with no AI-app users yet, or a transient Graph
    issue. We don't want to clear a sticky flag without positive
    evidence.
    """
    if premium_required:
        sql = (
            "UPDATE cloud_connections "
            "SET scope = jsonb_set(COALESCE(scope, '{}'::jsonb), "
            "                      '{signin_premium_required}', 'true'::jsonb), "
            "    updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        )
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=sql,
            parameters=[{"name": "cid", "value": {"stringValue": conn_id}}],
        )
    elif signin_count > 0:
        sql = (
            "UPDATE cloud_connections "
            "SET scope = scope #- '{signin_premium_required}', "
            "    updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        )
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=sql,
            parameters=[{"name": "cid", "value": {"stringValue": conn_id}}],
        )
    # else: ambiguous case, no write


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
