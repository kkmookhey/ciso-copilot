"""ti_lookup: pure dataclass + DB helpers that talk to Aurora via RDS Data API.

The Data-API client is monkeypatched in tests so the helpers exercise the
SQL construction without hitting the cluster.
"""
from __future__ import annotations

import datetime as dt

import ti_lookup


def _fake_data_api(records):
    """Build a Data-API stand-in that returns the canned records list."""
    class _Client:
        def __init__(self): self.calls = []
        def execute_statement(self, **kw):
            self.calls.append(kw)
            return {"records": records}
        def batch_execute_statement(self, **kw):
            self.calls.append(kw)
            return {"updateResults": [{} for _ in kw.get("parameterSets", [])]}
    return _Client()


def test_bulk_lookup_returns_matches_grouped_by_value(monkeypatch):
    client = _fake_data_api([
        [{"stringValue": "185.220.101.12"}, {"stringValue": "ip"},
         {"stringValue": "tor"},          {"isNull": True},
         {"stringValue": "[]"}],
        [{"stringValue": "185.220.101.12"}, {"stringValue": "ip"},
         {"stringValue": "abusech_feodo"},{"longValue": 80},
         {"stringValue": "[\"Heodo\"]"}],
    ])
    monkeypatch.setattr(ti_lookup, "rds_data", client)
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:test"); monkeypatch.setenv("DB_SECRET_ARN", "arn:test"); monkeypatch.setenv("DB_NAME", "ciso_copilot")
    ti_lookup._reload_env()

    matches = ti_lookup.bulk_lookup({"ip": ["185.220.101.12", "8.8.8.8"]})
    assert "185.220.101.12" in matches
    assert len(matches["185.220.101.12"]) == 2
    sources = {m["source"] for m in matches["185.220.101.12"]}
    assert sources == {"tor", "abusech_feodo"}
    # Confidence preserved as int or None
    confidences = {m["confidence"] for m in matches["185.220.101.12"]}
    assert confidences == {None, 80}
    # Misses don't appear as keys
    assert "8.8.8.8" not in matches


def test_bulk_lookup_empty_input_skips_db(monkeypatch):
    client = _fake_data_api([])
    monkeypatch.setattr(ti_lookup, "rds_data", client)
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:test"); monkeypatch.setenv("DB_SECRET_ARN", "arn:test"); monkeypatch.setenv("DB_NAME", "ciso_copilot")
    ti_lookup._reload_env()
    matches = ti_lookup.bulk_lookup({"ip": [], "domain": []})
    assert matches == {}
    assert client.calls == []


def test_upsert_indicators_uses_on_conflict(monkeypatch):
    client = _fake_data_api([])
    monkeypatch.setattr(ti_lookup, "rds_data", client)
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:test"); monkeypatch.setenv("DB_SECRET_ARN", "arn:test"); monkeypatch.setenv("DB_NAME", "ciso_copilot")
    ti_lookup._reload_env()
    now = dt.datetime(2026, 5, 25, 12, 0, 0, tzinfo=dt.timezone.utc)
    ind = ti_lookup.Indicator(
        value="185.220.101.12", kind="ip", source="tor",
        first_seen=now, last_seen=now, confidence=None, tags=["exit"], raw={},
    )
    ti_lookup.upsert_indicators([ind])

    assert len(client.calls) == 1
    sql = client.calls[0]["sql"]
    assert "INSERT INTO threat_indicators" in sql
    assert "ON CONFLICT (indicator_value, kind, source) DO UPDATE" in sql
    assert "last_seen" in sql
