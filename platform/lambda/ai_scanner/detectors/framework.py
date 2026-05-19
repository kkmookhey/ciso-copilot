"""Detect AI-framework imports (langchain, llama_index, crewai, autogen,
semantic_kernel, dspy, langgraph)."""
from __future__ import annotations

from pathlib import Path

from detectors._walk import ripgrep
from detectors.base import AssetEmission, RelEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.framework"
detector_version = "0.1.0"

FRAMEWORKS = [
    "langchain", "langgraph", "llama_index", "llama_cpp", "crewai",
    "autogen", "semantic_kernel", "dspy",
]


def detect(ctx) -> DetectorResult:
    assets: list[AssetEmission] = []
    rels:   list[RelEmission] = []

    repo_ref = f"repository::::{ctx.repo_asset_id}"

    for fw in FRAMEWORKS:
        pattern = rf"^\s*(from|import)\s+{fw}(\b|\.)"
        matches = ripgrep(ctx.repo_workdir, pattern, types=["py"])
        if not matches:
            continue

        first_path, first_line, first_snippet = matches[0]
        rel_path = str(first_path.relative_to(ctx.repo_workdir))

        packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_asset", subject_type="framework", subject_name=fw,
            source_events=[{
                "kind": "file",
                "repo": ctx.repo_full_name,
                "commit_sha": ctx.head_commit_sha,
                "path": rel_path,
                "snippet_lines": [first_line, first_line],
                "snippet": first_snippet,
            }],
            reasoning_chain=[f"matched {fw} import on {rel_path}:{first_line}"],
            confidence="high",
        )
        assets.append(AssetEmission(
            tenant_id=ctx.tenant_id,
            connection_id=ctx.connection_id,
            asset_type="framework",
            name=fw,
            source_repo_id=ctx.repo_asset_id,
            source_path=rel_path,
            attributes={"imports_seen": len(matches)},
            evidence_packet=packet,
            detector_id=detector_id,
            detector_version=detector_version,
        ))

        rel_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_relationship", subject_type="uses",
            subject_name=f"repo→uses→{fw}",
            source_events=[],
            reasoning_chain=["framework detected in repo"],
            confidence="high",
        )
        rels.append(RelEmission(
            tenant_id=ctx.tenant_id,
            source_asset_ref=repo_ref,
            target_asset_ref=f"framework::{ctx.repo_asset_id}::{rel_path}::{fw}",
            relationship_type="uses",
            attributes={},
            evidence_packet=rel_packet,
            detector_id=detector_id,
            detector_version=detector_version,
        ))

    return DetectorResult(assets=assets, relationships=rels, findings=[])
