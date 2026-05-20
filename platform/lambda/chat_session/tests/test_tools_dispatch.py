# platform/lambda/chat_session/tests/test_tools_dispatch.py
"""Tests for the server-side tool registry (tools_dispatch.py).

Data tools are exercised with a monkeypatched _q so no DB is touched — the
focus is on the artifact-hint shapes matching tools.ts EXACTLY. Action tools
are checked for pending approval_card + no mutation. Side-effect tools return
the intent only.
"""
import json

import pytest

import tools_dispatch as TD


# ---------------------------------------------------------------------------
# Helpers — build fake Data-API records (list[list[dict]])
# ---------------------------------------------------------------------------

def _s(v):
    return {"isNull": True} if v is None else {"stringValue": str(v)}


def _l(v):
    return {"longValue": int(v)}


# ---------------------------------------------------------------------------
# anthropic_tool_defs
# ---------------------------------------------------------------------------

def test_anthropic_tool_defs_has_12_tools():
    defs = TD.anthropic_tool_defs()
    assert len(defs) == 12
    names = {d["name"] for d in defs}
    assert "get_morning_briefing" in names
    assert "propose_risk_entry" in names
    for d in defs:
        assert {"name", "description", "input_schema"} <= set(d)


def test_dispatch_unknown_tool_raises_keyerror():
    with pytest.raises(KeyError):
        TD.dispatch("no_such_tool", "tenant-1", {})


# ---------------------------------------------------------------------------
# query_findings — entity_list (>3) and finding_card (<=3) shapes
# ---------------------------------------------------------------------------

def test_query_findings_three_results_returns_finding_cards(monkeypatch):
    rows = [
        [_s("fid-1"), _s("check.a"), _s("Bucket public"), _s("desc a"),
         _s("critical"), _s("fail"), _s("arn:aws:s3:::b"), _s("s3"),
         _s("us-east-1"), _s("cloud"), _s('{"soc2": ["CC6.1"]}')],
        [_s("fid-2"), _s("check.b"), _s("MFA off"), _s(None),
         _s("high"), _s("fail"), _s(None), _s("iam"),
         _s(None), _s("cloud"), _s("{}")],
    ]
    monkeypatch.setattr(TD, "_q", lambda sql, params=None: rows)

    out = TD.dispatch("query_findings", "tenant-1", {"severity": "critical"})
    hints = out["_artifact_hints"]
    assert len(hints) == 2
    h0 = hints[0]
    assert h0["kind"] == "finding_card"
    assert h0["finding_id"] == "fid-1"
    assert h0["check_id"] == "check.a"
    assert h0["severity"] == "critical"
    assert h0["frameworks"] == ["soc2"]
    assert h0["source"] == {"finding_id": "fid-1"}
    assert out["_artifact_hint"] == h0
    assert out["source"] == {"finding_id": "fid-1"}


def test_query_findings_many_results_returns_entity_list(monkeypatch):
    rows = [
        [_s(f"fid-{i}"), _s("c"), _s(f"Finding {i}"), _s(None), _s("low"),
         _s("fail"), _s(None), _s(None), _s(None), _s("cloud"), _s("{}")]
        for i in range(5)
    ]
    monkeypatch.setattr(TD, "_q", lambda sql, params=None: rows)

    out = TD.dispatch("query_findings", "tenant-1", {})
    hint = out["_artifact_hint"]
    assert hint["kind"] == "entity_list"
    assert len(hint["entities"]) == 5
    e0 = hint["entities"][0]
    assert e0["kind"] == "finding"
    assert e0["source"] == {"finding_id": "fid-0"}


def test_query_findings_is_tenant_scoped(monkeypatch):
    """The findings query MUST filter by tenant_id — security boundary."""
    captured = {}

    def fake_q(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(TD, "_q", fake_q)
    TD.dispatch("query_findings", "tenant-XYZ", {})
    assert "f.tenant_id = CAST(:tid AS UUID)" in captured["sql"]
    assert captured["params"]["tid"] == "tenant-XYZ"


# ---------------------------------------------------------------------------
# get_severity_breakdown — severity_breakdown shape
# ---------------------------------------------------------------------------

def test_get_severity_breakdown_shape(monkeypatch):
    rows = [
        [_s("critical"), _l(2)],
        [_s("high"), _l(5)],
        [_s("low"), _l(1)],
    ]
    monkeypatch.setattr(TD, "_q", lambda sql, params=None: rows)

    out = TD.dispatch("get_severity_breakdown", "tenant-1", {})
    hint = out["_artifact_hint"]
    assert hint["kind"] == "severity_breakdown"
    assert hint["total"] == 8
    assert hint["critical"] == 2
    assert hint["high"] == 5
    assert hint["medium"] == 0
    assert hint["low"] == 1


# ---------------------------------------------------------------------------
# list_risks — risk_card list
# ---------------------------------------------------------------------------

def test_list_risks_returns_risk_cards(monkeypatch):
    rows = [
        [_s("rid-1"), _s("Patch gap"), _s("desc"), _s("high"), _s("open"),
         _s("Alice"), _s("2026-06-01"), _s("fid-9")],
    ]
    monkeypatch.setattr(TD, "_q", lambda sql, params=None: rows)

    out = TD.dispatch("list_risks", "tenant-1", {"status": "open"})
    hint = out["_artifact_hint"]
    assert hint["kind"] == "risk_card"
    assert hint["risk_id"] == "rid-1"
    assert hint["status"] == "open"
    assert hint["owner"] == "Alice"
    assert hint["due_date"] == "2026-06-01"
    assert hint["source"] == {"finding_id": "fid-9"}
    assert out["_artifact_hints"] == [hint]


# ---------------------------------------------------------------------------
# get_morning_briefing — composes 3 artifacts via _artifact_hints
# ---------------------------------------------------------------------------

def test_get_morning_briefing_returns_three_artifacts(monkeypatch):
    calls = []

    def fake_q(sql, params=None):
        calls.append(sql)
        if "GROUP BY f.severity" in sql:
            return [[_s("high"), _l(3)]]
        # risks query
        return [
            [_s("rid-1"), _s("R1"), _s(None), _s("high"), _s("open"),
             _s(None), _s(None), _s(None)],
        ]

    monkeypatch.setattr(TD, "_q", fake_q)
    out = TD.dispatch("get_morning_briefing", "tenant-1", {})
    hints = out["_artifact_hints"]
    assert len(hints) == 3
    assert hints[0]["kind"] == "kpi_card"
    assert hints[1]["kind"] == "severity_breakdown"
    assert hints[2]["kind"] == "kpi_card"
    assert hints[0]["severity"] == "high"
    assert out["_artifact_hint"] == hints[0]


# ---------------------------------------------------------------------------
# get_compliance_summary — donut + per-framework kpi
# ---------------------------------------------------------------------------

def test_get_compliance_summary_shape(monkeypatch):
    rows = [
        [_s("soc2"), _s("CC6.1"), _l(0), _l(2), _l(2)],
        [_s("soc2"), _s("CC6.2"), _l(1), _l(0), _l(1)],
    ]
    monkeypatch.setattr(TD, "_q", lambda sql, params=None: rows)

    out = TD.dispatch("get_compliance_summary", "tenant-1", {})
    assert out["_artifact_hint"]["kind"] == "chart_donut"
    hints = out["_artifact_hints"]
    assert hints[0]["kind"] == "chart_donut"
    assert hints[1]["kind"] == "kpi_card"
    assert hints[1]["label"] == "soc2"


# ---------------------------------------------------------------------------
# query_entities / get_entity — entity_list
# ---------------------------------------------------------------------------

def test_query_entities_returns_entity_list(monkeypatch):
    rows = [
        [_s("eid-1"), _s("aws_s3_bucket"), _s("github.com/x"), _s("my-bucket"),
         _s("cloud"), _s('{"source_path": "infra/s3.tf"}')],
    ]
    monkeypatch.setattr(TD, "_q", lambda sql, params=None: rows)

    out = TD.dispatch("query_entities", "tenant-1", {"domain": "cloud"})
    hint = out["_artifact_hint"]
    assert hint["kind"] == "entity_list"
    assert hint["title"] == "cloud entities"
    e0 = hint["entities"][0]
    assert e0["id"] == "eid-1"
    assert e0["source_path"] == "infra/s3.tf"
    assert e0["source"] == {"entity_id": "eid-1"}


def test_get_entity_missing_returns_null(monkeypatch):
    monkeypatch.setattr(TD, "_q", lambda sql, params=None: [])
    out = TD.dispatch("get_entity", "tenant-1", {"entity_id": "nope"})
    assert out["result"] is None


# ---------------------------------------------------------------------------
# Action tools — pending approval_card, NO mutation (no _q call)
# ---------------------------------------------------------------------------

def test_propose_risk_entry_returns_pending_card_without_mutating(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("propose_risk_entry must NOT touch the DB")

    monkeypatch.setattr(TD, "_q", boom)
    out = TD.dispatch("propose_risk_entry", "tenant-1",
                      {"title": "New risk", "severity": "high"})
    hint = out["_artifact_hint"]
    assert hint["kind"] == "approval_card"
    assert hint["action_kind"] == "add_risk"
    assert hint["current_status"] == "pending"
    assert hint["payload"]["title"] == "New risk"
    assert hint["payload"]["severity"] == "high"
    assert hint["payload"]["status"] == "open"
    assert hint["edit_fields"] == TD.ADD_RISK_EDIT_FIELDS


def test_propose_policy_draft_returns_pending_card_without_mutating(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("propose_policy_draft must NOT touch the DB")

    monkeypatch.setattr(TD, "_q", boom)
    out = TD.dispatch("propose_policy_draft", "tenant-1", {"name": "Access Policy"})
    hint = out["_artifact_hint"]
    assert hint["kind"] == "approval_card"
    assert hint["action_kind"] == "draft_policy"
    assert hint["current_status"] == "pending"
    assert hint["payload"]["name"] == "Access Policy"
    assert hint["payload"]["status"] == "draft"
    assert hint["edit_fields"] == TD.DRAFT_POLICY_EDIT_FIELDS


# ---------------------------------------------------------------------------
# Side-effect tools — intent only, no artifact hint
# ---------------------------------------------------------------------------

def test_navigate_to_returns_intent_only():
    out = TD.dispatch("navigate_to", "tenant-1", {"path": "/findings"})
    assert out["result"] == {"navigated_to": "/findings"}
    assert "_artifact_hint" not in out
    assert "_artifact_hints" not in out


def test_filter_findings_view_returns_intent_only():
    out = TD.dispatch("filter_findings_view", "tenant-1", {"severity": "critical"})
    assert out["result"] == {"filtered": {"severity": "critical"}}
    assert "_artifact_hint" not in out

    assert "_artifact_hints" not in out


# ---------------------------------------------------------------------------
# Tenant-scope regression -- ALL 8 data tools
#
# For each data tool: monkeypatch _q to capture every (sql, params) pair,
# dispatch the tool with a known tenant_id, then assert:
#   1. At least one SQL call was made.
#   2. EVERY SQL call contains the tenant-scoping token "CAST(:tid AS UUID)".
#   3. EVERY such call passes params["tid"] equal to the sentinel tenant_id.
#
# "Tenant-owned tables" = findings, risks, entities -- i.e. every SELECT
# the data tools produce. Action and side-effect tools have separate tests
# that assert _q is NEVER called.
#
# Update this test when a new data tool is added to TOOLS.
# ---------------------------------------------------------------------------

_TENANT_SCOPE_TOKEN = "CAST(:tid AS UUID)"
_SENTINEL_TENANT_ID = "tenant-SCOPE-TEST"

# Minimal fake rows so each query returns enough data to finish without error.
_FINDING_ROW = [
    _s("fid-1"), _s("chk.1"), _s("Test finding"), _s("desc"),
    _s("high"), _s("fail"), _s(None), _s("s3"),
    _s("us-east-1"), _s("cloud"), _s("{}"),
]
_SEVERITY_ROW = [_s("high"), _l(1)]
_RISK_ROW = [
    _s("rid-1"), _s("Risk title"), _s("desc"), _s("high"), _s("open"),
    _s(None), _s(None), _s(None),
]
_ENTITY_ROW = [
    _s("eid-1"), _s("aws_s3_bucket"), _s("nat-key"), _s("Display"),
    _s("cloud"), _s("{}"),
]
_COMPLIANCE_ROW = [_s("soc2"), _s("CC6.1"), _l(0), _l(1), _l(1)]


def _stubbed_q(sql, params=None):
    """Return plausible rows for every SQL pattern the data tools issue."""
    if "jsonb_each" in sql:          # compliance rollup
        return [_COMPLIANCE_ROW]
    if "GROUP BY f.severity" in sql:  # severity breakdown
        return [_SEVERITY_ROW]
    if "FROM findings f" in sql:      # findings queries
        return [_FINDING_ROW]
    if "FROM risks" in sql:           # risks query
        return [_RISK_ROW]
    if "FROM entities" in sql:        # entities queries
        return [_ENTITY_ROW]
    return []


@pytest.mark.parametrize("tool_name,args", [
    ("get_morning_briefing",    {}),
    ("query_entities",          {}),
    ("get_entity",              {"entity_id": "eid-1"}),
    ("query_findings",          {}),
    ("get_finding",             {"check_id": "chk.1"}),
    ("get_compliance_summary",  {}),
    ("get_severity_breakdown",  {}),
    ("list_risks",              {}),
])
def test_data_tool_is_tenant_scoped(tool_name, args, monkeypatch):
    """Every SQL query issued by a data tool must contain the tenant filter
    and must bind :tid to the caller-supplied tenant_id -- security boundary.

    Covers all 8 data tools. Fails immediately if any query is missing the
    CAST(:tid AS UUID) predicate or passes the wrong tenant_id.
    """
    captured_calls: list[tuple[str, dict]] = []

    def recording_q(sql, params=None):
        captured_calls.append((sql, params or {}))
        return _stubbed_q(sql, params)

    monkeypatch.setattr(TD, "_q", recording_q)

    TD.dispatch(tool_name, _SENTINEL_TENANT_ID, args)

    assert captured_calls, (
        f"{tool_name}: expected at least one _q call but got none"
    )

    for sql, params in captured_calls:
        msg_sql = (
            f"{tool_name}: SQL missing tenant scope token {_TENANT_SCOPE_TOKEN!r}. "
            f"SQL was: {sql}"
        )
        assert _TENANT_SCOPE_TOKEN in sql, msg_sql
        msg_tid = (
            f"{tool_name}: params['tid'] = {params.get('tid')!r}, "
            f"expected {_SENTINEL_TENANT_ID!r}. Full params: {params}"
        )
        assert params.get("tid") == _SENTINEL_TENANT_ID, msg_tid
