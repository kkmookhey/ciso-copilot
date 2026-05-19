# platform/lambda/ai_scanner/tests/test_writer.py
"""Tests for the transactional writer."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:db")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:secret")
    monkeypatch.setenv("DB_NAME", "ciso_copilot")
    # scan_runner reads this at module-level; must be set before first import
    monkeypatch.setenv("GITHUB_APP_SECRET_ARN", "arn:fake")
    import boto3
    monkeypatch.setattr(boto3, "client", lambda _n, **_kw: MagicMock())


def _ctx():
    from scan_runner import ScanContext
    from pathlib import Path
    return ScanContext(
        scan_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        connection_id="33333333-3333-3333-3333-333333333333",
        repo_asset_id="44444444-4444-4444-4444-444444444444",
        repo_full_name="kk/foo", default_branch="main",
        head_commit_sha="abc123", installation_id=1,
        repo_workdir=Path("/tmp/x"),
    )


def test_commit_scan_runs_transactional_writes(monkeypatch):
    import writer
    calls: list[dict] = []
    tx_id = "txid-fake"

    def fake_call(method, **kw):
        calls.append({"method": method, **kw})
        if method == "begin_transaction":
            return {"transactionId": tx_id}
        return {"records": []}

    fake_client = MagicMock()
    fake_client.begin_transaction = lambda **kw: fake_call("begin_transaction", **kw)
    fake_client.commit_transaction = lambda **kw: fake_call("commit_transaction", **kw)
    fake_client.rollback_transaction = lambda **kw: fake_call("rollback_transaction", **kw)
    fake_client.execute_statement = lambda **kw: fake_call("execute_statement", **kw)
    fake_client.batch_execute_statement = lambda **kw: fake_call("batch_execute_statement", **kw)
    monkeypatch.setattr(writer, "_rds", fake_client)

    from detectors.base import AssetEmission, RelEmission, FindingEmission
    asset = AssetEmission(
        tenant_id="22222222-2222-2222-2222-222222222222",
        connection_id="33333333-3333-3333-3333-333333333333",
        asset_type="framework",
        name="langchain",
        source_repo_id="44444444-4444-4444-4444-444444444444",
        source_path="src/agent.py",
        attributes={"version": ">=0.3"},
        evidence_packet={"version": "0.1"},
        detector_id="ai.detectors.framework",
        detector_version="0.1.0",
    )
    writer.commit_scan(_ctx(), assets=[asset], relationships=[], findings=[])

    methods = [c["method"] for c in calls]
    assert methods[0]  == "begin_transaction"
    assert "execute_statement" in methods
    assert methods[-1] == "commit_transaction"


def test_commit_scan_rolls_back_on_error(monkeypatch):
    import writer

    fake_client = MagicMock()
    fake_client.begin_transaction = lambda **kw: {"transactionId": "tx"}
    fake_client.commit_transaction = MagicMock()
    fake_client.rollback_transaction = MagicMock()
    def fake_execute(**kw):
        raise RuntimeError("boom")
    fake_client.execute_statement = fake_execute
    fake_client.batch_execute_statement = fake_execute
    monkeypatch.setattr(writer, "_rds", fake_client)

    from detectors.base import AssetEmission
    asset = AssetEmission(
        tenant_id="t", connection_id="c", asset_type="framework", name="x",
        source_repo_id="r", source_path="/p", attributes={}, evidence_packet={},
        detector_id="d", detector_version="0.1",
    )

    with pytest.raises(RuntimeError, match="boom"):
        writer.commit_scan(_ctx(), assets=[asset], relationships=[], findings=[])

    fake_client.rollback_transaction.assert_called_once()
    fake_client.commit_transaction.assert_not_called()
