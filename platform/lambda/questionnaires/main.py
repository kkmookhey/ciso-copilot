"""Questionnaires API.

  GET   /questionnaires                    — list this tenant's questionnaires
  GET   /questionnaires/templates          — list available banks (SIG Lite, CAIQ Lite, ...)
  POST  /questionnaires                    — create from bank; auto-fills answers from findings
  GET   /questionnaires/{id}               — get questionnaire + items
  PATCH /questionnaires/{id}/items/{iid}   — manual override of an answer

Auto-fill model: for each question, look up findings tagged with its check_ids
within the user's tenant. If all match pass -> 'yes' (high conf), all fail ->
'no' (high conf), mixed -> 'partial' (medium), none -> 'manual'.
"""
from __future__ import annotations

import json
import os
import uuid

import boto3

from questions import BANKS, list_banks, get_bank
import anthropic_call

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    method = event.get("httpMethod", "GET")
    path = event.get("path") or ""
    path_params = event.get("pathParameters") or {}
    qid  = path_params.get("id")
    iid  = path_params.get("iid")

    if method == "GET" and path.endswith("/templates"):
        return _resp(200, {"templates": list_banks()})
    if method == "GET" and qid and iid:
        return _resp(400, {"error": "use PATCH on items"})
    if method == "GET" and qid:
        return _get(tenant_id, qid)
    if method == "GET":
        return _list(tenant_id)
    if method == "POST":
        return _create(tenant_id, _body(event))
    if method == "POST" and path.endswith("/from-excel"):
        return _from_excel(tenant_id, _body(event))
    if method == "POST" and qid and iid:
        return _suggest_item(tenant_id, qid, iid)
    if method == "PATCH" and qid and iid:
        return _patch_item(tenant_id, qid, iid, _body(event))
    return _resp(400, {"error": "unsupported"})


def _from_excel(tenant_id: str, body: dict) -> dict:
    """Create a questionnaire from rows parsed client-side from an Excel upload.

    Body:
      {
        "filename": "vendor-sig.xlsx",
        "name":     "Optional display name (defaults to filename)",
        "rows":     [
          { "row_idx": 5, "question": "Is MFA enforced?", "category": "Access Control" },
          ...
        ]
      }

    Returns: { questionnaire_id, items: int }
    """
    filename = (body.get("filename") or "uploaded.xlsx").strip()[:200]
    name     = (body.get("name") or filename).strip()[:200]
    rows     = body.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return _resp(400, {"error": "no_rows"})
    if len(rows) > 500:
        return _resp(400, {"error": "too_many_rows", "max": 500})

    qid = str(uuid.uuid4())
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("INSERT INTO questionnaires (questionnaire_id, tenant_id, name, template_key, source_filename) "
             "VALUES (CAST(:q AS UUID), CAST(:t AS UUID), :n, 'excel_upload', :f)"),
        parameters=[
            {"name": "q", "value": {"stringValue": qid}},
            {"name": "t", "value": {"stringValue": tenant_id}},
            {"name": "n", "value": {"stringValue": name}},
            {"name": "f", "value": {"stringValue": filename}},
        ],
    )

    inserted = 0
    for idx, row in enumerate(rows):
        question = (row.get("question") or "").strip()
        if not question:
            continue
        item_id = str(uuid.uuid4())
        category = (row.get("category") or "")[:100] if row.get("category") else None
        source_row_idx = row.get("row_idx")
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=("INSERT INTO questionnaire_items "
                 "(item_id, questionnaire_id, question_id, question, category, "
                 " sort_order, source_row_idx, evidence) "
                 "VALUES (CAST(:i AS UUID), CAST(:q AS UUID), :qid, :qt, :cat, :s, :ri, "
                 "        CAST('{}' AS JSONB))"),
            parameters=[
                {"name": "i",   "value": {"stringValue": item_id}},
                {"name": "q",   "value": {"stringValue": qid}},
                {"name": "qid", "value": {"stringValue": f"R{source_row_idx if source_row_idx is not None else idx}"}},
                {"name": "qt",  "value": {"stringValue": question[:2000]}},
                {"name": "cat", "value": ({"stringValue": category} if category else {"isNull": True})},
                {"name": "s",   "value": {"longValue": idx}},
                {"name": "ri",  "value": ({"longValue": int(source_row_idx)} if isinstance(source_row_idx, int) else {"isNull": True})},
            ],
        )
        inserted += 1

    return _resp(200, {"questionnaire_id": qid, "items": inserted})


def _suggest_item(tenant_id: str, qid: str, iid: str) -> dict:
    """AI-suggest an answer for one questionnaire item, grounded on findings."""
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT qi.question, qi.category, qi.evidence::text "
             "FROM questionnaire_items qi "
             "JOIN questionnaires q ON q.questionnaire_id = qi.questionnaire_id "
             "WHERE qi.item_id = CAST(:i AS UUID) AND qi.questionnaire_id = CAST(:q AS UUID) "
             "AND q.tenant_id = CAST(:t AS UUID)"),
        parameters=[
            {"name": "i", "value": {"stringValue": iid}},
            {"name": "q", "value": {"stringValue": qid}},
            {"name": "t", "value": {"stringValue": tenant_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return _resp(404, {"error": "item_not_found"})
    question = rows[0][0].get("stringValue")
    category = rows[0][1].get("stringValue") if not rows[0][1].get("isNull") else ""
    evidence = json.loads(rows[0][2].get("stringValue") or "{}")

    # Pull a small posture context: cloud types, open finding count totals.
    posture = _tenant_posture(tenant_id)

    system = (
        "You are a security questionnaire assistant. Given a yes/no compliance "
        "question and the company's actual posture, decide the most defensible "
        "answer and write a 1-2 sentence justification grounded in the data. "
        "Return STRICT JSON only — no commentary outside the JSON — with shape: "
        "{\"answer\": \"yes\"|\"no\"|\"partial\"|\"n/a\", \"justification\": \"...\"}."
    )
    user = (
        f"## Question\nCategory: {category}\n{question}\n\n"
        f"## Evidence from automated scans\n{json.dumps(evidence, indent=2)}\n\n"
        f"## Company posture\n{json.dumps(posture, indent=2)}\n\n"
        f"Now answer."
    )

    try:
        raw = anthropic_call.call(system, user, max_tokens=512)
    except Exception as e:
        return _resp(502, {"error": "anthropic_failed", "detail": str(e)[:300]})

    parsed = _parse_strict_json(raw)
    if not parsed:
        return _resp(502, {"error": "model_returned_non_json", "raw": raw[:300]})

    # Save as a notes/answer suggestion (don't overwrite manual answers)
    answer = (parsed.get("answer") or "").lower()
    if answer not in {"yes", "no", "partial", "n/a"}:
        return _resp(502, {"error": "invalid_answer_from_model", "answer": parsed.get("answer")})
    justification = (parsed.get("justification") or "").strip()

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("UPDATE questionnaire_items SET answer = :a, confidence = 'ai-suggested', "
             "       notes = :n, updated_at = now() "
             "WHERE item_id = CAST(:i AS UUID) AND questionnaire_id = CAST(:q AS UUID)"),
        parameters=[
            {"name": "i", "value": {"stringValue": iid}},
            {"name": "q", "value": {"stringValue": qid}},
            {"name": "a", "value": {"stringValue": answer}},
            {"name": "n", "value": {"stringValue": justification[:5000]}},
        ],
    )
    return _resp(200, {"answer": answer, "justification": justification, "confidence": "ai-suggested"})


def _tenant_posture(tenant_id: str) -> dict:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT cloud_type, COUNT(*) FROM cloud_connections "
             "WHERE tenant_id = CAST(:t AS UUID) AND status = 'active' GROUP BY cloud_type"),
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    clouds = {r[0].get("stringValue"): int(r[1].get("longValue", 0))
              for r in rs.get("records", [])}

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT severity, COUNT(*) FROM findings WHERE tenant_id = CAST(:t AS UUID) "
             "AND status = 'fail' GROUP BY severity"),
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    fcounts = {r[0].get("stringValue"): int(r[1].get("longValue", 0))
               for r in rs.get("records", [])}

    return {"clouds": clouds, "open_findings": fcounts}


def _parse_strict_json(raw: str) -> dict | None:
    """Be forgiving about ``` fences but otherwise require JSON."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        # Drop a leading "json" label if present
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _list(tenant_id: str) -> dict:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT q.questionnaire_id::text, q.name, q.template_key, q.status, "
             "       q.created_at::text, q.updated_at::text, "
             "       (SELECT COUNT(*) FROM questionnaire_items WHERE questionnaire_id = q.questionnaire_id) AS total, "
             "       (SELECT COUNT(*) FROM questionnaire_items WHERE questionnaire_id = q.questionnaire_id AND answer IS NOT NULL) AS answered "
             "FROM questionnaires q WHERE q.tenant_id = CAST(:t AS UUID) "
             "ORDER BY q.updated_at DESC"),
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    items = []
    for r in rs.get("records", []):
        items.append({
            "questionnaire_id": r[0].get("stringValue"),
            "name":             r[1].get("stringValue"),
            "template_key":     r[2].get("stringValue"),
            "status":           r[3].get("stringValue"),
            "created_at":       r[4].get("stringValue"),
            "updated_at":       r[5].get("stringValue"),
            "total":            int(r[6].get("longValue", 0)),
            "answered":         int(r[7].get("longValue", 0)),
        })
    return _resp(200, {"questionnaires": items})


def _get(tenant_id: str, qid: str) -> dict:
    head_rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT questionnaire_id::text, name, template_key, status, created_at::text, "
             "       source_filename "
             "FROM questionnaires WHERE questionnaire_id = CAST(:q AS UUID) AND tenant_id = CAST(:t AS UUID)"),
        parameters=[
            {"name": "q", "value": {"stringValue": qid}},
            {"name": "t", "value": {"stringValue": tenant_id}},
        ],
    )
    head_rows = head_rs.get("records", [])
    if not head_rows:
        return _resp(404, {"error": "not_found"})
    h = head_rows[0]

    items_rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT item_id::text, question_id, question, category, answer, confidence, "
             "       evidence::text, notes, sort_order, source_row_idx "
             "FROM questionnaire_items WHERE questionnaire_id = CAST(:q AS UUID) "
             "ORDER BY sort_order"),
        parameters=[{"name": "q", "value": {"stringValue": qid}}],
    )
    items = []
    for r in items_rs.get("records", []):
        items.append({
            "item_id":     r[0].get("stringValue"),
            "question_id": r[1].get("stringValue"),
            "question":    r[2].get("stringValue"),
            "category":    r[3].get("stringValue") if not r[3].get("isNull") else None,
            "answer":      r[4].get("stringValue") if not r[4].get("isNull") else None,
            "confidence":  r[5].get("stringValue") if not r[5].get("isNull") else None,
            "evidence":    json.loads(r[6].get("stringValue") or "{}"),
            "notes":       r[7].get("stringValue") if not r[7].get("isNull") else None,
            "sort_order":  int(r[8].get("longValue", 0)),
            "source_row_idx": int(r[9].get("longValue")) if not r[9].get("isNull") else None,
        })

    return _resp(200, {
        "questionnaire_id": h[0].get("stringValue"),
        "name":             h[1].get("stringValue"),
        "template_key":     h[2].get("stringValue"),
        "status":           h[3].get("stringValue"),
        "created_at":       h[4].get("stringValue"),
        "source_filename":  h[5].get("stringValue") if not h[5].get("isNull") else None,
        "items":            items,
    })


def _create(tenant_id: str, body: dict) -> dict:
    template_key = body.get("template_key")
    bank = get_bank(template_key) if template_key else None
    if not bank:
        return _resp(400, {"error": "invalid_template_key", "allowed": list(BANKS.keys())})

    qid = str(uuid.uuid4())
    name = body.get("name") or bank["name"]

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("INSERT INTO questionnaires (questionnaire_id, tenant_id, name, template_key) "
             "VALUES (CAST(:q AS UUID), CAST(:t AS UUID), :n, :k)"),
        parameters=[
            {"name": "q", "value": {"stringValue": qid}},
            {"name": "t", "value": {"stringValue": tenant_id}},
            {"name": "n", "value": {"stringValue": name}},
            {"name": "k", "value": {"stringValue": template_key}},
        ],
    )

    # For each question: auto-fill from findings
    for idx, q in enumerate(bank["questions"]):
        item_id = str(uuid.uuid4())
        answer, confidence, evidence = _autofill(tenant_id, q.get("check_ids") or [])
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=("INSERT INTO questionnaire_items "
                 "(item_id, questionnaire_id, question_id, question, category, "
                 " answer, confidence, evidence, sort_order) "
                 "VALUES (CAST(:i AS UUID), CAST(:q AS UUID), :qid, :qt, :cat, "
                 "        :a, :c, CAST(:e AS JSONB), :s)"),
            parameters=[
                {"name": "i",   "value": {"stringValue": item_id}},
                {"name": "q",   "value": {"stringValue": qid}},
                {"name": "qid", "value": {"stringValue": q["id"]}},
                {"name": "qt",  "value": {"stringValue": q["text"]}},
                {"name": "cat", "value": {"stringValue": q.get("category", "")}},
                {"name": "a",   "value": ({"stringValue": answer} if answer else {"isNull": True})},
                {"name": "c",   "value": ({"stringValue": confidence} if confidence else {"isNull": True})},
                {"name": "e",   "value": {"stringValue": json.dumps(evidence)}},
                {"name": "s",   "value": {"longValue": idx}},
            ],
        )

    return _resp(200, {"questionnaire_id": qid, "items": len(bank["questions"])})


def _autofill(tenant_id: str, check_ids: list[str]) -> tuple[str | None, str | None, dict]:
    """Return (answer, confidence, evidence) for a question's check_ids."""
    if not check_ids:
        return None, "manual", {"check_ids": [], "note": "no check mapping"}

    sql = ("SELECT status, COUNT(*) FROM findings "
           "WHERE tenant_id = CAST(:t AS UUID) "
           f"  AND check_id IN ({', '.join(f':c{i}' for i in range(len(check_ids)))}) "
           "GROUP BY status")
    params = [{"name": "t", "value": {"stringValue": tenant_id}}]
    for i, cid in enumerate(check_ids):
        params.append({"name": f"c{i}", "value": {"stringValue": cid}})
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=params,
    )
    counts: dict[str, int] = {}
    for r in rs.get("records", []):
        counts[r[0].get("stringValue")] = int(r[1].get("longValue", 0))

    pass_n = counts.get("pass", 0)
    fail_n = counts.get("fail", 0)
    total  = pass_n + fail_n

    evidence = {"check_ids": check_ids, "pass": pass_n, "fail": fail_n}

    if total == 0:
        return None, "manual", evidence
    if fail_n == 0:
        return "yes", "auto-high", evidence
    if pass_n == 0:
        return "no", "auto-high", evidence
    return "partial", "auto-medium", evidence


def _patch_item(tenant_id: str, qid: str, iid: str, body: dict) -> dict:
    # Make sure the questionnaire belongs to this tenant.
    check = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("SELECT 1 FROM questionnaires WHERE questionnaire_id = CAST(:q AS UUID) "
             "AND tenant_id = CAST(:t AS UUID)"),
        parameters=[
            {"name": "q", "value": {"stringValue": qid}},
            {"name": "t", "value": {"stringValue": tenant_id}},
        ],
    )
    if not check.get("records"):
        return _resp(404, {"error": "questionnaire_not_found"})

    sets = []
    params = [
        {"name": "i", "value": {"stringValue": iid}},
        {"name": "q", "value": {"stringValue": qid}},
    ]
    if "answer" in body:
        sets.append("answer = :a")
        sets.append("confidence = 'manual'")
        params.append({"name": "a", "value": ({"stringValue": body["answer"]} if body["answer"] else {"isNull": True})})
    if "notes" in body:
        sets.append("notes = :n")
        params.append({"name": "n", "value": {"stringValue": (body["notes"] or "")[:5000]}})
    if not sets:
        return _resp(400, {"error": "no_fields_to_update"})
    sets.append("updated_at = now()")

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(f"UPDATE questionnaire_items SET {', '.join(sets)} "
             f"WHERE item_id = CAST(:i AS UUID) AND questionnaire_id = CAST(:q AS UUID)"),
        parameters=params,
    )
    if rs.get("numberOfRecordsUpdated", 0) == 0:
        return _resp(404, {"error": "item_not_found"})
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
