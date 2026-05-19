"""Cross-detector correlator. Adds derived edges after the eight
deterministic detectors have all run.

Patterns:
  - ``ai_agent`` + ``ai_mcp_server`` co-located in the same file
      → ``ai_agent → invokes → ai_mcp_server``
  - ``ai_agent`` + ``ai_model`` co-located
      → ``ai_agent → orchestrates → ai_model``  (agentic_workflow can't emit
        this because it doesn't know which model the agent calls)
  - ``ai_model`` + ``ai_vector_db`` + ``ai_prompt`` co-located
      → ``ai_model → retrieves → ai_vector_db``  (RAG-shaped pattern)

Natural-key formulas the correlator reproduces (must stay in sync with the
upstream detectors; see spec §5):
  - ``ai_agent``      :  ``f"{repo_nk}::{path}::{agent.display_name}"``
  - ``ai_mcp_server`` :  ``f"{repo_nk}::{path}::{mcp.display_name}"``
  - ``ai_vector_db``  :  ``vdb.display_name``     (bare name, cross-repo dedup)
  - ``ai_model``      :  ``model.display_name``   (provider/model_id, cross-repo)

The correlator is invoked with ``correlate(ctx, results)`` where ``results``
is the list of ``DetectorResult`` objects from the eight detectors.
"""
from __future__ import annotations

from collections import defaultdict

from detectors.base import EdgeEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.correlator"
detector_version = "0.2.0"


def correlate(ctx, results: list[DetectorResult]) -> DetectorResult:
    edges: list[EdgeEmission] = []
    repo_nk = f"github.com/{ctx.repo_full_name}"

    entities_by_path: dict[str, list] = defaultdict(list)
    for r in results:
        for e in r.entities:
            if e.source_path:
                entities_by_path[e.source_path].append(e)

    for path in sorted(entities_by_path.keys()):
        ents = entities_by_path[path]
        kinds_in_file = {e.kind for e in ents}

        if "ai_agent" in kinds_in_file and "ai_mcp_server" in kinds_in_file:
            agent = next(e for e in ents if e.kind == "ai_agent")
            mcp   = next(e for e in ents if e.kind == "ai_mcp_server")
            edges.append(_edge(
                ctx, path, kind="invokes",
                source_kind="ai_agent",      source_nk=agent.natural_key,
                target_kind="ai_mcp_server", target_nk=mcp.natural_key,
                subject_name=f"{agent.display_name}→invokes→{mcp.display_name}",
                reasoning=["ai_agent and ai_mcp_server detected in same file"],
            ))

        if "ai_agent" in kinds_in_file and "ai_model" in kinds_in_file:
            agent = next(e for e in ents if e.kind == "ai_agent")
            model = next(e for e in ents if e.kind == "ai_model")
            edges.append(_edge(
                ctx, path, kind="orchestrates",
                source_kind="ai_agent", source_nk=agent.natural_key,
                target_kind="ai_model", target_nk=model.natural_key,
                subject_name=f"{agent.display_name}→orchestrates→{model.display_name}",
                reasoning=["ai_agent and ai_model detected in same file"],
            ))

        if {"ai_model", "ai_vector_db", "ai_prompt"}.issubset(kinds_in_file):
            model = next(e for e in ents if e.kind == "ai_model")
            vdb   = next(e for e in ents if e.kind == "ai_vector_db")
            edges.append(_edge(
                ctx, path, kind="retrieves",
                source_kind="ai_model",     source_nk=model.natural_key,
                target_kind="ai_vector_db", target_nk=vdb.natural_key,
                subject_name=f"{model.display_name}→retrieves→{vdb.display_name}",
                reasoning=["ai_model, ai_vector_db, ai_prompt detected in same file"],
            ))

    return DetectorResult(entities=[], edges=edges, findings=[])


def _edge(ctx, path: str, *, kind: str,
           source_kind: str, source_nk: str,
           target_kind: str, target_nk: str,
           subject_name: str, reasoning: list[str]) -> EdgeEmission:
    packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_relationship", subject_type=kind,
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
    return EdgeEmission(
        tenant_id=ctx.tenant_id,
        source_kind=source_kind, source_natural_key=source_nk,
        target_kind=target_kind, target_natural_key=target_nk,
        kind=kind,
        attributes={},
        evidence_packet=packet,
        detector_id=detector_id, detector_version=detector_version,
    )
