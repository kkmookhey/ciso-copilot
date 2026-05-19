"""Cross-detector correlator. Adds derived relationships after the eight
deterministic detectors have all run.

Patterns:
  - ``agent`` + ``mcp_server`` co-located in the same file
      → ``agent → invokes → mcp_server``
  - ``agent`` + ``model`` co-located
      → ``agent → orchestrates → model``  (the edge agentic_workflow leaves
        to us because the agent detector doesn't know which model is being
        called)
  - ``model`` + ``vector_db`` + ``prompt`` co-located
      → ``model → retrieves → vector_db``  (RAG-shaped pattern)

The correlator is invoked with ``correlate(ctx, results)`` where ``results``
is the list of ``DetectorResult`` objects from the eight detectors.
"""
from __future__ import annotations

from collections import defaultdict

from detectors.base import RelEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.correlator"
detector_version = "0.1.0"


def correlate(ctx, results: list[DetectorResult]) -> DetectorResult:
    rels: list[RelEmission] = []

    assets_by_path: dict[str, list] = defaultdict(list)
    for r in results:
        for a in r.assets:
            if a.source_path:
                assets_by_path[a.source_path].append(a)

    for path in sorted(assets_by_path.keys()):
        assets = assets_by_path[path]
        types_in_file = {a.asset_type for a in assets}

        if "agent" in types_in_file and "mcp_server" in types_in_file:
            agent = next(a for a in assets if a.asset_type == "agent")
            mcp   = next(a for a in assets if a.asset_type == "mcp_server")
            rels.append(_edge(
                ctx, path, relationship_type="invokes",
                source_asset_ref=f"agent::{ctx.repo_asset_id}::{path}::{agent.name}",
                target_asset_ref=f"mcp_server::{ctx.repo_asset_id}::{path}::{mcp.name}",
                subject_name=f"{agent.name}→invokes→{mcp.name}",
                reasoning=["agent and mcp_server detected in same file"],
            ))

        if "agent" in types_in_file and "model" in types_in_file:
            agent = next(a for a in assets if a.asset_type == "agent")
            model = next(a for a in assets if a.asset_type == "model")
            rels.append(_edge(
                ctx, path, relationship_type="orchestrates",
                source_asset_ref=f"agent::{ctx.repo_asset_id}::{path}::{agent.name}",
                target_asset_ref=f"model::{ctx.repo_asset_id}::{path}::{model.name}",
                subject_name=f"{agent.name}→orchestrates→{model.name}",
                reasoning=["agent and model detected in same file"],
            ))

        if {"model", "vector_db", "prompt"}.issubset(types_in_file):
            model = next(a for a in assets if a.asset_type == "model")
            vdb   = next(a for a in assets if a.asset_type == "vector_db")
            rels.append(_edge(
                ctx, path, relationship_type="retrieves",
                source_asset_ref=f"model::{ctx.repo_asset_id}::{path}::{model.name}",
                target_asset_ref=f"vector_db::{ctx.repo_asset_id}::{path}::{vdb.name}",
                subject_name=f"{model.name}→retrieves→{vdb.name}",
                reasoning=["model, vector_db, prompt detected in same file"],
            ))

    return DetectorResult(assets=[], relationships=rels, findings=[])


def _edge(ctx, path: str, *, relationship_type: str,
           source_asset_ref: str, target_asset_ref: str,
           subject_name: str, reasoning: list[str]) -> RelEmission:
    packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_relationship", subject_type=relationship_type,
        subject_name=subject_name,
        source_events=[{
            "kind": "file", "repo": ctx.repo_full_name,
            "commit_sha": ctx.head_commit_sha,
            "path": path, "snippet_lines": [1, 1],
            "snippet": f"(co-located in {path})",
        }],
        reasoning_chain=reasoning,
        confidence="medium",
    )
    return RelEmission(
        tenant_id=ctx.tenant_id,
        source_asset_ref=source_asset_ref,
        target_asset_ref=target_asset_ref,
        relationship_type=relationship_type,
        attributes={},
        evidence_packet=packet,
        detector_id=detector_id,
        detector_version=detector_version,
    )
