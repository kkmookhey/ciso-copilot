# platform/lambda/ai_scanner/tests/test_unified_writer.py
"""Tests for unified_writer — transactional semantics, ON CONFLICT
RETURNING, stub support, and repo-root pre-seed regression."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:db")
    monkeypatch.setenv("DB_SECRET_ARN",  "arn:secret")
    monkeypatch.setenv("DB_NAME",        "ciso_copilot")
    # scan_runner reads GITHUB_APP_SECRET_ARN at import time.
    monkeypatch.setenv("GITHUB_APP_SECRET_ARN", "arn:fake")
    import boto3
    monkeypatch.setattr(boto3, "client", lambda _n, **_kw: MagicMock())


def _ctx():
    from scan_runner import ScanContext
    return ScanContext(
        scan_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        connection_id="33333333-3333-3333-3333-333333333333",
        repo_asset_id="44444444-4444-4444-4444-444444444444",
        repo_full_name="kk/foo", default_branch="main",
        head_commit_sha="abc123", installation_id=1,
        repo_workdir=Path("/tmp/x"),
    )


def _stub_rds(monkeypatch, persisted_id="aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa"):
    """Stub Aurora Data API to return a deterministic id on every UPSERT
    so the writer's resolution map is predictable."""
    import unified_writer
    fake = MagicMock()
    fake.begin_transaction = lambda **kw: {"transactionId": "tx"}
    fake.commit_transaction = MagicMock()
    fake.rollback_transaction = MagicMock()
    calls = []
    def fake_execute(**kw):
        calls.append(kw)
        return {"records": [[{"stringValue": persisted_id}]]}
    fake.execute_statement = fake_execute
    monkeypatch.setattr(unified_writer, "_rds", fake)
    return fake, calls


def test_entity_upsert_returns_persisted_id(monkeypatch):
    """The writer must use the id returned by ON CONFLICT RETURNING — not
    the assigned UUID — so edges and findings get the correct FK."""
    import unified_writer
    from detectors.base import EntityEmission
    fake, calls = _stub_rds(monkeypatch, persisted_id="exists-already-id")

    e = EntityEmission(
        tenant_id="t1", kind="ai_framework", natural_key="langchain",
        display_name="langchain", domain="ai",
        attributes={"imports_seen": 2}, evidence_packet={"version": "0.1"},
        detector_id="ai.detectors.framework", detector_version="0.2.0",
    )
    unified_writer.commit_scan(_ctx(), entities=[e], edges=[], findings=[])

    # Last call should be the ai_scans UPDATE, before that the entity upsert
    upsert_calls = [c for c in calls if "INSERT INTO entities" in (c.get("sql") or "")]
    assert len(upsert_calls) == 1
    assert "RETURNING id::text" in upsert_calls[0]["sql"]


def test_edge_resolves_against_entity_emitted_in_same_scan(monkeypatch):
    import unified_writer
    from detectors.base import EntityEmission, EdgeEmission
    fake, calls = _stub_rds(monkeypatch)

    repo = EntityEmission(
        tenant_id="t1", kind="github_repo", natural_key="github.com/kk/foo",
        display_name="kk/foo", domain="repo",
        attributes={}, evidence_packet=None,
        detector_id="manual.repo_attach", detector_version="0.1.0",
    )
    fw = EntityEmission(
        tenant_id="t1", kind="ai_framework", natural_key="langchain",
        display_name="langchain", domain="ai",
        attributes={}, evidence_packet={"version": "0.1"},
        detector_id="ai.detectors.framework", detector_version="0.2.0",
    )
    edge = EdgeEmission(
        tenant_id="t1",
        source_kind="github_repo", source_natural_key="github.com/kk/foo",
        target_kind="ai_framework", target_natural_key="langchain",
        kind="uses", attributes={}, evidence_packet={"version": "0.1"},
        detector_id="ai.detectors.framework", detector_version="0.2.0",
    )
    unified_writer.commit_scan(_ctx(), entities=[repo, fw], edges=[edge], findings=[])

    edge_calls = [c for c in calls if "INSERT INTO edges" in (c.get("sql") or "")]
    assert len(edge_calls) == 1, "edge was dropped — resolution failed"


def test_cross_scan_edge_creates_stub_entity(monkeypatch):
    """When an edge points at an entity the current scan did NOT emit,
    the writer should create a stub entity and link to it (so cross-domain
    edges work the day they're emitted, even before the other scanner runs)."""
    import unified_writer
    from detectors.base import EntityEmission, EdgeEmission
    fake, calls = _stub_rds(monkeypatch)

    repo = EntityEmission(
        tenant_id="t1", kind="github_repo", natural_key="github.com/kk/foo",
        display_name="kk/foo", domain="repo",
        attributes={}, evidence_packet=None,
        detector_id="manual.repo_attach", detector_version="0.1.0",
    )
    edge = EdgeEmission(
        tenant_id="t1",
        source_kind="github_repo", source_natural_key="github.com/kk/foo",
        target_kind="aws_iam_role",
        target_natural_key="arn:aws:iam::999999999999:role/Deploy",
        kind="deploys_to", attributes={}, evidence_packet={"version": "0.1"},
        detector_id="ai.detectors.crossdomain", detector_version="0.1.0",
    )
    unified_writer.commit_scan(_ctx(), entities=[repo], edges=[edge], findings=[])

    stub_calls = [c for c in calls
                  if "INSERT INTO entities" in (c.get("sql") or "")
                  and any(p.get("name") == "stub" and p.get("value", {}).get("booleanValue") is True
                          for p in c.get("parameters") or [])]
    assert len(stub_calls) == 1, "stub entity was not created for the cross-domain target"


def test_rollback_on_error(monkeypatch):
    import unified_writer
    from detectors.base import EntityEmission
    fake = MagicMock()
    fake.begin_transaction = lambda **kw: {"transactionId": "tx"}
    fake.commit_transaction = MagicMock()
    fake.rollback_transaction = MagicMock()
    def boom(**kw): raise RuntimeError("boom")
    fake.execute_statement = boom
    monkeypatch.setattr(unified_writer, "_rds", fake)

    e = EntityEmission(
        tenant_id="t1", kind="ai_framework", natural_key="x",
        display_name="x", domain="ai", attributes={}, evidence_packet={},
        detector_id="d", detector_version="0.1",
    )
    with pytest.raises(RuntimeError, match="boom"):
        unified_writer.commit_scan(_ctx(), entities=[e], edges=[], findings=[])

    fake.rollback_transaction.assert_called_once()
    fake.commit_transaction.assert_not_called()


def test_insert_finding_persists_frameworks(monkeypatch):
    """unified_writer must write FindingEmission.frameworks into the
    findings.frameworks column — compliance_summary rolls that column up."""
    import unified_writer
    from detectors.base import FindingEmission
    _fake, calls = _stub_rds(monkeypatch)

    f = FindingEmission(
        tenant_id="t1",
        finding_type="bedrock-guardrails-configured",
        severity="medium",
        title="Bedrock guardrails not configured",
        description="No guardrails found on the account.",
        subject_entity_kind=None,
        subject_entity_natural_key=None,
        subject_type=None,
        subject_ref=None,
        evidence_packet={"version": "0.1"},
        confidence="high",
        frameworks={"nist_ai_rmf": ["MANAGE-2"], "iso_42001": ["AI-8.3"]},
    )
    unified_writer.commit_scan(_ctx(), entities=[], edges=[], findings=[f])

    finding_calls = [c for c in calls if "INSERT INTO findings" in (c.get("sql") or "")]
    assert len(finding_calls) == 1
    params = {p["name"]: p["value"] for p in finding_calls[0]["parameters"]}
    assert json.loads(params["fw"]["stringValue"]) == {
        "nist_ai_rmf": ["MANAGE-2"],
        "iso_42001":   ["AI-8.3"],
    }


def test_nullable_cast_params_are_typed_not_case_is_null(monkeypatch):
    """Regression: a typeless-NULL Data API param used in `CASE WHEN :x IS
    NULL` triggers Postgres error 42P18 ('could not determine data type').
    Nullable cast columns — evidence_packet on entities, subject_entity_id
    on findings — must use a plain typed CAST so a NULL parameter still
    carries a determinable type."""
    import unified_writer
    from detectors.base import EntityEmission, FindingEmission
    _fake, calls = _stub_rds(monkeypatch)

    entity_no_ev = EntityEmission(
        tenant_id="t1", kind="aws_account", natural_key="111122223333",
        display_name="111122223333", domain="cloud", attributes={},
        evidence_packet=None,
        detector_id="shasta_runner.account", detector_version="0.1.0",
    )
    finding_no_subject = FindingEmission(
        tenant_id="t1", finding_type="x", severity="medium",
        title="t", description="d",
        subject_entity_kind=None, subject_entity_natural_key=None,
        subject_type=None, subject_ref=None,
        evidence_packet={"version": "0.1"}, confidence="high",
    )
    unified_writer.commit_scan(_ctx(), entities=[entity_no_ev], edges=[],
                               findings=[finding_no_subject])

    entity_sql  = next(c["sql"] for c in calls if "INSERT INTO entities" in (c.get("sql") or ""))
    finding_sql = next(c["sql"] for c in calls if "INSERT INTO findings" in (c.get("sql") or ""))
    assert "CASE WHEN :ev IS NULL"  not in entity_sql
    assert "CAST(:ev AS JSONB)"     in entity_sql
    assert "CASE WHEN :sid IS NULL" not in entity_sql
    assert "CASE WHEN :eid IS NULL" not in finding_sql
    assert "CAST(:eid AS UUID)"     in finding_sql


def test_insert_finding_writes_real_domain_status_and_upserts(monkeypatch):
    """_insert_finding must persist the finding's real domain/status/region
    (not hardcoded 'ai'/'fail') and UPSERT on the natural key so re-scans
    refresh rather than accumulate."""
    import unified_writer
    from detectors.base import FindingEmission
    _fake, calls = _stub_rds(monkeypatch)

    f = FindingEmission(
        tenant_id="t1", finding_type="iam-overbroad", severity="high",
        title="t", description="d",
        subject_entity_kind=None, subject_entity_natural_key=None,
        subject_type="iam-user", subject_ref="arn:aws:iam::1:user/x",
        evidence_packet={"version": "0.1"}, confidence="high",
        domain="iam", status="partial", region="us-west-2",
    )
    unified_writer.commit_scan(_ctx(), entities=[], edges=[], findings=[f])

    fc = next(c for c in calls if "INSERT INTO findings" in (c.get("sql") or ""))
    assert "ON CONFLICT" in fc["sql"]
    assert "'ai'"   not in fc["sql"]   # domain no longer hardcoded
    assert "'fail'" not in fc["sql"]   # status no longer hardcoded
    params = {p["name"]: p["value"] for p in fc["parameters"]}
    assert params["domain"]["stringValue"] == "iam"
    assert params["status"]["stringValue"] == "partial"
    assert params["region"]["stringValue"] == "us-west-2"


def test_critical_fail_triggers_fanout(monkeypatch):
    """End-to-end: when commit_scan writes a critical-fail finding, the
    broadcast_fanout hook publishes to SQS."""
    from unittest.mock import MagicMock
    from _shared import broadcast_fanout as bf

    fake_sqs = MagicMock()
    monkeypatch.setenv("AUTONOMOUS_BROADCAST_QUEUE_URL",
                       "https://sqs.us-east-1.amazonaws.com/000000000000/q")
    monkeypatch.setattr(bf, "_sqs", fake_sqs)

    import unified_writer
    from detectors.base import FindingEmission
    _fake, _calls = _stub_rds(monkeypatch)

    f = FindingEmission(
        tenant_id="t1",
        finding_type="critical_test",
        title="t",
        description="d",
        severity="critical",
        status="fail",
        subject_entity_kind=None,
        subject_entity_natural_key=None,
        subject_type=None,
        subject_ref=None,
        evidence_packet={},
        confidence="high",
    )

    unified_writer.commit_scan(_ctx(), entities=[], edges=[], findings=[f])

    fake_sqs.send_message.assert_called_once()
    call_kwargs = fake_sqs.send_message.call_args[1]
    assert call_kwargs["QueueUrl"] == "https://sqs.us-east-1.amazonaws.com/000000000000/q"
    import json as _json
    body = _json.loads(call_kwargs["MessageBody"])
    assert body["tenant_id"] == "t1"


def test_broadcast_fires_after_commit(monkeypatch):
    """Regression: the broadcast must publish AFTER commit_transaction. If it
    fires inside the open tx, the subscriber's `SELECT WHERE finding_id = :fid`
    races the commit and silently early-returns (subscriber main.py:92). See
    the 2026-06-02 GCP smoke that surfaced this — one of two critical-fail
    findings made it to Slack, the other was dropped on the in-tx race."""
    from unittest.mock import MagicMock, call as mock_call
    from _shared import broadcast_fanout as bf

    fake_sqs = MagicMock()
    monkeypatch.setenv("AUTONOMOUS_BROADCAST_QUEUE_URL",
                       "https://sqs.us-east-1.amazonaws.com/000000000000/q")
    monkeypatch.setattr(bf, "_sqs", fake_sqs)

    import unified_writer
    from detectors.base import FindingEmission
    fake_rds, _calls = _stub_rds(monkeypatch)

    # Attach commit_transaction and send_message to one parent mock so we can
    # assert ordering across them via mock_calls.
    parent = MagicMock()
    parent.attach_mock(fake_rds.commit_transaction, "commit_transaction")
    parent.attach_mock(fake_sqs.send_message,       "send_message")

    f = FindingEmission(
        tenant_id="t1", finding_type="critical_test", title="t", description="d",
        severity="critical", status="fail",
        subject_entity_kind=None, subject_entity_natural_key=None,
        subject_type=None, subject_ref=None,
        evidence_packet={}, confidence="high",
    )
    unified_writer.commit_scan(_ctx(), entities=[], edges=[], findings=[f])

    names = [c[0] for c in parent.mock_calls]
    assert "commit_transaction" in names, "commit must have been called"
    assert "send_message"       in names, "broadcast must have been called"
    assert names.index("commit_transaction") < names.index("send_message"), (
        f"commit must precede broadcast, got call order: {names}"
    )


def test_broadcast_uses_persisted_finding_id_on_conflict(monkeypatch):
    """Regression: on ON CONFLICT, the broadcast must carry the DB-persisted
    finding_id, not the freshly-generated client UUID. Subscriber looks up
    the row by this id and silently no-ops on miss (subscriber main.py:92),
    so a mismatched id == a dropped Slack card on repeat scans.

    Stub Aurora to return a fixed persisted id; verify the SQS MessageBody
    carries that id, not the random uuid the writer minted."""
    from unittest.mock import MagicMock
    from _shared import broadcast_fanout as bf

    fake_sqs = MagicMock()
    monkeypatch.setenv("AUTONOMOUS_BROADCAST_QUEUE_URL",
                       "https://sqs.us-east-1.amazonaws.com/000000000000/q")
    monkeypatch.setattr(bf, "_sqs", fake_sqs)

    import unified_writer
    from detectors.base import FindingEmission
    persisted_id = "deadbeef-1234-5678-9abc-def012345678"
    _fake, _calls = _stub_rds(monkeypatch, persisted_id=persisted_id)

    f = FindingEmission(
        tenant_id="t1", finding_type="critical_test", title="t", description="d",
        severity="critical", status="fail",
        subject_entity_kind=None, subject_entity_natural_key=None,
        subject_type=None, subject_ref=None,
        evidence_packet={}, confidence="high",
    )
    unified_writer.commit_scan(_ctx(), entities=[], edges=[], findings=[f])

    fake_sqs.send_message.assert_called_once()
    import json as _json
    body = _json.loads(fake_sqs.send_message.call_args[1]["MessageBody"])
    assert body["finding_id"] == persisted_id, (
        f"broadcast must carry persisted id, got {body['finding_id']}"
    )
    # And the SQL itself must contain RETURNING — the load-bearing change.
    finding_sql = next(
        c["sql"] for c in _calls if "INSERT INTO findings" in (c.get("sql") or "")
    )
    assert "RETURNING finding_id::text" in finding_sql


def test_broadcast_does_not_fire_on_rollback(monkeypatch):
    """If commit_scan rolls back (any in-tx error), pending broadcasts must
    NOT fire — we don't broadcast findings that never made it to DB."""
    from unittest.mock import MagicMock
    from _shared import broadcast_fanout as bf

    fake_sqs = MagicMock()
    monkeypatch.setenv("AUTONOMOUS_BROADCAST_QUEUE_URL",
                       "https://sqs.us-east-1.amazonaws.com/000000000000/q")
    monkeypatch.setattr(bf, "_sqs", fake_sqs)

    import unified_writer
    from detectors.base import FindingEmission
    fake_rds, _calls = _stub_rds(monkeypatch)
    fake_rds.commit_transaction.side_effect = RuntimeError("aurora unavailable")

    f = FindingEmission(
        tenant_id="t1", finding_type="critical_test", title="t", description="d",
        severity="critical", status="fail",
        subject_entity_kind=None, subject_entity_natural_key=None,
        subject_type=None, subject_ref=None,
        evidence_packet={}, confidence="high",
    )
    with pytest.raises(RuntimeError, match="aurora unavailable"):
        unified_writer.commit_scan(_ctx(), entities=[], edges=[], findings=[f])

    fake_sqs.send_message.assert_not_called()
    fake_rds.rollback_transaction.assert_called_once()


def test_non_critical_does_not_trigger_fanout(monkeypatch):
    """Findings with severity != critical (or status != fail) must not publish."""
    from unittest.mock import MagicMock
    from _shared import broadcast_fanout as bf

    fake_sqs = MagicMock()
    monkeypatch.setenv("AUTONOMOUS_BROADCAST_QUEUE_URL",
                       "https://sqs.us-east-1.amazonaws.com/000000000000/q")
    monkeypatch.setattr(bf, "_sqs", fake_sqs)

    import unified_writer
    from detectors.base import FindingEmission
    _fake, _calls = _stub_rds(monkeypatch)

    f = FindingEmission(
        tenant_id="t1",
        finding_type="medium_test",
        title="t",
        description="d",
        severity="medium",
        status="fail",
        subject_entity_kind=None,
        subject_entity_natural_key=None,
        subject_type=None,
        subject_ref=None,
        evidence_packet={},
        confidence="high",
    )

    unified_writer.commit_scan(_ctx(), entities=[], edges=[], findings=[f])

    fake_sqs.send_message.assert_not_called()
