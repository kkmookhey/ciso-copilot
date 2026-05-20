"""Server-side tool registry for the chat text path (SP4 Task 4b.3).

The text chat path runs the Anthropic tool-use loop entirely server-side in
app.py. This module is the Python mirror of web/src/chat/tools.ts — the same 12
tools, the same artifact-hint shapes. The browser's tools.ts stays as the VOICE
path registry; tools.ts is the authoritative shape for the ArtifactHint union
and Artifact.tsx renders against it.

Each tool:
  - name / description / input_schema  — passed to the Anthropic Messages API
  - execute(tenant_id, args) -> dict   — returns {result, _artifact_hint?,
    _artifact_hints?, source?}  (snake_case, same shape as the TS ToolResult)

The 8 data tools query Aurora directly via _db._q, tenant-scoped (every query
filters tenant_id — this is a security boundary). They replicate the SQL of the
matching REST Lambdas (findings_list, findings_summary, compliance_summary,
risks, entities_api) rather than HTTP-calling the REST API.

The 2 action tools (propose_*) NEVER mutate — they return a pending
approval_card. The 2 side-effect tools (navigate_to, filter_findings_view)
return an intent only; the browser performs the actual navigation/filter.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable

from _db import _claim_value, _q

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

SEV_ORDER = ["critical", "high", "medium", "low", "info"]
ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "info"}
ALLOWED_STATUSES = {"open", "mitigated", "accepted", "transferred", "closed"}
ALLOWED_CLOUDS = {"aws", "azure", "entra", "gcp"}

# Static edit_fields per action_kind — mirrors tools.ts (spec §8).
ADD_RISK_EDIT_FIELDS = [
    {"key": "title", "label": "Title", "type": "text"},
    {"key": "severity", "label": "Severity", "type": "select",
     "options": ["critical", "high", "medium", "low"]},
    {"key": "status", "label": "Status", "type": "select",
     "options": ["open", "mitigated", "accepted", "transferred", "closed"]},
    {"key": "owner", "label": "Owner", "type": "text"},
    {"key": "due_date", "label": "Due date", "type": "date"},
]

DRAFT_POLICY_EDIT_FIELDS = [
    {"key": "name", "label": "Name", "type": "text"},
    {"key": "content", "label": "Content", "type": "textarea"},
    {"key": "status", "label": "Status", "type": "select",
     "options": ["draft", "approved", "retired"]},
]


# ---------------------------------------------------------------------------
# Data-tool query helpers (mirror the REST Lambdas; all tenant-scoped)
# ---------------------------------------------------------------------------

ALLOWED_DOMAINS = {"iam", "storage", "encryption", "logging", "networking",
                   "monitoring", "compute"}


def _query_findings(tenant_id: str, severity: str | None = None,
                     cloud: str | None = None, check_id: str | None = None,
                     domain: str | None = None,
                     limit: int = 20) -> list[dict]:
    """Replicates findings_list/main.py — open ('fail') findings, tenant-scoped."""
    severities = [severity] if severity in ALLOWED_SEVERITIES else list(ALLOWED_SEVERITIES)
    limit = max(1, min(int(limit or 20), 200))
    cloud = cloud.lower() if cloud else None
    if cloud and cloud not in ALLOWED_CLOUDS:
        cloud = None
    domain = domain.lower() if domain else None
    if domain and domain not in ALLOWED_DOMAINS:
        domain = None

    sev_in = ", ".join(f":sev{i}" for i in range(len(severities)))
    sql = (
        "SELECT f.finding_id::text, f.check_id, f.title, f.description, f.severity, "
        "       f.status, f.resource_arn, f.resource_type, f.region, f.domain, "
        "       f.frameworks::text "
        "FROM findings f "
        + ("JOIN cloud_connections c ON c.conn_id = f.conn_id AND c.cloud_type = :cloud "
           if cloud else "")
        + "WHERE f.tenant_id = CAST(:tid AS UUID) "
        + f"  AND f.severity IN ({sev_in}) "
        + "  AND f.status = 'fail' "
        + ("  AND f.check_id = :chk " if check_id else "")
        + ("  AND f.domain = :dom " if domain else "")
        + "ORDER BY CASE f.severity "
          "    WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 "
          "    WHEN 'low' THEN 4 ELSE 5 END, f.last_seen DESC "
        + "LIMIT :lim"
    )
    params: dict[str, Any] = {"tid": tenant_id, "lim": limit}
    for i, s in enumerate(severities):
        params[f"sev{i}"] = s
    if cloud:
        params["cloud"] = cloud
    if check_id:
        params["chk"] = check_id
    if domain:
        params["dom"] = domain

    rows = _q(sql, params)
    return [
        {
            "finding_id": _claim_value(r[0]),
            "check_id": _claim_value(r[1]),
            "title": _claim_value(r[2]),
            "description": _claim_value(r[3]),
            "severity": _claim_value(r[4]),
            "status": _claim_value(r[5]),
            "resource_arn": _claim_value(r[6]),
            "resource_type": _claim_value(r[7]),
            "region": _claim_value(r[8]),
            "domain": _claim_value(r[9]),
            "frameworks": _claim_value(r[10]) or "{}",
        }
        for r in rows
    ]


def _severity_breakdown(tenant_id: str) -> dict:
    """Replicates findings_summary/main.py by_severity aggregate, tenant-scoped."""
    rows = _q(
        "SELECT f.severity AS k, COUNT(*) AS n FROM findings f "
        "WHERE f.tenant_id = CAST(:tid AS UUID) AND f.status = 'fail' "
        "GROUP BY f.severity",
        {"tid": tenant_id},
    )
    counts = {_claim_value(r[0]): int(_claim_value(r[1]) or 0) for r in rows}
    by_severity = {
        "critical": counts.get("critical", 0),
        "high": counts.get("high", 0),
        "medium": counts.get("medium", 0),
        "low": counts.get("low", 0),
        "info": counts.get("info", 0),
    }
    return {"by_severity": by_severity, "total": sum(by_severity.values())}


def _list_risks(tenant_id: str, status: str | None = None,
                severity: str | None = None) -> list[dict]:
    """Replicates risks/main.py _list, tenant-scoped."""
    sql = (
        "SELECT risk_id::text, title, description, severity, status, owner, "
        "       due_date::text, finding_id::text "
        "FROM risks WHERE tenant_id = CAST(:tid AS UUID)"
    )
    params: dict[str, Any] = {"tid": tenant_id}
    if status and status in ALLOWED_STATUSES:
        sql += " AND status = :st"
        params["st"] = status
    if severity and severity in ALLOWED_SEVERITIES:
        sql += " AND severity = :sev"
        params["sev"] = severity
    sql += (" ORDER BY CASE severity "
            "  WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 "
            "  WHEN 'low' THEN 4 ELSE 5 END, "
            "  COALESCE(due_date, '9999-12-31'::date), created_at DESC")
    rows = _q(sql, params)
    return [
        {
            "risk_id": _claim_value(r[0]),
            "title": _claim_value(r[1]),
            "description": _claim_value(r[2]),
            "severity": _claim_value(r[3]),
            "status": _claim_value(r[4]),
            "owner": _claim_value(r[5]),
            "due_date": _claim_value(r[6]),
            "finding_id": _claim_value(r[7]),
        }
        for r in rows
    ]


def _list_entities(tenant_id: str, domain: str | None = None,
                   kind: str | None = None, per_page: int = 20) -> list[dict]:
    """Replicates entities_api/main.py _list_entities, tenant-scoped."""
    per_page = max(1, min(int(per_page or 20), 200))
    sql = (
        "SELECT e.id::text, e.kind, e.natural_key, e.display_name, e.domain, "
        "       e.attributes::text "
        "FROM entities e WHERE e.tenant_id = CAST(:tid AS UUID)"
    )
    params: dict[str, Any] = {"tid": tenant_id, "lim": per_page}
    if domain:
        sql += " AND e.domain = :dom"
        params["dom"] = domain
    if kind:
        sql += " AND e.kind = :kind"
        params["kind"] = kind
    sql += " ORDER BY e.last_seen_at DESC LIMIT :lim"
    rows = _q(sql, params)
    out = []
    for r in rows:
        import json as _json
        attrs = _json.loads(_claim_value(r[5]) or "{}")
        out.append({
            "id": _claim_value(r[0]),
            "kind": _claim_value(r[1]),
            "natural_key": _claim_value(r[2]),
            "display_name": _claim_value(r[3]),
            "domain": _claim_value(r[4]),
            "source_path": attrs.get("source_path"),
        })
    return out


def _get_entity_row(tenant_id: str, entity_id: str) -> dict | None:
    """Single entity by id, tenant-scoped — mirrors entities_api _get_entity."""
    rows = _q(
        "SELECT id::text, kind, natural_key, display_name, domain, attributes::text "
        "FROM entities WHERE tenant_id = CAST(:tid AS UUID) "
        "  AND id = CAST(:eid AS UUID) LIMIT 1",
        {"tid": tenant_id, "eid": entity_id},
    )
    if not rows:
        return None
    import json as _json
    r = rows[0]
    attrs = _json.loads(_claim_value(r[5]) or "{}")
    return {
        "id": _claim_value(r[0]),
        "kind": _claim_value(r[1]),
        "natural_key": _claim_value(r[2]),
        "display_name": _claim_value(r[3]),
        "domain": _claim_value(r[4]),
        "source_path": attrs.get("source_path"),
    }


def _compliance_summary(tenant_id: str) -> dict:
    """Replicates compliance_summary/main.py rollup, tenant-scoped."""
    rows = _q(
        "SELECT fw.key AS framework, ctrl::text AS control_id, "
        "       COUNT(*) FILTER (WHERE f.status = 'fail') AS fail_count, "
        "       COUNT(*) FILTER (WHERE f.status = 'pass') AS pass_count, "
        "       COUNT(*) AS total "
        "FROM findings f, "
        "     jsonb_each(f.frameworks) AS fw(key, value), "
        "     jsonb_array_elements_text(fw.value) AS ctrl "
        "WHERE f.tenant_id = CAST(:tid AS UUID) "
        "GROUP BY fw.key, ctrl ORDER BY fw.key, ctrl",
        {"tid": tenant_id},
    )
    rollup: dict[str, dict] = {}
    for r in rows:
        framework = _claim_value(r[0])
        control_id = _claim_value(r[1])
        fail_count = int(_claim_value(r[2]) or 0)
        pass_count = int(_claim_value(r[3]) or 0)
        fw = rollup.setdefault(
            framework, {"controls": set(), "passing": set(), "failing": set()})
        fw["controls"].add(control_id)
        if fail_count > 0:
            fw["failing"].add(control_id)
        elif pass_count > 0:
            fw["passing"].add(control_id)
    summary = {}
    for framework, agg in rollup.items():
        total = len(agg["controls"])
        passing = len(agg["passing"])
        failing = len(agg["failing"])
        assessed = passing + failing
        score = (passing / assessed * 100) if assessed > 0 else 0.0
        summary[framework] = {
            "total": total,
            "passing": passing,
            "failing": failing,
            "score_pct": round(score, 1),
        }
    return {"summary": summary}


# ---------------------------------------------------------------------------
# Artifact-hint builders — shapes match tools.ts ArtifactHint EXACTLY
# ---------------------------------------------------------------------------

def _finding_card(f: dict) -> dict:
    import json as _json
    fw = f.get("frameworks") or "{}"
    frameworks = list(_json.loads(fw).keys()) if isinstance(fw, str) else list(fw.keys())
    return {
        "kind": "finding_card",
        "finding_id": f["finding_id"],
        "check_id": f["check_id"],
        "title": f["title"],
        "severity": f["severity"],
        "description": f.get("description"),
        "resource_arn": f.get("resource_arn"),
        "region": f.get("region"),
        "frameworks": frameworks or None,
        "source": {"finding_id": f["finding_id"]},
    }


def _entity_list_hint(title: str, entities: list[dict]) -> dict:
    return {
        "kind": "entity_list",
        "title": title,
        "entities": entities,
    }


def _severity_breakdown_hint(sev: dict) -> dict:
    by = sev["by_severity"]
    return {
        "kind": "severity_breakdown",
        "total": sev["total"],
        "critical": by["critical"],
        "high": by["high"],
        "medium": by["medium"],
        "low": by["low"],
    }


def _risk_cards(risks: list[dict]) -> list[dict]:
    cards = []
    for r in risks:
        card = {
            "kind": "risk_card",
            "risk_id": r["risk_id"],
            "title": r["title"],
            "severity": r["severity"],
            "status": r["status"],
        }
        if r.get("owner"):
            card["owner"] = r["owner"]
        if r.get("due_date"):
            card["due_date"] = r["due_date"]
        if r.get("finding_id"):
            card["source"] = {"finding_id": r["finding_id"]}
        cards.append(card)
    return cards


# ---------------------------------------------------------------------------
# Tool executors  — signature: execute(tenant_id, args) -> ToolResult dict
# ---------------------------------------------------------------------------

def _exec_get_morning_briefing(tenant_id: str, args: dict) -> dict:
    sev = _severity_breakdown(tenant_id)
    risks = _list_risks(tenant_id)
    by = sev["by_severity"]
    total = sev["total"]

    top_sev = next((s for s in SEV_ORDER if by.get(s, 0) > 0), None)
    if top_sev:
        n = by[top_sev]
        value = f"{n} {top_sev} finding" + ("s" if n != 1 else "")
    else:
        value = "No open findings"
    top_finding_kpi = {
        "kind": "kpi_card",
        "label": "Top open finding",
        "value": value,
        "severity": top_sev or "info",
        "detail": f"{total} total findings",
    }
    severity_breakdown = _severity_breakdown_hint(sev)
    open_count = sum(1 for r in risks if r["status"] == "open")
    count = len(risks)
    risk_kpi = {
        "kind": "kpi_card",
        "label": "Risk register",
        "value": f"{count} total risk" + ("s" if count != 1 else ""),
        "detail": f"{open_count} open",
    }
    hints = [top_finding_kpi, severity_breakdown, risk_kpi]
    return {
        "result": {
            "summary": sev,
            "risks_count": count,
            "open_risks": open_count,
        },
        "_artifact_hint": top_finding_kpi,
        "_artifact_hints": hints,
    }


def _exec_query_entities(tenant_id: str, args: dict) -> dict:
    rows = _list_entities(
        tenant_id,
        domain=args.get("domain"),
        kind=args.get("kind"),
        per_page=args.get("per_page", 20),
    )
    entities = [
        {
            "id": e["id"],
            "kind": e["kind"],
            "display_name": e["display_name"],
            "source_path": e.get("source_path"),
            "source": {"entity_id": e["id"]},
        }
        for e in rows
    ]
    title = f"{args['domain']} entities" if args.get("domain") else "Entities"
    return {
        "result": {"entities": rows, "count": len(rows)},
        "_artifact_hint": _entity_list_hint(title, entities),
    }


def _exec_get_entity(tenant_id: str, args: dict) -> dict:
    eid = args.get("entity_id")
    if not eid:
        return {"result": None}
    e = _get_entity_row(tenant_id, eid)
    if not e:
        return {"result": None}
    entity = {
        "id": e["id"],
        "kind": e["kind"],
        "display_name": e["display_name"],
        "source_path": e.get("source_path"),
        "source": {"entity_id": e["id"]},
    }
    return {
        "result": e,
        "_artifact_hint": _entity_list_hint(e["display_name"], [entity]),
        "source": {"entity_id": e["id"]},
    }


def _exec_query_findings(tenant_id: str, args: dict) -> dict:
    findings = _query_findings(
        tenant_id,
        severity=args.get("severity"),
        cloud=args.get("cloud"),
        check_id=args.get("check_id"),
        domain=args.get("domain"),
        limit=args.get("limit", 20),
    )
    result = {"findings": findings, "count": len(findings)}
    if 0 < len(findings) <= 3:
        cards = [_finding_card(f) for f in findings]
        return {
            "result": result,
            "_artifact_hint": cards[0],
            "_artifact_hints": cards,
            "source": {"finding_id": findings[0]["finding_id"]},
        }
    title = f"{args['severity']} findings" if args.get("severity") else "Findings"
    entities = [
        {
            "id": f["finding_id"],
            "kind": "finding",
            "display_name": f["title"],
            "source": {"finding_id": f["finding_id"]},
        }
        for f in findings
    ]
    return {
        "result": result,
        "_artifact_hint": _entity_list_hint(title, entities),
    }


def _exec_get_finding(tenant_id: str, args: dict) -> dict:
    check_id = args.get("check_id")
    finding_id = args.get("finding_id")
    finding = None
    if check_id:
        matches = _query_findings(tenant_id, check_id=check_id, limit=10)
        finding = matches[0] if matches else None
    if finding_id and (not finding or finding["finding_id"] != finding_id):
        broader = _query_findings(tenant_id, limit=200)
        finding = next(
            (f for f in broader if f["finding_id"] == finding_id), finding)
    if not finding:
        return {"result": None}
    card = _finding_card(finding)
    return {
        "result": finding,
        "_artifact_hint": card,
        "source": {"finding_id": finding["finding_id"]},
    }


def _exec_get_compliance_summary(tenant_id: str, args: dict) -> dict:
    data = _compliance_summary(tenant_id)
    summary = data["summary"]

    # No per-segment color — ChartDonut assigns distinct palette colors
    # automatically, so any chart_donut renders distinguishable.
    # value = passing control count (one slice per framework).
    segments = [
        {"label": fw, "value": counts["passing"]}
        for fw, counts in summary.items()
    ]
    donut = {"kind": "chart_donut", "title": "Compliance posture — passing controls by framework", "segments": segments}

    def _sev(score: float) -> str:
        if score >= 80:
            return "info"
        if score >= 50:
            return "medium"
        return "high"

    framework_kpis = [
        {
            "kind": "kpi_card",
            "label": fw,
            "value": f"{counts['score_pct']:.0f}%",
            "detail": f"{counts['passing']} / {counts['total']} controls passing",
            "severity": _sev(counts["score_pct"]),
        }
        for fw, counts in summary.items()
    ]
    return {
        "result": data,
        "_artifact_hint": donut,
        "_artifact_hints": [donut, *framework_kpis],
    }


def _exec_get_severity_breakdown(tenant_id: str, args: dict) -> dict:
    sev = _severity_breakdown(tenant_id)
    return {
        "result": sev,
        "_artifact_hint": _severity_breakdown_hint(sev),
    }


def _exec_list_risks(tenant_id: str, args: dict) -> dict:
    risks = _list_risks(
        tenant_id, status=args.get("status"), severity=args.get("severity"))
    cards = _risk_cards(risks)
    out: dict = {"result": {"risks": risks, "count": len(risks)}}
    if cards:
        out["_artifact_hint"] = cards[0]
    out["_artifact_hints"] = cards
    return out


def _exec_propose_risk_entry(tenant_id: str, args: dict) -> dict:
    """ACTION tool — NO mutation. Returns a pending approval_card."""
    payload = {
        "title": args.get("title") or "",
        "severity": args.get("severity") or "medium",
        "description": args.get("description") or "",
        "owner": args.get("owner") or "",
        "due_date": args.get("due_date") or "",
        "status": args.get("status") or "open",
    }
    hint = {
        "kind": "approval_card",
        "action_kind": "add_risk",
        "current_status": "pending",
        "approval_id": str(uuid.uuid4()),
        "payload": payload,
        "edit_fields": ADD_RISK_EDIT_FIELDS,
    }
    return {"result": {"proposed": payload}, "_artifact_hint": hint}


def _exec_propose_policy_draft(tenant_id: str, args: dict) -> dict:
    """ACTION tool — NO mutation. Returns a pending approval_card."""
    payload = {
        "name": args.get("name") or "",
        "content": args.get("content") or "",
        "template_id": args.get("template_id") or "",
        "status": args.get("status") or "draft",
    }
    hint = {
        "kind": "approval_card",
        "action_kind": "draft_policy",
        "current_status": "pending",
        "approval_id": str(uuid.uuid4()),
        "payload": payload,
        "edit_fields": DRAFT_POLICY_EDIT_FIELDS,
    }
    return {"result": {"proposed": payload}, "_artifact_hint": hint}


def _exec_navigate_to(tenant_id: str, args: dict) -> dict:
    """SIDE-EFFECT tool — intent only; the browser performs navigation."""
    return {"result": {"navigated_to": args.get("path")}}


def _exec_filter_findings_view(tenant_id: str, args: dict) -> dict:
    """SIDE-EFFECT tool — intent only; the browser applies the UI filter."""
    return {"result": {"filtered": dict(args)}}


# ---------------------------------------------------------------------------
# The 12-tool catalog — mirrors tools.ts TOOLS, in spec order
# ---------------------------------------------------------------------------

class _T:
    """Lightweight tool descriptor."""
    __slots__ = ("name", "description", "input_schema", "flavor", "execute")

    def __init__(self, name: str, description: str, input_schema: dict,
                 flavor: str, execute: Callable[[str, dict], dict]):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.flavor = flavor
        self.execute = execute


_EMPTY_SCHEMA = {"type": "object", "properties": {}, "required": []}

TOOLS: list[_T] = [
    _T(
        "get_morning_briefing",
        "Returns a morning security briefing: the top open finding severity, a "
        "severity breakdown of all findings, and a risk register summary. Use on "
        "first sign-in or when the user asks for a daily overview.",
        _EMPTY_SCHEMA, "data", _exec_get_morning_briefing,
    ),
    _T(
        "query_entities",
        "Query the entity inventory. Filter by domain (ai, cloud, repo, identity, "
        "asm), kind (e.g. aws_s3_bucket, ai_model), or repo name. Returns an "
        "entity_list artifact.",
        {
            "type": "object",
            "properties": {
                "domain": {"type": "string",
                           "enum": ["ai", "cloud", "repo", "identity", "asm"]},
                "kind": {"type": "string"},
                "repo": {"type": "string",
                         "description": "GitHub repo full name filter"},
                "page": {"type": "number"},
                "per_page": {"type": "number", "default": 20},
            },
            "required": [],
        },
        "data", _exec_query_entities,
    ),
    _T(
        "get_entity",
        "Get a single entity by its UUID. Returns an entity_list artifact with "
        "one entry.",
        {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string",
                              "description": "UUID of the entity"},
            },
            "required": ["entity_id"],
        },
        "data", _exec_get_entity,
    ),
    _T(
        "query_findings",
        "Query open security findings. Filter by severity, cloud, domain, or "
        "check_id. Use domain to scope by security category: 'iam' for identity "
        "and access management findings, 'networking' for network findings, "
        "'encryption' for key/certificate/encryption findings (including Key "
        "Vault), 'storage' for storage findings, 'logging' for audit/log "
        "findings, 'monitoring' for alerting findings, 'compute' for VM/container "
        "findings. Returns an entity_list for many results or individual "
        "finding_card artifacts for <=3 matches.",
        {
            "type": "object",
            "properties": {
                "severity": {"type": "string",
                             "enum": ["critical", "high", "medium", "low", "info"]},
                "cloud": {"type": "string",
                          "enum": ["aws", "azure", "gcp", "entra"]},
                "domain": {"type": "string",
                           "enum": ["iam", "storage", "encryption", "logging",
                                    "networking", "monitoring", "compute"],
                           "description": "Security domain / category to filter by. "
                                          "Use 'iam' for IAM/identity findings."},
                "check_id": {"type": "string"},
                "limit": {"type": "number", "default": 20},
            },
            "required": [],
        },
        "data", _exec_query_findings,
    ),
    _T(
        "get_finding",
        "Get a single finding by finding_id or check_id. Returns a finding_card "
        "artifact with full detail.",
        {
            "type": "object",
            "properties": {
                "finding_id": {"type": "string",
                               "description": "UUID of the finding"},
                "check_id": {"type": "string",
                             "description": "Check ID (returns first match)"},
            },
            "required": [],
        },
        "data", _exec_get_finding,
    ),
    _T(
        "get_compliance_summary",
        "Returns a compliance summary across all connected frameworks. Renders "
        "as a donut chart plus per-framework score tiles.",
        _EMPTY_SCHEMA, "data", _exec_get_compliance_summary,
    ),
    _T(
        "get_severity_breakdown",
        "Returns a count of findings by severity (critical, high, medium, low). "
        "Use for posture overview or finding distribution questions.",
        _EMPTY_SCHEMA, "data", _exec_get_severity_breakdown,
    ),
    _T(
        "list_risks",
        "List items in the risk register. Filter by status "
        "(open/mitigated/accepted/transferred/closed) or severity.",
        {
            "type": "object",
            "properties": {
                "status": {"type": "string",
                           "enum": ["open", "mitigated", "accepted",
                                    "transferred", "closed"]},
                "severity": {"type": "string",
                             "enum": ["critical", "high", "medium", "low", "info"]},
            },
            "required": [],
        },
        "data", _exec_list_risks,
    ),
    _T(
        "propose_risk_entry",
        "Propose adding a new entry to the risk register. Returns an editable "
        "approval card that the user must explicitly approve — never "
        "auto-executes.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "severity": {"type": "string",
                             "enum": ["critical", "high", "medium", "low"]},
                "description": {"type": "string"},
                "owner": {"type": "string"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "status": {"type": "string",
                           "enum": ["open", "mitigated", "accepted",
                                    "transferred", "closed"], "default": "open"},
            },
            "required": ["title", "severity"],
        },
        "action", _exec_propose_risk_entry,
    ),
    _T(
        "propose_policy_draft",
        "Propose drafting a new policy document. Returns an editable approval "
        "card with a content field — the user must approve before any policy is "
        "created.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string",
                            "description": "Initial policy content in Markdown"},
                "template_id": {"type": "string",
                                "description": "Optional policy template key"},
                "status": {"type": "string",
                           "enum": ["draft", "approved", "retired"],
                           "default": "draft"},
            },
            "required": ["name"],
        },
        "action", _exec_propose_policy_draft,
    ),
    _T(
        "navigate_to",
        "Navigate the user to a specific app route (e.g. /findings, /risks, "
        "/policies, /dashboard). Use when the user asks to go to a section.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "App route path, e.g. /findings"},
            },
            "required": ["path"],
        },
        "side-effect", _exec_navigate_to,
    ),
    _T(
        "filter_findings_view",
        "Apply filters to the findings view (severity, cloud, check_id, status). "
        "Use when the user says 'show only critical AWS findings'. Returns the "
        "filter intent; the caller applies it to the UI.",
        {
            "type": "object",
            "properties": {
                "severity": {"type": "string",
                             "enum": ["critical", "high", "medium", "low", "info"]},
                "cloud": {"type": "string",
                          "enum": ["aws", "azure", "gcp", "entra"]},
                "check_id": {"type": "string"},
                "status": {"type": "string"},
            },
            "required": [],
        },
        "side-effect", _exec_filter_findings_view,
    ),
]

_BY_NAME = {t.name: t for t in TOOLS}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def anthropic_tool_defs() -> list[dict]:
    """The Anthropic Messages .tools array — {name, description, input_schema}."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in TOOLS
    ]


def dispatch(name: str, tenant_id: str, args: dict | None) -> dict:
    """Execute a tool by name, tenant-scoped. Returns the ToolResult dict.

    Raises KeyError if the tool name is unknown — the caller (app.py) should
    catch and surface this as a tool_result error so the agentic loop can
    recover gracefully.
    """
    tool = _BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    return tool.execute(tenant_id, args or {})
