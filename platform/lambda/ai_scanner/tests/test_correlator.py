"""Unit tests for the cross-detector correlator."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("GITHUB_APP_SECRET_ARN", "arn:test")


@pytest.fixture
def ctx():
    from scan_runner import ScanContext
    return ScanContext(
        scan_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        connection_id="33333333-3333-3333-3333-333333333333",
        repo_asset_id="44444444-4444-4444-4444-444444444444",
        repo_full_name="fixture/repo",
        default_branch="main",
        head_commit_sha="fixture-sha",
        installation_id=0,
        repo_workdir=Path("/tmp/unused"),
    )


def _asset(asset_type: str, name: str, source_path: str):
    from detectors.base import AssetEmission
    return AssetEmission(
        tenant_id="22222222-2222-2222-2222-222222222222",
        connection_id="33333333-3333-3333-3333-333333333333",
        asset_type=asset_type, name=name,
        source_repo_id="44444444-4444-4444-4444-444444444444",
        source_path=source_path,
        attributes={}, evidence_packet={"version": "0.1"},
        detector_id="test", detector_version="0.1.0",
    )


def _result(assets):
    from detectors.base import DetectorResult
    return DetectorResult(assets=assets, relationships=[], findings=[])


def test_agent_plus_mcp_emits_invokes(ctx):
    from detectors import correlator
    out = correlator.correlate(ctx, [
        _result([_asset("agent", "run_agent", "app/agent.py"),
                  _asset("mcp_server", "kk-tools", "app/agent.py")]),
    ])
    assert len(out.relationships) == 1
    edge = out.relationships[0]
    assert edge.relationship_type == "invokes"
    assert "run_agent" in edge.source_asset_ref
    assert "kk-tools" in edge.target_asset_ref


def test_agent_plus_model_emits_orchestrates(ctx):
    from detectors import correlator
    out = correlator.correlate(ctx, [
        _result([_asset("agent", "run_agent", "app/agent.py")]),
        _result([_asset("model", "openai/gpt-4o-mini", "app/agent.py")]),
    ])
    rt = [r.relationship_type for r in out.relationships]
    assert "orchestrates" in rt


def test_rag_triple_emits_retrieves(ctx):
    from detectors import correlator
    out = correlator.correlate(ctx, [
        _result([
            _asset("model", "openai/gpt-4o-mini", "app/rag.py"),
            _asset("vector_db", "chromadb", "app/rag.py"),
            _asset("prompt", "app/rag.py::system@10", "app/rag.py"),
        ]),
    ])
    rt = [r.relationship_type for r in out.relationships]
    assert "retrieves" in rt


def test_no_colocation_no_edges(ctx):
    from detectors import correlator
    out = correlator.correlate(ctx, [
        _result([
            _asset("agent", "run_agent", "app/agent.py"),
            _asset("mcp_server", "kk-tools", "tools/server.py"),
        ]),
    ])
    assert out.relationships == []
