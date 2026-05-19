"""Detect vector databases used in a repo.

Python SDKs: chromadb, pinecone, weaviate, qdrant_client, faiss, llama_index.
SQL: pgvector (``CREATE EXTENSION vector`` in any .sql file).
"""
from __future__ import annotations

import re
from pathlib import Path

from detectors._walk import ripgrep
from detectors.base import AssetEmission, RelEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.vector_db"
detector_version = "0.1.0"

PY_VECTOR_DBS = [
    "chromadb",
    "pinecone",
    "weaviate",
    "qdrant_client",
    "faiss",
]


def detect(ctx) -> DetectorResult:
    assets: list[AssetEmission] = []
    rels:   list[RelEmission] = []

    repo_ref = f"repository::::{ctx.repo_asset_id}"

    for vdb in PY_VECTOR_DBS:
        pattern = rf"^\s*(from|import)\s+{vdb}(\b|\.)"
        matches = ripgrep(ctx.repo_workdir, pattern, types=["py"])
        if not matches:
            continue
        matches.sort(key=lambda m: (str(m[0]), m[1]))
        _emit(ctx, vdb, matches, repo_ref, assets, rels)

    pg_matches = _ripgrep_sql_pgvector(ctx.repo_workdir)
    if pg_matches:
        _emit(ctx, "pgvector", pg_matches, repo_ref, assets, rels)

    return DetectorResult(assets=assets, relationships=rels, findings=[])


def _emit(ctx, name: str, matches: list[tuple[Path, int, str]],
           repo_ref: str, assets: list, rels: list) -> None:
    first_path, first_line, first_snippet = matches[0]
    rel_path = str(first_path.relative_to(ctx.repo_workdir))
    packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_asset", subject_type="vector_db", subject_name=name,
        source_events=[{
            "kind": "file", "repo": ctx.repo_full_name,
            "commit_sha": ctx.head_commit_sha,
            "path": rel_path, "snippet_lines": [first_line, first_line],
            "snippet": first_snippet,
        }],
        reasoning_chain=[f"matched {name} reference on {rel_path}:{first_line}"],
        confidence="high",
    )
    assets.append(AssetEmission(
        tenant_id=ctx.tenant_id, connection_id=ctx.connection_id,
        asset_type="vector_db", name=name,
        source_repo_id=ctx.repo_asset_id, source_path=rel_path,
        attributes={"references_seen": len(matches)},
        evidence_packet=packet,
        detector_id=detector_id, detector_version=detector_version,
    ))
    rel_packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_relationship", subject_type="retrieves",
        subject_name=f"repo→retrieves→{name}",
        source_events=[], reasoning_chain=["vector_db detected in repo"],
        confidence="high",
    )
    rels.append(RelEmission(
        tenant_id=ctx.tenant_id,
        source_asset_ref=repo_ref,
        target_asset_ref=f"vector_db::{ctx.repo_asset_id}::{rel_path}::{name}",
        relationship_type="retrieves",
        attributes={},
        evidence_packet=rel_packet,
        detector_id=detector_id, detector_version=detector_version,
    ))


def _ripgrep_sql_pgvector(workdir: Path) -> list[tuple[Path, int, str]]:
    """Find pgvector activation: CREATE EXTENSION vector — case-insensitive."""
    matches = ripgrep(workdir, r"CREATE\s+EXTENSION.*\bvector\b",
                       types=["sql"], ignore_case=True)
    matches.sort(key=lambda m: (str(m[0]), m[1]))
    return matches
