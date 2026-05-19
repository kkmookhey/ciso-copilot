# platform/lambda/ai_scanner/detectors/framework.py
"""Detect AI-framework imports (langchain, llama_index, crewai, autogen,
semantic_kernel, dspy, langgraph)."""
from __future__ import annotations

from detectors._walk import ripgrep
from detectors.base import EntityEmission, EdgeEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.framework"
detector_version = "0.2.0"

FRAMEWORKS = [
    "langchain", "langgraph", "llama_index", "llama_cpp", "crewai",
    "autogen", "semantic_kernel", "dspy",
]


def detect(ctx) -> DetectorResult:
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission] = []
    repo_natural_key = f"github.com/{ctx.repo_full_name}"

    for fw in FRAMEWORKS:
        pattern = rf"^\s*(from|import)\s+{fw}(\b|\.)"
        matches = ripgrep(ctx.repo_workdir, pattern, types=["py"])
        if not matches:
            continue
        matches.sort(key=lambda m: (str(m[0]), m[1]))
        first_path, first_line, first_snippet = matches[0]
        rel_path = str(first_path.relative_to(ctx.repo_workdir))

        packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_asset", subject_type="ai_framework", subject_name=fw,
            source_events=[{
                "kind": "file", "repo": ctx.repo_full_name,
                "commit_sha": ctx.head_commit_sha,
                "path": rel_path, "snippet_lines": [first_line, first_line],
                "snippet": first_snippet,
            }],
            reasoning_chain=[f"matched {fw} import on {rel_path}:{first_line}"],
            confidence="high",
        )
        entities.append(EntityEmission(
            tenant_id=ctx.tenant_id, kind="ai_framework",
            natural_key=fw, display_name=fw, domain="ai",
            attributes={"imports_seen": len(matches)},
            evidence_packet=packet,
            detector_id=detector_id, detector_version=detector_version,
            connection_id=ctx.connection_id, source_path=rel_path,
        ))

        rel_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_relationship", subject_type="uses",
            subject_name=f"repo→uses→{fw}",
            source_events=[], reasoning_chain=["framework detected in repo"],
            confidence="high",
        )
        edges.append(EdgeEmission(
            tenant_id=ctx.tenant_id,
            source_kind="github_repo", source_natural_key=repo_natural_key,
            target_kind="ai_framework", target_natural_key=fw,
            kind="uses", attributes={}, evidence_packet=rel_packet,
            detector_id=detector_id, detector_version=detector_version,
        ))

    return DetectorResult(entities=entities, edges=edges, findings=[])
