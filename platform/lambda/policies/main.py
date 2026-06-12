"""Policies API.

  GET    /policies                — list this tenant's policies
  GET    /policies/templates      — list available templates (static)
  POST   /policies                — create from template. Body:
                                     {template_key, vars: {company_name, effective_date, ...}}
  PATCH  /policies/{id}           — update content_md / status / vars
  GET    /policies/{id}           — fetch a single policy

Routes by httpMethod + pathParameters.
"""
from __future__ import annotations

import json
import os
import uuid

import boto3

from templates import TEMPLATES, render, list_templates
import anthropic_call

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

ALLOWED_STATUSES = {"draft", "approved", "retired"}


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    method = event.get("httpMethod", "GET")
    path = event.get("path") or ""
    path_params = event.get("pathParameters") or {}
    policy_id = path_params.get("id")

    # /policies/templates is a fixed route handled specially
    if method == "GET" and path.endswith("/templates"):
        return _resp(200, {"templates": list_templates()})
    if method == "POST" and path.endswith("/generate-all"):
        return _generate_all(tenant_id, _body(event))
    if method == "POST" and policy_id and path.endswith("/enrich"):
        return _enrich(tenant_id, policy_id)
    if method == "GET" and policy_id:
        return _get(tenant_id, policy_id)
    if method == "GET":
        return _list(tenant_id, event)
    if method == "POST":
        return _create(tenant_id, _body(event))
    if method == "PATCH" and policy_id:
        return _update(tenant_id, policy_id, _body(event))
    return _resp(400, {"error": "unsupported"})


def _generate_all(tenant_id: str, body: dict) -> dict:
    """One-click: render all templates with vars, persist as drafts, then
    AI-enrich each in parallel grounded on tenant context.

    Body: { vars: { company_name, effective_date, approver, ... } }
    """
    from concurrent.futures import ThreadPoolExecutor

    vars_in = body.get("vars") or {}
    context = _tenant_context(tenant_id)
    context_blob = json.dumps(context, indent=2)

    created = []

    def render_and_persist(template_key: str) -> tuple[str, str]:
        """Render the template + insert as a draft; return (policy_id, content_md)."""
        rendered = render(template_key, vars_in)
        pid = str(uuid.uuid4())
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=("INSERT INTO policies (policy_id, tenant_id, template_key, title, content_md, "
                 "                       soc2_controls, vars) "
                 "VALUES (CAST(:p AS UUID), CAST(:t AS UUID), :k, :ti, :body, "
                 "        CAST(:soc AS JSONB), CAST(:v AS JSONB))"),
            parameters=[
                {"name": "p",   "value": {"stringValue": pid}},
                {"name": "t",   "value": {"stringValue": tenant_id}},
                {"name": "k",   "value": {"stringValue": template_key}},
                {"name": "ti",  "value": {"stringValue": rendered["title"]}},
                {"name": "body","value": {"stringValue": rendered["content_md"]}},
                {"name": "soc", "value": {"stringValue": json.dumps(rendered["soc2_controls"])}},
                {"name": "v",   "value": {"stringValue": json.dumps(vars_in)}},
            ],
        )
        return pid, rendered["content_md"]

    # Step 1: render + insert all drafts upfront (sequential — short DB inserts).
    drafts = []
    for tkey in TEMPLATES.keys():
        try:
            pid, body_md = render_and_persist(tkey)
            drafts.append({"template_key": tkey, "policy_id": pid, "title": TEMPLATES[tkey]["title"], "draft_body": body_md})
        except Exception as e:
            print(f"WARN: render {tkey} failed: {e}")

    # Step 2: parallel-enrich each draft.
    def enrich_one(d: dict) -> dict:
        system = (
            "You are a security policy editor. Given a draft policy and the company's "
            "actual security posture, rewrite the policy to be specific to THIS company. "
            "Keep the original Markdown structure (headings, sections) and SOC 2 control "
            "references. Replace generic phrasing with concrete details that reflect the "
            "company's cloud footprint. Keep it concise. Output only the rewritten Markdown."
        )
        user = (
            f"## Company posture\n{context_blob}\n\n"
            f"## Draft policy\n{d['draft_body']}\n\n"
            f"Now rewrite the policy above, grounded in the posture data."
        )
        try:
            rewritten = anthropic_call.call(system, user, max_tokens=4096)
            rds_data.execute_statement(
                resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
                sql=("UPDATE policies SET content_md = :body, version = version + 1, updated_at = now() "
                     "WHERE policy_id = CAST(:p AS UUID)"),
                parameters=[
                    {"name": "p",    "value": {"stringValue": d["policy_id"]}},
                    {"name": "body", "value": {"stringValue": rewritten}},
                ],
            )
            return {"template_key": d["template_key"], "policy_id": d["policy_id"],
                    "title": d["title"], "enriched": True}
        except Exception as e:
            print(f"WARN: enrich {d['template_key']} failed: {e}")
            return {"template_key": d["template_key"], "policy_id": d["policy_id"],
                    "title": d["title"], "enriched": False, "error": str(e)[:200]}

    with ThreadPoolExecutor(max_workers=8) as ex:
        created = list(ex.map(enrich_one, drafts))

    return _resp(200, {"count": len(created), "policies": created})


def _enrich(tenant_id: str, policy_id: str) -> dict:
    """AI-personalize a policy doc using the tenant's actual posture context."""
    cur = _get(tenant_id, policy_id)
    if cur["statusCode"] != 200:
        return cur
    policy = json.loads(cur["body"])

    # Gather grounding context — small enough to fit in one prompt.
    context = _tenant_context(tenant_id)

    system = (
        "You are a security policy editor. Given a draft policy and the company's "
        "actual security posture, rewrite the policy to be specific to THIS company. "
        "Keep the original Markdown structure (headings, sections) and SOC 2 control "
        "references. Replace generic phrasing with concrete details that reflect the "
        "company's cloud footprint (e.g., 'AWS IAM' instead of 'cloud IAM' if they "
        "use AWS). Keep it concise — do not pad. Output only the rewritten Markdown, "
        "no preamble or commentary."
    )
    user = (
        f"## Company posture\n{json.dumps(context, indent=2)}\n\n"
        f"## Draft policy\n{policy['content_md']}\n\n"
        f"Now rewrite the policy above, grounded in the posture data."
    )

    try:
        rewritten = anthropic_call.call(system, user, max_tokens=4096)
    except Exception as e:
        return _resp(502, {"error": "anthropic_failed", "detail": str(e)[:300]})

    # Persist as a new version
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("UPDATE policies SET content_md = :body, version = version + 1, updated_at = now() "
             "WHERE policy_id = CAST(:p AS UUID) AND tenant_id = CAST(:t AS UUID)"),
        parameters=[
            {"name": "p",    "value": {"stringValue": policy_id}},
            {"name": "t",    "value": {"stringValue": tenant_id}},
            {"name": "body", "value": {"stringValue": rewritten}},
        ],
    )
    return _resp(200, {"enriched": True, "content_md": rewritten})


def _tenant_context(tenant_id: str) -> dict:
    """Light posture snapshot used as Claude grounding."""
    ctx: dict = {}

    # Connected clouds
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT cloud_type, COUNT(*) FROM cloud_connections "
             "WHERE tenant_id = CAST(:t AS UUID) AND status = 'active' GROUP BY cloud_type"),
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    ctx["clouds"] = {r[0].get("stringValue"): int(r[1].get("longValue", 0))
                    for r in rs.get("records", [])}

    # Open finding counts by severity
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT severity, COUNT(*) FROM findings WHERE tenant_id = CAST(:t AS UUID) "
             "AND status = 'fail' GROUP BY severity"),
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    ctx["open_findings_by_severity"] = {r[0].get("stringValue"): int(r[1].get("longValue", 0))
                                         for r in rs.get("records", [])}

    return ctx


def _list(tenant_id: str, event: dict) -> dict:
    qp = event.get("queryStringParameters") or {}
    status = qp.get("status")

    sql = ("SELECT policy_id::text, template_key, title, status, version, soc2_controls::text, "
           "       created_at::text, updated_at::text "
           "FROM policies WHERE tenant_id = CAST(:tid AS UUID)")
    params = [{"name": "tid", "value": {"stringValue": tenant_id}}]
    if status and status in ALLOWED_STATUSES:
        sql += " AND status = :s"
        params.append({"name": "s", "value": {"stringValue": status}})
    sql += " ORDER BY updated_at DESC"

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params,
    )

    policies = []
    for r in rs.get("records", []):
        policies.append({
            "policy_id":     r[0].get("stringValue"),
            "template_key":  r[1].get("stringValue"),
            "title":         r[2].get("stringValue"),
            "status":        r[3].get("stringValue"),
            "version":       int(r[4].get("longValue", 1)),
            "soc2_controls": json.loads(r[5].get("stringValue") or "[]"),
            "created_at":    r[6].get("stringValue"),
            "updated_at":    r[7].get("stringValue"),
        })
    return _resp(200, {"policies": policies, "count": len(policies)})


def _get(tenant_id: str, policy_id: str) -> dict:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT policy_id::text, template_key, title, status, version, content_md, "
             "       soc2_controls::text, vars::text, created_at::text, updated_at::text "
             "FROM policies WHERE policy_id = CAST(:p AS UUID) AND tenant_id = CAST(:t AS UUID)"),
        parameters=[
            {"name": "p", "value": {"stringValue": policy_id}},
            {"name": "t", "value": {"stringValue": tenant_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return _resp(404, {"error": "not_found"})
    r = rows[0]
    return _resp(200, {
        "policy_id":     r[0].get("stringValue"),
        "template_key":  r[1].get("stringValue"),
        "title":         r[2].get("stringValue"),
        "status":        r[3].get("stringValue"),
        "version":       int(r[4].get("longValue", 1)),
        "content_md":    r[5].get("stringValue"),
        "soc2_controls": json.loads(r[6].get("stringValue") or "[]"),
        "vars":          json.loads(r[7].get("stringValue") or "{}"),
        "created_at":    r[8].get("stringValue"),
        "updated_at":    r[9].get("stringValue"),
    })


def _create(tenant_id: str, body: dict) -> dict:
    template_key = body.get("template_key")
    if not template_key or template_key not in TEMPLATES:
        return _resp(400, {"error": "invalid_template_key", "allowed": list(TEMPLATES.keys())})

    source_approval_id = body.get("source_approval_id")

    vars_in = body.get("vars") or {}
    rendered = render(template_key, vars_in)

    # Allow caller to override rendered title/content (e.g. chat approval cards
    # where the AI authored the content directly rather than filling a template).
    title_override    = body.get("title")
    content_override  = body.get("content_md")

    policy_id = str(uuid.uuid4())

    params = [
        {"name": "p",   "value": {"stringValue": policy_id}},
        {"name": "t",   "value": {"stringValue": tenant_id}},
        {"name": "k",   "value": {"stringValue": template_key}},
        {"name": "ti",  "value": {"stringValue": title_override or rendered["title"]}},
        {"name": "body","value": {"stringValue": content_override or rendered["content_md"]}},
        {"name": "soc", "value": {"stringValue": json.dumps(rendered["soc2_controls"])}},
        {"name": "v",   "value": {"stringValue": json.dumps(vars_in)}},
    ]

    if source_approval_id:
        # Atomic idempotency via ON CONFLICT: the INSERT either creates the row
        # or no-ops if (tenant_id, source_approval_id) already exists. If it
        # no-ops (RETURNING is empty), fetch the winner row. This eliminates
        # the TOCTOU window that a SELECT-then-INSERT pattern has.
        params.append({"name": "said", "value": {"stringValue": source_approval_id}})

        # The unique index `idx_policies_tenant_approval` is partial
        # (WHERE source_approval_id IS NOT NULL) — Postgres requires the
        # ON CONFLICT inference clause to mirror that predicate or it errors
        # with SQLState 42P10 ("no unique constraint matches").
        rs = rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=("INSERT INTO policies (policy_id, tenant_id, template_key, title, content_md, "
                 "                      soc2_controls, vars, source_approval_id) "
                 "VALUES (CAST(:p AS UUID), CAST(:t AS UUID), :k, :ti, :body, "
                 "        CAST(:soc AS JSONB), CAST(:v AS JSONB), CAST(:said AS UUID)) "
                 "ON CONFLICT (tenant_id, source_approval_id) "
                 "WHERE source_approval_id IS NOT NULL DO NOTHING "
                 "RETURNING policy_id::text, status"),
            parameters=params,
        )
        rows = rs.get("records", [])
        if rows:
            # Fresh insert succeeded — return the new row.
            new_id = rows[0][0].get("stringValue")
            new_st = rows[0][1].get("stringValue")
            return _resp(200, {"policy_id": new_id, "status": new_st})

        # Conflict: another concurrent request already inserted this row.
        # Fetch and return the winner.
        rs2 = rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=("SELECT policy_id::text, status FROM policies "
                 "WHERE tenant_id = CAST(:t AS UUID) "
                 "AND source_approval_id = CAST(:said AS UUID) LIMIT 1"),
            parameters=[
                {"name": "t",    "value": {"stringValue": tenant_id}},
                {"name": "said", "value": {"stringValue": source_approval_id}},
            ],
        )
        existing = rs2.get("records", [])
        if existing:
            existing_id = existing[0][0].get("stringValue")
            existing_st = existing[0][1].get("stringValue")
            print(f"INFO: idempotent policy create — returning existing policy_id={existing_id}")
            return _resp(200, {"policy_id": existing_id, "status": existing_st})
        # Should never reach here, but fall through to plain insert as safety net.

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("INSERT INTO policies (policy_id, tenant_id, template_key, title, content_md, "
             "                      soc2_controls, vars) "
             "VALUES (CAST(:p AS UUID), CAST(:t AS UUID), :k, :ti, :body, "
             "        CAST(:soc AS JSONB), CAST(:v AS JSONB))"),
        parameters=params,
    )
    return _resp(200, {"policy_id": policy_id, "status": "draft"})


def _update(tenant_id: str, policy_id: str, body: dict) -> dict:
    sets = []
    params = [
        {"name": "p", "value": {"stringValue": policy_id}},
        {"name": "t", "value": {"stringValue": tenant_id}},
    ]
    if "content_md" in body:
        sets.append("content_md = :body")
        params.append({"name": "body", "value": {"stringValue": body["content_md"]}})
        sets.append("version = version + 1")
    if "status" in body:
        if body["status"] not in ALLOWED_STATUSES:
            return _resp(400, {"error": "invalid_status"})
        sets.append("status = :st")
        params.append({"name": "st", "value": {"stringValue": body["status"]}})
    if "title" in body:
        sets.append("title = :ti")
        params.append({"name": "ti", "value": {"stringValue": body["title"][:500]}})

    if not sets:
        return _resp(400, {"error": "no_fields_to_update"})

    sets.append("updated_at = now()")
    sql = (f"UPDATE policies SET {', '.join(sets)} "
           f"WHERE policy_id = CAST(:p AS UUID) AND tenant_id = CAST(:t AS UUID)")
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params,
    )
    if rs.get("numberOfRecordsUpdated", 0) == 0:
        return _resp(404, {"error": "not_found"})
    return _resp(200, {"updated": True})


def _body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


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
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
