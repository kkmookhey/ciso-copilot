"""scan_state writes scan status/phase/stats and the coverage map to the
`scans` table via the Aurora Data API. The rds-data client and DB config
are isolated here with a fake so the SQL/params can be asserted."""
import json

import pytest

import scan_state


class FakeRds:
    """Records execute_statement calls."""
    def __init__(self):
        self.calls = []

    def execute_statement(self, **kwargs):
        self.calls.append(kwargs)
        return {}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    fake = FakeRds()
    monkeypatch.setattr(scan_state, "_rds", fake)
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:cluster")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:secret")
    monkeypatch.setenv("DB_NAME", "ciso_copilot")
    return fake


def _params(call):
    """execute_statement parameters list -> {name: value-dict}."""
    return {p["name"]: p["value"] for p in call["parameters"]}


def test_update_scan_status_only(_isolate):
    scan_state.update_scan("scan-1", "running")
    call = _isolate.calls[-1]
    assert call["resourceArn"] == "arn:cluster"
    assert call["secretArn"] == "arn:secret"
    assert call["database"] == "ciso_copilot"
    assert call["sql"] == (
        "UPDATE scans SET status = :status "
        "WHERE scan_id = CAST(:sid AS UUID)")
    p = _params(call)
    assert p["sid"] == {"stringValue": "scan-1"}
    assert p["status"] == {"stringValue": "running"}


def test_update_scan_with_phase(_isolate):
    scan_state.update_scan("scan-1", "running", phase="first_signal")
    call = _isolate.calls[-1]
    assert "phase = :phase" in call["sql"]
    assert _params(call)["phase"] == {"stringValue": "first_signal"}


def test_update_scan_terminal_status_sets_finished_at(_isolate):
    scan_state.update_scan("scan-1", "completed", phase="done")
    sql = _isolate.calls[-1]["sql"]
    assert "finished_at = now()" in sql


def test_update_scan_running_does_not_set_finished_at(_isolate):
    scan_state.update_scan("scan-1", "running")
    assert "finished_at" not in _isolate.calls[-1]["sql"]


def test_update_scan_with_stats(_isolate):
    scan_state.update_scan("scan-1", "completed", stats={"findings": 7})
    call = _isolate.calls[-1]
    assert "stats = CAST(:stats AS JSONB)" in call["sql"]
    assert json.loads(_params(call)["stats"]["stringValue"]) == {"findings": 7}


def test_update_scan_with_error(_isolate):
    scan_state.update_scan("scan-1", "failed", error="boom")
    call = _isolate.calls[-1]
    assert "error = :error" in call["sql"]
    assert _params(call)["error"] == {"stringValue": "boom"}


def test_record_scan_scope_writes_passed_dict(_isolate):
    scope = {"tier": "quick", "regions": {"us-east-1": {"state": "active"}}}
    scan_state.record_scan_scope("scan-1", scope)
    call = _isolate.calls[-1]
    assert call["sql"] == (
        "UPDATE scans SET scope = CAST(:scope AS JSONB) "
        "WHERE scan_id = CAST(:sid AS UUID)")
    p = _params(call)
    assert p["sid"] == {"stringValue": "scan-1"}
    assert json.loads(p["scope"]["stringValue"]) == scope
