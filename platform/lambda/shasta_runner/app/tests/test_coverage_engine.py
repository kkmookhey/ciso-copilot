# app/tests/test_coverage_engine.py
"""The engine runs collectors + tier-filtered checks and emits
entities, edges, and findings."""
from coverage import engine
from coverage.model import Resource


class _FakeSession:
    """Stands in for a boto3 Session — .client(name) is never actually
    used because we monkeypatch the collectors."""
    def client(self, name):
        return f"client:{name}"


def _make_session(region):
    return _FakeSession()


def test_engine_emits_entities_edges_findings(monkeypatch):
    # One SQS queue, unencrypted → the quick-tier encryption check fails.
    def fake_sqs_collect(client, *, account_id, region):
        return [Resource(service="sqs", resource_type="queue",
                         arn="arn:aws:sqs:us-east-1:111:q1", name="q1",
                         region=region, raw={})]
    monkeypatch.setitem(engine.COLLECTORS, "sqs", fake_sqs_collect)

    result = engine.run_coverage(
        _make_session, account_id="111", tenant_id="tnt-1",
        regions=["us-east-1"], scan_tier="quick",
    )

    queue_entities = [e for e in result["entities"] if e.kind == "aws_sqs_queue"]
    assert len(queue_entities) == 1
    assert queue_entities[0].natural_key == "arn:aws:sqs:us-east-1:111:q1"
    assert queue_entities[0].domain == "cloud"

    contains = [e for e in result["edges"] if e.kind == "contains"]
    assert any(e.target_natural_key == "arn:aws:sqs:us-east-1:111:q1"
               for e in contains)

    enc = [f for f in result["findings"]
           if f.finding_type == "sqs-encryption-at-rest"]
    assert len(enc) == 1
    assert enc[0].status == "fail"
    assert enc[0].subject_entity_kind == "aws_sqs_queue"
    assert enc[0].region == "us-east-1"
    assert enc[0].domain == "encryption"


def test_engine_respects_scan_tier(monkeypatch):
    def fake_sqs_collect(client, *, account_id, region):
        return [Resource(service="sqs", resource_type="queue",
                         arn="arn:aws:sqs:us-east-1:111:q1", name="q1",
                         region=region, raw={})]
    monkeypatch.setitem(engine.COLLECTORS, "sqs", fake_sqs_collect)
    # Stub the other services' collectors so a medium-tier run does not
    # hit the real collectors with a fake client.
    monkeypatch.setitem(engine.COLLECTORS, "secretsmanager",
                        lambda client, *, account_id, region: [])
    monkeypatch.setitem(engine.COLLECTORS, "ecr",
                        lambda client, *, account_id, region: [])

    quick = engine.run_coverage(
        _make_session, account_id="111", tenant_id="t",
        regions=["us-east-1"], scan_tier="quick")
    medium = engine.run_coverage(
        _make_session, account_id="111", tenant_id="t",
        regions=["us-east-1"], scan_tier="medium")

    quick_checks = {f.finding_type for f in quick["findings"]}
    medium_checks = {f.finding_type for f in medium["findings"]}
    # sqs-dlq-configured is medium-tier — present at medium, absent at quick.
    assert "sqs-dlq-configured" not in quick_checks
    assert "sqs-dlq-configured" in medium_checks


def test_engine_survives_a_failing_collector(monkeypatch):
    def boom(client, *, account_id, region):
        raise RuntimeError("access denied")
    monkeypatch.setitem(engine.COLLECTORS, "sqs", boom)

    # Should not raise — the bad collector is caught and skipped.
    result = engine.run_coverage(
        _make_session, account_id="111", tenant_id="t",
        regions=["us-east-1"], scan_tier="quick")
    assert "findings" in result
