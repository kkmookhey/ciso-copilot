"""Detect vector databases used in a repo.

Python SDKs: chromadb, pinecone, weaviate, qdrant_client, faiss, llama_index.
SQL: pgvector (``CREATE EXTENSION vector`` in any .sql file).

SP1 shape: emits ``ai_vector_db`` entities with bare-name natural keys
(``"chromadb"``, ``"pgvector"``, …) deduped across files/repos, plus a
``github_repo → retrieves → ai_vector_db`` edge per (repo, vector_db) pair.
"""
from __future__ import annotations

import re
from pathlib import Path

from detectors._walk import ripgrep
from detectors.base import EntityEmission, EdgeEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.vector_db"
detector_version = "0.2.0"

PY_VECTOR_DBS = [
    "chromadb",
    "pinecone",
    "weaviate",
    "qdrant_client",
    "faiss",
]


def detect(ctx) -> DetectorResult:
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission] = []
    repo_nk = f"github.com/{ctx.repo_full_name}"

    for vdb in PY_VECTOR_DBS:
        pattern = rf"^\s*(from|import)\s+{vdb}(\b|\.)"
        matches = ripgrep(ctx.repo_workdir, pattern, types=["py"])
        if not matches:
            continue
        matches.sort(key=lambda m: (str(m[0]), m[1]))
        _emit(ctx, vdb, matches, repo_nk, entities, edges)

    pg_matches = _ripgrep_sql_pgvector(ctx.repo_workdir)
    if pg_matches:
        _emit(ctx, "pgvector", pg_matches, repo_nk, entities, edges)

    return DetectorResult(entities=entities, edges=edges, findings=[])


def _emit(ctx, name: str, matches: list[tuple[Path, int, str]],
           repo_nk: str, entities: list, edges: list) -> None:
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
    entities.append(EntityEmission(
        tenant_id=ctx.tenant_id, kind="ai_vector_db",
        natural_key=name, display_name=name, domain="ai",
        attributes={"references_seen": len(matches)},
        evidence_packet=packet,
        detector_id=detector_id, detector_version=detector_version,
        connection_id=ctx.connection_id, source_path=rel_path,
    ))
    rel_packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_relationship", subject_type="retrieves",
        subject_name=f"repo→retrieves→{name}",
        source_events=[], reasoning_chain=["vector_db detected in repo"],
        confidence="high",
    )
    edges.append(EdgeEmission(
        tenant_id=ctx.tenant_id,
        source_kind="github_repo", source_natural_key=repo_nk,
        target_kind="ai_vector_db", target_natural_key=name,
        kind="retrieves", attributes={},
        evidence_packet=rel_packet,
        detector_id=detector_id, detector_version=detector_version,
    ))


def _ripgrep_sql_pgvector(workdir: Path) -> list[tuple[Path, int, str]]:
    """Find pgvector activation: CREATE EXTENSION vector — case-insensitive."""
    matches = ripgrep(workdir, r"CREATE\s+EXTENSION.*\bvector\b",
                       types=["sql"], ignore_case=True)
    matches.sort(key=lambda m: (str(m[0]), m[1]))
    return matches
