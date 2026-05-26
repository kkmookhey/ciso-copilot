"""Test that source_event_id is extracted per source and that dedupe SQL is emitted."""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


def test_source_event_id_cloudtrail(cloudtrail_sg_open_event):
    """CloudTrail events get source_event_id = detail.eventID."""
    sei = main._source_event_id(cloudtrail_sg_open_event)
    assert sei == "ct-eventid-7f3a9c"


def test_source_event_id_config(config_item_change_event):
    """Config events get source_event_id = configurationItemCaptureTime + resourceId."""
    sei = main._source_event_id(config_item_change_event)
    assert sei == "2026-05-25T18:43:14.123Z:sg-0abc123def"


def test_source_event_id_unknown_returns_none():
    """Unknown source falls back to None (legacy path; INSERT without dedupe)."""
    evt = {"source": "unknown", "detail": {}}
    assert main._source_event_id(evt) is None


def test_insert_event_emits_on_conflict_do_nothing(monkeypatch):
    """_insert_event uses ON CONFLICT (tenant_id, source, source_event_id) DO NOTHING."""
    captured = {}
    class FakeRdsData:
        def execute_statement(self, **kwargs):
            captured["sql"]    = kwargs["sql"]
            captured["params"] = kwargs["parameters"]
            return {"records": []}
    monkeypatch.setattr(main, "rds_data", FakeRdsData())

    main._insert_event(
        event_id="e1", tenant_id="t1", conn_id="c1",
        kind="drift", source="aws.cloudtrail", severity="high",
        title="SG opened", description=None, resource_arn="sg-1", actor="user/x",
        raw_s3_key="raw/2026/05/25/t1/aws.cloudtrail/e1.json",
        normalized={"x": 1}, fired_at="2026-05-25T18:42:10Z",
        source_event_id="ct-eventid-7f3a9c",
        source_ip=None,
    )

    assert "ON CONFLICT" in captured["sql"]
    assert "DO NOTHING" in captured["sql"]
    names = {p["name"] for p in captured["params"]}
    assert "sei" in names
