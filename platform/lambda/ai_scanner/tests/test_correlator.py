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


def _entity(kind: str, natural_key: str, display_name: str, source_path: str):
    from detectors.base import EntityEmission
    return EntityEmission(
        tenant_id="22222222-2222-2222-2222-222222222222",
        kind=kind, natural_key=natural_key,
        display_name=display_name, domain="ai",
        attributes={}, evidence_packet={"version": "0.1"},
        detector_id="test", detector_version="0.2.0",
        connection_id="33333333-3333-3333-3333-333333333333",
        source_path=source_path,
    )


def _result(entities):
    from detectors.base import DetectorResult
    return DetectorResult(entities=entities, edges=[], findings=[])


def test_agent_plus_mcp_emits_invokes(ctx):
    from detectors import correlator
    out = correlator.correlate(ctx, [
        _result([
            _entity("ai_agent",      "github.com/fixture/repo::app/agent.py::run_agent",
                    "run_agent", "app/agent.py"),
            _entity("ai_mcp_server", "github.com/fixture/repo::app/agent.py::kk-tools",
                    "kk-tools",  "app/agent.py"),
        ]),
    ])
    assert len(out.edges) == 1
    edge = out.edges[0]
    assert edge.kind == "invokes"
    assert edge.source_kind == "ai_agent"
    assert "run_agent" in edge.source_natural_key
    assert edge.target_kind == "ai_mcp_server"
    assert "kk-tools" in edge.target_natural_key


def test_agent_plus_model_emits_orchestrates(ctx):
    from detectors import correlator
    out = correlator.correlate(ctx, [
        _result([_entity("ai_agent",
                          "github.com/fixture/repo::app/agent.py::run_agent",
                          "run_agent", "app/agent.py")]),
        _result([_entity("ai_model", "openai/gpt-4o-mini", "openai/gpt-4o-mini",
                          "app/agent.py")]),
    ])
    kinds = [e.kind for e in out.edges]
    assert "orchestrates" in kinds


def test_rag_triple_emits_retrieves(ctx):
    from detectors import correlator
    out = correlator.correlate(ctx, [
        _result([
            _entity("ai_model",     "openai/gpt-4o-mini", "openai/gpt-4o-mini", "app/rag.py"),
            _entity("ai_vector_db", "chromadb", "chromadb", "app/rag.py"),
            _entity("ai_prompt",    "github.com/fixture/repo::app/rag.py::system@10",
                    "system@10", "app/rag.py"),
        ]),
    ])
    kinds = [e.kind for e in out.edges]
    assert "retrieves" in kinds


def test_no_colocation_no_edges(ctx):
    from detectors import correlator
    out = correlator.correlate(ctx, [
        _result([
            _entity("ai_agent",      "github.com/fixture/repo::app/agent.py::run_agent",
                    "run_agent", "app/agent.py"),
            _entity("ai_mcp_server", "github.com/fixture/repo::tools/server.py::kk-tools",
                    "kk-tools",  "tools/server.py"),
        ]),
    ])
    assert out.edges == []
