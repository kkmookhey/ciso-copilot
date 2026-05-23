"""GET /ai/summary — AI-touching findings aggregated for the /ai view.

A finding is AI-touching iff:
  - Any key in findings.frameworks starts with one of the AI-framework
    prefixes (nist_ai_rmf, iso_42001, soc2_ai, eu_ai_act), OR
  - The associated entity carries an AI domain/kind (joined via
    findings.subject_entity_id -> entities.id), OR
  - The finding's evidence_packet JSONB has is_ai=true.

This module evaluates the predicate inside SQL — it is faster than
pulling rows into Python and lets the DB index the JSONB lookups.

Response shape:
  {
    "score":        {"fail": int, "partial": int, "pass": int},
    "by_source":    {"aws": int, "azure": int, "code": int, "entra": int},
    "by_framework": {<fw>: {"fail": int, "partial": int, "pass": int}},
    "top_people":   [{"email": str, "fail": int, "partial": int, "sources": [str]}]
  }

Schema notes (verified against the live Aurora schema 2026-05-22):
  - entities.id is the PK (not entity_id) — join uses e.id.
  - findings has no `attributes` column; per-finding metadata lives in
    `evidence_packet` (JSONB). is_ai / commit_author_email /
    iam_owner_email / entra_upn keys live there once the writers populate
    them (Task 7).
"""
from __future__ import annotations

import json
import os

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

_AI_FRAMEWORKS = ("nist_ai_rmf", "iso_42001", "soc2_ai", "eu_ai_act")

# Kinds the per-person view treats as AI-touching when joining entities.
# Keeping this list in code (not the DB) so deploys carry the truth.
_AI_RESOURCE_KINDS = (
    "bedrock_model", "bedrock_guardrail", "sagemaker_endpoint",
    "sagemaker_model", "sagemaker_training_job", "comprehend_endpoint",
    "lambda_ai_function",
    "azure_openai_deployment", "azure_ml_workspace", "cognitive_service",
    "vertex_endpoint",  # reserved for GCP-AI sub-project
    "ai_saas_app", "ai_code_finding",
    "ai_user_signin", "ai_api_key", "ai_org_member", "ai_project",
    "ai_provider_org",
)

# Shared SQL fragment: a finding is AI-touching.
# Uses ?| (jsonb has any of) for framework keys. Entity joins are LEFT
# JOIN because some findings have no subject entity.
_IS_AI_TOUCHING = """
  (
    f.frameworks ?| ARRAY[{fws}]
    OR e.domain = 'ai'
    OR e.kind = ANY(ARRAY[{kinds}])
    OR (f.evidence_packet ->> 'is_ai') = 'true'
  )
""".format(
    fws   = ", ".join(f"'{k}'" for k in _AI_FRAMEWORKS),
    kinds = ", ".join(f"'{k}'" for k in _AI_RESOURCE_KINDS),
)


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    score        = _query_score(tenant_id)
    by_source    = _query_by_source(tenant_id)
    by_framework = _query_by_framework(tenant_id)
    top_people   = _query_top_people(tenant_id)

    return _resp(200, {
        "score":        score,
        "by_source":    by_source,
        "by_framework": by_framework,
        "top_people":   top_people,
    })


def _query_score(tenant_id: str) -> dict:
    sql = f"""
        SELECT f.status, COUNT(*)
        FROM findings f
        LEFT JOIN entities e
          ON e.id = f.subject_entity_id
        WHERE f.tenant_id = CAST(:tid AS UUID)
          AND { _IS_AI_TOUCHING }
        GROUP BY f.status
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    counts = {
        r[0].get("stringValue"): int(r[1].get("longValue", 0))
        for r in rs.get("records", [])
    }
    return {
        "fail":    counts.get("fail", 0),
        "partial": counts.get("partial", 0),
        "pass":    counts.get("pass", 0),
    }


def _query_by_source(tenant_id: str) -> dict:
    """Source = cloud_connections.cloud_type for cloud findings; 'code'
    for findings with no cloud connection (the AI-code scanner)."""
    sql = f"""
        SELECT COALESCE(c.cloud_type, 'code') AS source, COUNT(*)
        FROM findings f
        LEFT JOIN entities e ON e.id = f.subject_entity_id
        LEFT JOIN cloud_connections c ON c.conn_id = f.conn_id
        WHERE f.tenant_id = CAST(:tid AS UUID)
          AND f.status IN ('fail', 'partial')
          AND { _IS_AI_TOUCHING }
        GROUP BY 1
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    raw = {
        r[0].get("stringValue"): int(r[1].get("longValue", 0))
        for r in rs.get("records", [])
    }
    return {
        "aws":   raw.get("aws", 0),
        "azure": raw.get("azure", 0),
        "code":  raw.get("code", 0),
        "entra": raw.get("entra", 0),
    }


def _query_by_framework(tenant_id: str) -> dict:
    """Counts per AI framework per status. Cross-joins jsonb_object_keys
    so a finding tagged with multiple AI frameworks is counted once per
    framework."""
    fws_list = ", ".join(f"'{k}'" for k in _AI_FRAMEWORKS)
    sql = f"""
        SELECT k AS fw, f.status, COUNT(*)
        FROM findings f
        LEFT JOIN entities e ON e.id = f.subject_entity_id
        CROSS JOIN LATERAL jsonb_object_keys(f.frameworks) AS k
        WHERE f.tenant_id = CAST(:tid AS UUID)
          AND k IN ({fws_list})
          AND { _IS_AI_TOUCHING }
        GROUP BY k, f.status
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    out: dict[str, dict[str, int]] = {
        fw: {"fail": 0, "partial": 0, "pass": 0} for fw in _AI_FRAMEWORKS
    }
    for r in rs.get("records", []):
        fw  = r[0].get("stringValue")
        st  = r[1].get("stringValue")
        n   = int(r[2].get("longValue", 0))
        if fw in out and st in out[fw]:
            out[fw][st] = n
    return out


def _query_top_people(tenant_id: str) -> list:
    """Per-person ranking — emails sourced from
        - findings.evidence_packet->>'commit_author_email' (AI code findings)
        - findings.evidence_packet->>'iam_owner_email'     (AWS IAM-tagged AI resources)
        - findings.evidence_packet->>'entra_upn'           (Entra signin findings - S2)
    Top 25 by (fail desc, partial desc).

    Note: until Task 7 wires these keys into the writers, this query
    returns an empty list — which is the correct empty state for the UI.
    """
    sql = f"""
        SELECT
          LOWER(
            COALESCE(
              f.evidence_packet->>'commit_author_email',
              f.evidence_packet->>'iam_owner_email',
              f.evidence_packet->>'entra_upn'
            )
          ) AS person,
          COUNT(*) FILTER (WHERE f.status = 'fail')    AS fail_n,
          COUNT(*) FILTER (WHERE f.status = 'partial') AS partial_n,
          STRING_AGG(DISTINCT COALESCE(c.cloud_type, 'code'), ',') AS sources
        FROM findings f
        LEFT JOIN entities e ON e.id = f.subject_entity_id
        LEFT JOIN cloud_connections c ON c.conn_id = f.conn_id
        WHERE f.tenant_id = CAST(:tid AS UUID)
          AND COALESCE(
                f.evidence_packet->>'commit_author_email',
                f.evidence_packet->>'iam_owner_email',
                f.evidence_packet->>'entra_upn'
              ) IS NOT NULL
          AND f.status IN ('fail', 'partial')
          AND { _IS_AI_TOUCHING }
        GROUP BY 1
        ORDER BY fail_n DESC, partial_n DESC
        LIMIT 25
    """
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql,
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    out = []
    for r in rs.get("records", []):
        out.append({
            "email":   r[0].get("stringValue"),
            "fail":    int(r[1].get("longValue", 0)),
            "partial": int(r[2].get("longValue", 0)),
            "sources": (r[3].get("stringValue") or "").split(",") if r[3].get("stringValue") else [],
        })
    return out


def _resolve_tenant_id(event: dict) -> str | None:
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    raw = claims.get("identities")
    sso_subject = None
    if raw:
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(ids, dict):
                ids = [ids]
            if ids:
                sso_subject = ids[0].get("userId") or claims.get("sub")
        except (TypeError, ValueError):
            pass
    sso_subject = sso_subject or claims.get("sub")
    if not sso_subject:
        return None
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql="SELECT tenant_id::text FROM users WHERE sso_subject = :s LIMIT 1",
        parameters=[{"name": "s", "value": {"stringValue": sso_subject}}],
    )
    rows = rs.get("records", [])
    return rows[0][0].get("stringValue") if rows else None


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json",
                       "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
